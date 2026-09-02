"""Bounded BFF streaming with deterministic iterator cleanup."""

import asyncio
import inspect
import time
from collections.abc import AsyncIterable, Callable, Iterable
from typing import Any

from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from pytincture.backend.results import BFFResultLimitExceeded, encode_bff_result


_DEFERRED_SYNC_STREAM_CLEANUPS: set[asyncio.Task] = set()


class _StreamSendTimeout(Exception):
    """A stream send exceeded its write-idle or absolute deadline."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _BoundedStreamingResponse(StreamingResponse):
    def __init__(
        self,
        *args,
        max_seconds: float,
        write_timeout_seconds: float,
        on_send_timeout: Callable[[str, int], None],
        close_unstarted_source: Callable[[], Any],
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_seconds = max_seconds
        self.write_timeout_seconds = write_timeout_seconds
        self.on_send_timeout = on_send_timeout
        self.close_unstarted_source = close_unstarted_source

    async def _send_frame(
        self,
        send,
        message: dict[str, Any],
        absolute_deadline: float,
    ) -> None:
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0:
            raise _StreamSendTimeout("timeout")
        send_timeout = min(self.write_timeout_seconds, remaining)
        try:
            await asyncio.wait_for(
                send(message),
                timeout=send_timeout,
            )
        except asyncio.TimeoutError as exc:
            reason = (
                "timeout"
                if remaining <= self.write_timeout_seconds
                else "write-timeout"
            )
            raise _StreamSendTimeout(reason) from exc

    async def stream_response(self, send) -> None:
        absolute_deadline = time.monotonic() + self.max_seconds
        sent_bytes = 0
        body_iterator_started = False
        try:
            await self._send_frame(
                send,
                {
                    "type": "http.response.start",
                    "status": self.status_code,
                    "headers": self.raw_headers,
                },
                absolute_deadline,
            )
            async for chunk in self.body_iterator:
                body_iterator_started = True
                if not isinstance(chunk, bytes | memoryview):
                    chunk = chunk.encode(self.charset)
                await self._send_frame(
                    send,
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    },
                    absolute_deadline,
                )
                sent_bytes += len(chunk)

            await self._send_frame(
                send,
                {"type": "http.response.body", "body": b"", "more_body": False},
                absolute_deadline,
            )
        except _StreamSendTimeout as exc:
            # Release scarce admission before asking trusted application code
            # to clean up its iterator. The shared completion callback is
            # idempotent, so the iterator's own finally block cannot release
            # the slot or log completion twice.
            self.on_send_timeout(exc.reason, sent_bytes)
            if body_iterator_started:
                close = getattr(self.body_iterator, "aclose", None)
                if callable(close):
                    await close()
            else:
                await self.close_unstarted_source()


def _next_sync_item(iterator):
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


async def _finish_sync_stream(
    iterator,
    pending_next: asyncio.Task | None,
    reason: str,
    output_bytes: int,
    on_finish: Callable[[str, int], None] | None,
) -> None:
    if pending_next is not None:
        try:
            await pending_next
        except BaseException:
            # The response has already ended. Retrieve the worker outcome so it
            # cannot become an unhandled task exception, then close the source.
            pass
    try:
        close = getattr(iterator, "close", None)
        if callable(close):
            await run_in_threadpool(close)
    finally:
        if on_finish is not None:
            on_finish(reason, output_bytes)


def _defer_sync_stream_finish(
    iterator,
    pending_next: asyncio.Task | None,
    reason: str,
    output_bytes: int,
    on_finish: Callable[[str, int], None] | None,
) -> None:
    cleanup = asyncio.create_task(
        _finish_sync_stream(
            iterator,
            pending_next,
            reason,
            output_bytes,
            on_finish,
        )
    )
    # Keep a strong reference until the abandoned worker and iterator close
    # have both completed. The BFF admission callback runs only at that point.
    _DEFERRED_SYNC_STREAM_CLEANUPS.add(cleanup)
    cleanup.add_done_callback(_DEFERRED_SYNC_STREAM_CLEANUPS.discard)


def serialize_stream_item(
    item: Any,
    raw: bool = False,
    *,
    max_bytes: int | None = None,
    max_depth: int = 32,
    max_items: int = 10_000,
) -> str | bytes:
    if isinstance(item, (bytes, bytearray)):
        data = bytes(item)
        if max_bytes is not None and len(data) > max_bytes:
            raise BFFResultLimitExceeded("BFF stream byte limit exceeded")
        return data if raw or data.endswith(b"\n") else data + b"\n"
    if isinstance(item, str):
        if max_bytes is not None and len(item) > max_bytes:
            raise BFFResultLimitExceeded("BFF stream byte limit exceeded")
        text = item
    else:
        text = encode_bff_result(
            item,
            max_bytes=max_bytes if max_bytes is not None else 2**63 - 1,
            max_depth=max_depth,
            max_items=max_items,
            compact=False,
        ).decode("utf-8")
    return text if raw or text.endswith("\n") else text + "\n"


def _serialized_size(value: str | bytes) -> int:
    return len(value.encode("utf-8") if isinstance(value, str) else value)


def limited_sync_stream(
    iterable: Iterable,
    *,
    raw: bool,
    max_seconds: float,
    max_bytes: int,
    max_items: int = 10_000,
    on_finish: Callable[[str, int], None] | None = None,
):
    started = time.monotonic()
    output_bytes = 0
    output_items = 0
    reason = "complete"
    iterator = iter(iterable)
    try:
        for item in iterator:
            if time.monotonic() - started > max_seconds:
                reason = "timeout"
                return
            remaining_bytes = max_bytes - output_bytes
            try:
                serialized = serialize_stream_item(
                    item,
                    raw,
                    max_bytes=remaining_bytes,
                    max_items=max_items,
                )
            except BFFResultLimitExceeded:
                reason = "byte-limit"
                return
            output_items += 1
            if output_items > max_items:
                reason = "item-limit"
                return
            output_bytes += _serialized_size(serialized)
            if output_bytes > max_bytes:
                reason = "byte-limit"
                return
            yield serialized
    except GeneratorExit:
        reason = "disconnect"
        raise
    finally:
        try:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        finally:
            if on_finish is not None:
                on_finish(reason, output_bytes)


async def limited_async_stream(
    iterable: AsyncIterable,
    *,
    raw: bool,
    max_seconds: float,
    max_bytes: int,
    max_items: int = 10_000,
    idle_timeout_seconds: float = 30.0,
    on_finish: Callable[[str, int], None] | None = None,
):
    started = time.monotonic()
    output_bytes = 0
    output_items = 0
    reason = "complete"
    iterator = iterable.__aiter__()
    try:
        while True:
            remaining = max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                reason = "timeout"
                return
            wait_seconds = min(remaining, idle_timeout_seconds)
            try:
                item = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=wait_seconds,
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                reason = (
                    "idle-timeout"
                    if idle_timeout_seconds < remaining
                    else "timeout"
                )
                return
            remaining_bytes = max_bytes - output_bytes
            try:
                serialized = serialize_stream_item(
                    item,
                    raw,
                    max_bytes=remaining_bytes,
                    max_items=max_items,
                )
            except BFFResultLimitExceeded:
                reason = "byte-limit"
                return
            output_items += 1
            if output_items > max_items:
                reason = "item-limit"
                return
            output_bytes += _serialized_size(serialized)
            if output_bytes > max_bytes:
                reason = "byte-limit"
                return
            yield serialized
    except asyncio.CancelledError:
        reason = "disconnect"
        raise
    except GeneratorExit:
        reason = "disconnect"
        raise
    finally:
        try:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()
        finally:
            if on_finish is not None:
                on_finish(reason, output_bytes)


async def limited_thread_stream(
    iterable: Iterable,
    *,
    raw: bool,
    max_seconds: float,
    max_bytes: int,
    max_items: int = 10_000,
    idle_timeout_seconds: float = 30.0,
    on_finish: Callable[[str, int], None] | None = None,
):
    """Iterate a legacy synchronous stream without losing worker accounting.

    Python threads cannot be killed safely. If ``next()`` outlives a response
    timeout or disconnect, cleanup and ``on_finish`` are deferred until that
    exact worker exits. The application-level BFF slot therefore remains held
    and bounds the number of abandoned synchronous workers.
    """

    started = time.monotonic()
    output_bytes = 0
    output_items = 0
    reason = "complete"
    iterator = iter(iterable)
    pending_next: asyncio.Task | None = None
    deferred_finish = False
    try:
        while True:
            remaining = max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                reason = "timeout"
                return
            wait_seconds = min(remaining, idle_timeout_seconds)
            pending_next = asyncio.create_task(
                run_in_threadpool(_next_sync_item, iterator)
            )
            try:
                has_item, item = await asyncio.wait_for(
                    asyncio.shield(pending_next),
                    timeout=wait_seconds,
                )
                pending_next = None
            except asyncio.TimeoutError:
                reason = (
                    "idle-timeout"
                    if idle_timeout_seconds < remaining
                    else "timeout"
                )
                _defer_sync_stream_finish(
                    iterator,
                    pending_next,
                    reason,
                    output_bytes,
                    on_finish,
                )
                deferred_finish = True
                return
            if not has_item:
                return
            remaining_bytes = max_bytes - output_bytes
            try:
                serialized = serialize_stream_item(
                    item,
                    raw,
                    max_bytes=remaining_bytes,
                    max_items=max_items,
                )
            except BFFResultLimitExceeded:
                reason = "byte-limit"
                return
            output_items += 1
            if output_items > max_items:
                reason = "item-limit"
                return
            output_bytes += _serialized_size(serialized)
            if output_bytes > max_bytes:
                reason = "byte-limit"
                return
            yield serialized
    except (asyncio.CancelledError, GeneratorExit):
        reason = "disconnect"
        _defer_sync_stream_finish(
            iterator,
            pending_next,
            reason,
            output_bytes,
            on_finish,
        )
        deferred_finish = True
        raise
    finally:
        if not deferred_finish:
            await _finish_sync_stream(
                iterator,
                pending_next,
                reason,
                output_bytes,
                on_finish,
            )


def as_streaming_response(
    result: Any,
    *,
    raw: bool,
    media_type: str,
    max_seconds: float,
    max_bytes: int,
    max_items: int,
    idle_timeout_seconds: float,
    write_timeout_seconds: float = 30.0,
    on_finish: Callable[[str, int], None] | None = None,
) -> StreamingResponse:
    status_code = 200
    headers = None
    background = None
    if isinstance(result, StreamingResponse):
        status_code = result.status_code
        headers = dict(result.headers)
        headers.pop("content-length", None)
        media_type = result.media_type or media_type
        background = result.background
        result = result.body_iterator
        raw = True

    source_to_close = result

    finished = False

    def finish_once(reason: str, output_bytes: int) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        if on_finish is not None:
            on_finish(reason, output_bytes)

    if inspect.isasyncgen(result) or hasattr(result, "__aiter__"):
        content = limited_async_stream(
            result,
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            max_items=max_items,
            idle_timeout_seconds=idle_timeout_seconds,
            on_finish=finish_once,
        )
    else:
        if isinstance(result, (str, bytes, bytearray, dict)):
            result = [result]
        elif not inspect.isgenerator(result) and not isinstance(result, Iterable):
            result = [result]
        source_to_close = iter(result)
        content = limited_thread_stream(
            source_to_close,
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            max_items=max_items,
            idle_timeout_seconds=idle_timeout_seconds,
            on_finish=finish_once,
        )

    async def close_unstarted_source() -> None:
        async_close = getattr(source_to_close, "aclose", None)
        if callable(async_close):
            await async_close()
            return
        sync_close = getattr(source_to_close, "close", None)
        if callable(sync_close):
            await run_in_threadpool(sync_close)

    return _BoundedStreamingResponse(
        content,
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
        max_seconds=max_seconds,
        write_timeout_seconds=write_timeout_seconds,
        on_send_timeout=lambda reason, sent_bytes: finish_once(
            reason,
            sent_bytes,
        ),
        close_unstarted_source=close_unstarted_source,
    )
