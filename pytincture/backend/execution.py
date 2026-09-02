"""Optional killable process boundary for deployment-trusted BFF modules."""

from __future__ import annotations

import asyncio
import inspect
import json
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
    """Raised when bounded process or per-identity capacity is exhausted."""


class IsolatedExecutionTimeout(RuntimeError):
    """Raised after a child is terminated at its wall-time boundary."""


class IsolatedExecutionFailed(RuntimeError):
    """Raised when isolated application code fails without leaking details."""


_IPC_MAGIC = b"PTB1"
_IPC_STATUS_OK = b"O"
_IPC_STATUS_RESULT_LIMIT = b"L"
_IPC_STATUS_FAILED = b"F"
_IPC_HEADER_BYTES = len(_IPC_MAGIC) + 1


def _isolated_message(status: bytes, payload: bytes = b"") -> bytes:
    if status not in {
        _IPC_STATUS_OK,
        _IPC_STATUS_RESULT_LIMIT,
        _IPC_STATUS_FAILED,
    }:
        raise ValueError("invalid isolated BFF message status")
    if status != _IPC_STATUS_OK and payload:
        raise ValueError("isolated BFF error messages cannot include a payload")
    return _IPC_MAGIC + status + payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not permitted: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _decode_isolated_message(
    message: bytes,
    *,
    result_max_bytes: int,
    result_max_depth: int,
    result_max_items: int,
) -> tuple[bytes, bytes]:
    if len(message) < _IPC_HEADER_BYTES or not message.startswith(_IPC_MAGIC):
        raise IsolatedExecutionFailed("invalid isolated BFF response")
    status = message[len(_IPC_MAGIC) : _IPC_HEADER_BYTES]
    payload = message[_IPC_HEADER_BYTES:]
    if status == _IPC_STATUS_OK:
        if not payload or len(payload) > result_max_bytes:
            raise IsolatedExecutionFailed("invalid isolated BFF response")
        try:
            decoded = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            canonical = encode_bff_result(
                decoded,
                max_bytes=result_max_bytes,
                max_depth=result_max_depth,
                max_items=result_max_items,
            )
        except (BFFResultLimitExceeded, UnicodeDecodeError, ValueError) as exc:
            raise IsolatedExecutionFailed("invalid isolated BFF response") from exc
        if canonical != payload:
            raise IsolatedExecutionFailed("invalid isolated BFF response")
        return status, payload
    if status in {_IPC_STATUS_RESULT_LIMIT, _IPC_STATUS_FAILED} and not payload:
        return status, payload
    raise IsolatedExecutionFailed("invalid isolated BFF response")


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
        connection.send_bytes(_isolated_message(_IPC_STATUS_OK, payload))
    except BFFResultLimitExceeded:
        connection.send_bytes(_isolated_message(_IPC_STATUS_RESULT_LIMIT))
    except BaseException:  # noqa: BLE001 - child failures cross only as a safe tag
        try:
            connection.send_bytes(_isolated_message(_IPC_STATUS_FAILED))
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
                raise IsolatedExecutionRejected("isolated BFF per-identity capacity is full")
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
                    try:
                        message = receiving.recv_bytes(
                            self.result_max_bytes + _IPC_HEADER_BYTES
                        )
                    except (EOFError, OSError) as exc:
                        raise IsolatedExecutionFailed(
                            "invalid isolated BFF response"
                        ) from exc
                    status, payload = _decode_isolated_message(
                        message,
                        result_max_bytes=self.result_max_bytes,
                        result_max_depth=self.result_max_depth,
                        result_max_items=self.result_max_items,
                    )
                    process.join(timeout=0.1)
                    if status == _IPC_STATUS_OK:
                        return payload
                    if status == _IPC_STATUS_RESULT_LIMIT:
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
