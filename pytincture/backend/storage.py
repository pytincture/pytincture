"""Shared state stores used by sessions and one-time BFF tokens."""

import json
from typing import Any


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
    ):
        if redis_client is None:
            try:
                from upstash_redis import Redis
            except ImportError as exc:
                raise RuntimeError(
                    "Redis support requires optional dependencies; "
                    "install pytincture[redis]"
                ) from exc

            redis_client = Redis(url=redis_url, token=redis_token)
        self._redis = redis_client
        self._prefix = key_prefix
        self._cache_reads = cache_reads
        self._cache: dict[str, Any] = {}

    @staticmethod
    def _decode(value):
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return json.loads(value)
        return value

    def __getitem__(self, key):
        if self._cache_reads and key in self._cache:
            return self._cache[key]
        value = self._redis.get(self._prefix + key)
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
        self._redis.set(self._prefix + key, serialized)
        if self._cache_reads:
            self._cache[key] = value

    def set_with_ttl(self, key, value, ttl_seconds: int):
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._redis.set(self._prefix + key, serialized, ex=ttl_seconds)
        if self._cache_reads:
            self._cache[key] = value

    def __delitem__(self, key):
        if self._redis.delete(self._prefix + key) == 0:
            raise KeyError(key)
        self._cache.pop(key, None)

    def pop_atomic(self, key, default=None):
        value = self._redis.getdel(self._prefix + key)
        self._cache.pop(key, None)
        return default if value is None else self._decode(value)

    def __contains__(self, key):
        if key is None:
            return False
        if self._cache_reads and key in self._cache:
            return self._cache[key] is not None
        exists = self._redis.exists(self._prefix + key) == 1
        if not exists and self._cache_reads:
            self._cache[key] = None
        return exists

    def __len__(self):
        return sum(value is not None for value in self._cache.values())

    def __iter__(self):
        cursor = "0"
        while True:
            cursor, keys = self._redis.scan(
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
        result = ping()
        return result is True or result == "PONG"
