"""Bounded stores for the optional BFF one-time request-proof feature."""

from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


class ReplayAdmissionRejected(RuntimeError):
    """Raised when a replay store cannot admit another bounded token batch."""


@runtime_checkable
class AtomicReplayStore(Protocol):
    """Contract for stores that atomically consume BFF request proofs.

    ``shared_across_workers`` is an explicit topology declaration. A custom
    provider may set it to true only when ``consume`` is atomic across every
    worker that accepts requests for the service.
    """

    shared_across_workers: bool

    def issue_batch(
        self,
        subject: str,
        records: Mapping[str, Mapping[str, Any]],
        ttl_seconds: int,
    ) -> None: ...

    def consume(self, key: str, default: Any = None) -> Any: ...


def validate_atomic_replay_store(store: Any) -> AtomicReplayStore:
    """Validate and return a configured replay store without naming a vendor."""

    if not callable(getattr(store, "issue_batch", None)):
        raise TypeError("BFF replay store must implement issue_batch()")
    if not callable(getattr(store, "consume", None)):
        raise TypeError("BFF replay store must implement atomic consume()")
    if not isinstance(getattr(store, "shared_across_workers", None), bool):
        raise TypeError(
            "BFF replay store must declare shared_across_workers as a boolean"
        )
    return store


class LocalReplayStore:
    """Thread-safe, expiration-indexed, bounded single-worker replay store."""

    shared_across_workers = False

    def __init__(
        self,
        max_entries: int,
        max_entries_per_subject: int,
        *,
        clock=time.time,
    ):
        if max_entries <= 0 or max_entries_per_subject <= 0:
            raise ValueError("replay-store limits must be greater than zero")
        if max_entries_per_subject > max_entries:
            raise ValueError("per-subject replay limit cannot exceed store capacity")
        self.max_entries = max_entries
        self.max_entries_per_subject = max_entries_per_subject
        self.clock = clock
        self._entries: dict[str, tuple[dict[str, Any], float, str]] = {}
        self._expirations: list[tuple[float, str]] = []
        self._subject_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _remove_locked(self, key: str):
        entry = self._entries.pop(key, None)
        if entry is None:
            return None
        record, _expires_at, subject = entry
        remaining = self._subject_counts.get(subject, 1) - 1
        if remaining > 0:
            self._subject_counts[subject] = remaining
        else:
            self._subject_counts.pop(subject, None)
        return record

    def _purge_expired_locked(self, now: float) -> None:
        while self._expirations and self._expirations[0][0] <= now:
            expires_at, key = heapq.heappop(self._expirations)
            current = self._entries.get(key)
            if current is not None and current[1] == expires_at:
                self._remove_locked(key)

    def _compact_expirations_locked(self) -> None:
        if len(self._expirations) <= self.max_entries * 2:
            return
        self._expirations = [
            (expires_at, key)
            for key, (_record, expires_at, _subject) in self._entries.items()
        ]
        heapq.heapify(self._expirations)

    def issue_batch(
        self,
        subject: str,
        records: Mapping[str, Mapping[str, Any]],
        ttl_seconds: int,
    ) -> None:
        if not subject or ttl_seconds <= 0:
            raise ValueError("replay batch subject and TTL are required")
        prepared = [(str(key), dict(value)) for key, value in records.items()]
        if not prepared:
            return
        if len({key for key, _value in prepared}) != len(prepared):
            raise ValueError("replay batch contains duplicate keys")
        now = self.clock()
        expires_at = now + ttl_seconds
        with self._lock:
            self._purge_expired_locked(now)
            self._compact_expirations_locked()
            if any(key in self._entries for key, _value in prepared):
                raise ValueError("replay batch collides with an existing key")
            if len(self._entries) + len(prepared) > self.max_entries:
                raise ReplayAdmissionRejected("worker replay-token capacity is full")
            if (
                self._subject_counts.get(subject, 0) + len(prepared)
                > self.max_entries_per_subject
            ):
                raise ReplayAdmissionRejected("session replay-token capacity is full")
            for key, record in prepared:
                self._entries[key] = (record, expires_at, subject)
                heapq.heappush(self._expirations, (expires_at, key))
            self._subject_counts[subject] = (
                self._subject_counts.get(subject, 0) + len(prepared)
            )
            self._compact_expirations_locked()

    def consume(self, key: str, default: Any = None) -> Any:
        now = self.clock()
        with self._lock:
            self._purge_expired_locked(now)
            value = self._remove_locked(key)
        return default if value is None else value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._expirations.clear()
            self._subject_counts.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired_locked(self.clock())
            return len(self._entries)

    @property
    def expiration_index_size(self) -> int:
        with self._lock:
            return len(self._expirations)


class SharedReplayStoreAdapter:
    """Adapt an atomic TTL mapping, including RedisDict, to the public contract."""

    shared_across_workers = True

    def __init__(self, store: Any):
        if not callable(getattr(store, "set_with_ttl", None)):
            raise TypeError("shared replay backend must implement set_with_ttl()")
        if not callable(getattr(store, "pop_atomic", None)):
            raise TypeError("shared replay backend must implement pop_atomic()")
        self.store = store

    def issue_batch(
        self,
        subject: str,
        records: Mapping[str, Mapping[str, Any]],
        ttl_seconds: int,
    ) -> None:
        for key, value in records.items():
            self.store.set_with_ttl(key, dict(value), ttl_seconds)

    def consume(self, key: str, default: Any = None) -> Any:
        return self.store.pop_atomic(key, default)

    def ping(self) -> bool:
        ping = getattr(self.store, "ping", None)
        return bool(ping()) if callable(ping) else False
