"""Bounded, disposable per-worker resource controls."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator


class AdmissionRejected(RuntimeError):
    """Raised when a bounded worker queue cannot admit more work."""


class AsyncAdmissionGate:
    """Bound active work and waiters without storing user or session state."""

    def __init__(self, concurrency: int, max_waiters: int, wait_seconds: float):
        if concurrency <= 0 or max_waiters < 0 or wait_seconds <= 0:
            raise ValueError("invalid admission limits")
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_waiters = max_waiters
        self._wait_seconds = wait_seconds
        self._waiters = 0
        self._waiters_lock = threading.Lock()

    async def acquire(self) -> None:
        with self._waiters_lock:
            if self._semaphore.locked() and self._waiters >= self._max_waiters:
                raise AdmissionRejected("admission queue is full")
            self._waiters += 1
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._wait_seconds
            )
        except asyncio.TimeoutError as exc:
            raise AdmissionRejected("admission wait timed out") from exc
        finally:
            with self._waiters_lock:
                self._waiters -= 1

    def release(self) -> None:
        self._semaphore.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()


class CircuitOpen(RuntimeError):
    """Raised while a failing optional remote dependency is cooling down."""


class CircuitBreaker:
    """Small thread-safe failure circuit with no durable application state."""

    def __init__(self, failure_threshold: int, cooldown_seconds: float, *, clock=time.monotonic):
        if failure_threshold <= 0 or cooldown_seconds <= 0:
            raise ValueError("circuit-breaker limits must be greater than zero")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_progress = False
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                if self._probe_in_progress:
                    raise CircuitOpen("remote store circuit probe is in progress")
                return
            if self.clock() - self._opened_at >= self.cooldown_seconds:
                self._opened_at = None
                self._probe_in_progress = True
                return
            raise CircuitOpen("remote store circuit is open")

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_progress = False

    def failure(self) -> None:
        with self._lock:
            self._probe_in_progress = False
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self.clock()
