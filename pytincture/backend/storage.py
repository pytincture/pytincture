"""Shared state stores used by sessions and one-time BFF tokens."""

import json
from typing import Any

from pytincture.backend.limits import CircuitBreaker


class RedisDict:
    """A small mapping facade over Upstash Redis with a read-through cache."""

    def __init__(
        self,
        redis_url: str = "",
        redis_token: str = "",
        key_prefix: str = "",
        *,
        redis_client: Any = None,
        cache_reads: bool = True,
        timeout_seconds: float = 2.0,
        failure_threshold: int = 3,
        cooldown_seconds: float = 15.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("remote store timeout must be greater than zero")
        if redis_client is None:
            try:
                from upstash_redis import Redis
            except ImportError as exc:
                raise RuntimeError(
                    "Redis support requires optional dependencies; "
                    "install pytincture[redis]"
                ) from exc

            redis_client = Redis(
                url=redis_url,
                token=redis_token,
                rest_retries=0,
                rest_retry_interval=0,
            )
            # Upstash currently constructs an httpx client with no deadline.
            # Replace only that transport detail while keeping its public Redis
            # API, so every network operation has a hard connect/read/write cap.
            try:
                import httpx

                previous_client = redis_client._http._client
                redis_client._http._client = httpx.Client(timeout=timeout_seconds)
                previous_client.close()
            except (AttributeError, ImportError):
                # Custom/future clients can supply their own transport deadline.
                pass
        self._redis = redis_client
        self._prefix = key_prefix
        self._cache_reads = cache_reads
        self._cache: dict[str, Any] = {}
        self._circuit = CircuitBreaker(failure_threshold, cooldown_seconds)

    def _call(self, method: str, *args, **kwargs):
        self._circuit.before_call()
        try:
            result = getattr(self._redis, method)(*args, **kwargs)
        except Exception:
            self._circuit.failure()
            raise
        self._circuit.success()
        return result

    @staticmethod
    def _decode(value):
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return json.loads(value)
        return value

    def __getitem__(self, key):
        if self._cache_reads and key in self._cache:
            return self._cache[key]
        value = self._call("get", self._prefix + key)
        if not value:
            if self._cache_reads:
                self._cache[key] = None
            return None
        value = self._decode(value)
        if self._cache_reads:
            self._cache[key] = value
        return value

    def __setitem__(self, key, value):
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._call("set", self._prefix + key, serialized)
        if self._cache_reads:
            self._cache[key] = value

    def set_with_ttl(self, key, value, ttl_seconds: int):
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._call("set", self._prefix + key, serialized, ex=ttl_seconds)
        if self._cache_reads:
            self._cache[key] = value

    def __delitem__(self, key):
        if self._call("delete", self._prefix + key) == 0:
            raise KeyError(key)
        self._cache.pop(key, None)

    def pop_atomic(self, key, default=None):
        value = self._call("getdel", self._prefix + key)
        self._cache.pop(key, None)
        return default if value is None else self._decode(value)

    def __contains__(self, key):
        if key is None:
            return False
        if self._cache_reads and key in self._cache:
            return self._cache[key] is not None
        exists = self._call("exists", self._prefix + key) == 1
        if not exists and self._cache_reads:
            self._cache[key] = None
        return exists

    def __len__(self):
        return sum(value is not None for value in self._cache.values())

    def __iter__(self):
        cursor = "0"
        while True:
            cursor, keys = self._call(
                "scan",
                cursor=cursor,
                match=self._prefix + "*",
                count=100,
            )
            for key in keys:
                yield key[len(self._prefix) :]
            if cursor == "0":
                break

    def keys(self):
        return self.__iter__()

    def items(self):
        for key in self:
            yield key, self[key]

    def values(self):
        for key in self:
            yield self[key]

    def get(self, key, default=None):
        value = self.__getitem__(key)
        return default if value is None else value

    def ping(self) -> bool:
        """Check the configured Redis dependency without changing stored data."""
        ping = getattr(self._redis, "ping", None)
        if not callable(ping):
            return False
        self._circuit.before_call()
        try:
            result = ping()
        except Exception:
            self._circuit.failure()
            raise
        self._circuit.success()
        return result is True or result == "PONG"
