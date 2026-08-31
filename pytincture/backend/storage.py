"""Optional shared stores used by revocation and one-time BFF features."""

import ipaddress
import json
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

from pytincture.backend.limits import CircuitBreaker


def validate_redis_url(redis_url: str) -> str:
    """Require TLS except for literal loopback development endpoints."""

    normalized = str(redis_url).strip()
    try:
        parsed = urlparse(normalized)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("redis_url must be an absolute HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("redis_url must be an absolute HTTP(S) URL")
    if parsed.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError(
                "redis_url must use HTTPS unless it targets a literal loopback IP"
            )
    return normalized


class RedisDict:
    """A small mapping facade over Upstash Redis with an optional bounded cache."""

    def __init__(
        self,
        redis_url: str = "",
        redis_token: str = "",
        key_prefix: str = "",
        *,
        redis_client: Any = None,
        cache_reads: bool = False,
        cache_max_entries: int = 256,
        cache_ttl_seconds: float = 1.0,
        timeout_seconds: float = 2.0,
        failure_threshold: int = 3,
        cooldown_seconds: float = 15.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("remote store timeout must be greater than zero")
        if cache_max_entries <= 0 or cache_ttl_seconds <= 0:
            raise ValueError("remote store cache limits must be greater than zero")
        if redis_client is None:
            redis_url = validate_redis_url(redis_url)
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
        self._cache_max_entries = cache_max_entries
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._circuit = CircuitBreaker(failure_threshold, cooldown_seconds)

    def _cache_get(self, key: str) -> tuple[bool, Any]:
        if not self._cache_reads:
            return False, None
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return False, None
            expires_at, value = cached
            if expires_at <= now:
                self._cache.pop(key, None)
                return False, None
            self._cache.move_to_end(key)
            return True, value

    def _cache_put(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        if not self._cache_reads or value is None:
            return
        ttl = self._cache_ttl_seconds
        if ttl_seconds is not None:
            ttl = min(ttl, float(ttl_seconds))
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)

    def _cache_delete(self, key: str) -> None:
        with self._cache_lock:
            self._cache.pop(key, None)

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
        found, cached = self._cache_get(key)
        if found:
            return cached
        value = self._call("get", self._prefix + key)
        if not value:
            return None
        value = self._decode(value)
        self._cache_put(key, value)
        return value

    def __setitem__(self, key, value):
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._call("set", self._prefix + key, serialized)
        self._cache_delete(key)
        self._cache_put(key, value)

    def set_with_ttl(self, key, value, ttl_seconds: int):
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._call("set", self._prefix + key, serialized, ex=ttl_seconds)
        self._cache_delete(key)
        self._cache_put(key, value, ttl_seconds)

    def __delitem__(self, key):
        deleted = self._call("delete", self._prefix + key)
        self._cache_delete(key)
        if deleted == 0:
            raise KeyError(key)

    def pop_atomic(self, key, default=None):
        value = self._call("getdel", self._prefix + key)
        self._cache_delete(key)
        return default if value is None else self._decode(value)

    def __contains__(self, key):
        if key is None:
            return False
        found, _cached = self._cache_get(key)
        if found:
            return True
        exists = self._call("exists", self._prefix + key) == 1
        return exists

    def __len__(self):
        now = time.monotonic()
        with self._cache_lock:
            expired = [
                key for key, (expires_at, _value) in self._cache.items()
                if expires_at <= now
            ]
            for key in expired:
                self._cache.pop(key, None)
            return len(self._cache)

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
