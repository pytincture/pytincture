"""Optional killable process boundary for deployment-trusted BFF modules."""

from __future__ import annotations

import asyncio
import inspect
import math
import multiprocessing
import sys
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from pytincture.backend.results import BFFResultLimitExceeded, encode_bff_result
from pytincture.backend.source_loading import load_source_module
from pytincture.dataclass import verify_bff_runtime_export


class IsolatedExecutionRejected(RuntimeError):
    """Raised when bounded process or per-user capacity is exhausted."""


class IsolatedExecutionTimeout(RuntimeError):
    """Raised after a child is terminated at its wall-time boundary."""


class IsolatedExecutionFailed(RuntimeError):
    """Raised when isolated application code fails without leaking details."""


@dataclass(frozen=True, slots=True)
class IsolatedBFFInvocation:
    module_path: str
    modules_root: str
    class_name: str
    member_name: str
    source_digest: str
    operation: dict[str, Any]
    user: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    subject: str
    wall_time_seconds: float


def _apply_process_limits(cpu_seconds: float, memory_bytes: int) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows has no resource module
        return
    cpu_limit = max(1, math.ceil(cpu_seconds))
    _cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    if cpu_hard != resource.RLIM_INFINITY:
        cpu_limit = min(cpu_limit, cpu_hard)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_hard))
    address_space = getattr(resource, "RLIMIT_AS", None)
    if address_space is not None and sys.platform != "darwin":
        _memory_soft, memory_hard = resource.getrlimit(address_space)
        if memory_hard != resource.RLIM_INFINITY:
            memory_bytes = min(memory_bytes, memory_hard)
        resource.setrlimit(address_space, (memory_bytes, memory_hard))


async def _resolve_result(value: Any) -> Any:
    if inspect.isawaitable(value):
        value = await value
    if inspect.isgenerator(value) or inspect.isasyncgen(value) or hasattr(
        value, "__aiter__"
    ):
        raise TypeError("streaming BFF operations are not supported in isolated mode")
    return value


def _isolated_worker(
    connection: Connection,
    invocation: IsolatedBFFInvocation,
    *,
    cpu_seconds: float,
    memory_bytes: int,
    result_max_bytes: int,
    result_max_depth: int,
    result_max_items: int,
) -> None:
    try:
        _apply_process_limits(cpu_seconds, memory_bytes)
        module = load_source_module(
            invocation.module_path,
            invocation.class_name,
            invocation.modules_root,
            expected_digest=invocation.source_digest or None,
        )
        cls = vars(module).get(invocation.class_name)
        verify_bff_runtime_export(
            cls,
            class_name=invocation.class_name,
            member_name=invocation.member_name,
            operation=invocation.operation,
            source_path=invocation.module_path,
        )
        instance = cls(_user=invocation.user)
        member = getattr(instance, invocation.member_name)
        value = (
            member(*invocation.args, **invocation.kwargs)
            if callable(member)
            else member
        )
        value = asyncio.run(_resolve_result(value))
        payload = encode_bff_result(
            value,
            max_bytes=result_max_bytes,
            max_depth=result_max_depth,
            max_items=result_max_items,
        )
        connection.send(("ok", payload))
    except BFFResultLimitExceeded:
        connection.send(("result-limit", b""))
    except BaseException:  # noqa: BLE001 - child failures cross only as a safe tag
        try:
            connection.send(("failed", b""))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


class ProcessIsolatedBFFExecutor:
    """Spawn one resource-limited child per call and terminate it on timeout."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        max_per_user: int,
        cpu_seconds: float,
        memory_bytes: int,
        result_max_bytes: int,
        result_max_depth: int,
        result_max_items: int,
        start_method: str = "spawn",
    ):
        if min(
            max_concurrency,
            max_per_user,
            cpu_seconds,
            memory_bytes,
            result_max_bytes,
            result_max_depth,
            result_max_items,
        ) <= 0:
            raise ValueError("isolated BFF limits must be greater than zero")
        self.max_concurrency = max_concurrency
        self.max_per_user = max_per_user
        self.cpu_seconds = cpu_seconds
        self.memory_bytes = memory_bytes
        self.result_max_bytes = result_max_bytes
        self.result_max_depth = result_max_depth
        self.result_max_items = result_max_items
        self._context = multiprocessing.get_context(start_method)
        self._capacity = threading.BoundedSemaphore(max_concurrency)
        self._user_counts: dict[str, int] = {}
        self._user_lock = threading.Lock()

    def _acquire(self, subject: str) -> None:
        if not self._capacity.acquire(blocking=False):
            raise IsolatedExecutionRejected("isolated BFF process capacity is full")
        with self._user_lock:
            current = self._user_counts.get(subject, 0)
            if current >= self.max_per_user:
                self._capacity.release()
                raise IsolatedExecutionRejected("isolated BFF per-user capacity is full")
            self._user_counts[subject] = current + 1

    def _release(self, subject: str) -> None:
        with self._user_lock:
            remaining = self._user_counts.get(subject, 1) - 1
            if remaining > 0:
                self._user_counts[subject] = remaining
            else:
                self._user_counts.pop(subject, None)
        self._capacity.release()

    def execute(self, invocation: IsolatedBFFInvocation) -> bytes:
        self._acquire(invocation.subject)
        receiving, sending = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_isolated_worker,
            args=(sending, invocation),
            kwargs={
                "cpu_seconds": self.cpu_seconds,
                "memory_bytes": self.memory_bytes,
                "result_max_bytes": self.result_max_bytes,
                "result_max_depth": self.result_max_depth,
                "result_max_items": self.result_max_items,
            },
            daemon=True,
        )
        deadline = time.monotonic() + invocation.wall_time_seconds
        try:
            process.start()
            sending.close()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IsolatedExecutionTimeout("isolated BFF call timed out")
                if receiving.poll(min(0.05, remaining)):
                    status, payload = receiving.recv()
                    process.join(timeout=0.1)
                    if status == "ok":
                        return payload
                    if status == "result-limit":
                        raise BFFResultLimitExceeded("BFF result limit exceeded")
                    raise IsolatedExecutionFailed("isolated BFF call failed")
                if not process.is_alive():
                    raise IsolatedExecutionFailed("isolated BFF process exited")
        finally:
            receiving.close()
            sending.close()
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
            self._release(invocation.subject)

    @property
    def active_users(self) -> dict[str, int]:
        with self._user_lock:
            return dict(self._user_counts)
