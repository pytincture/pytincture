"""Bounded BFF streaming with deterministic iterator cleanup."""

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterable, Callable, Iterable
from typing import Any

from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool


def serialize_stream_item(item: Any, raw: bool = False) -> str | bytes:
    if isinstance(item, (bytes, bytearray)):
        data = bytes(item)
        return data if raw or data.endswith(b"\n") else data + b"\n"
    text = item if isinstance(item, str) else json.dumps(item)
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
            serialized = serialize_stream_item(item, raw)
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
            serialized = serialize_stream_item(item, raw)
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


def as_streaming_response(
    result: Any,
    *,
    raw: bool,
    media_type: str,
    max_seconds: float,
    max_bytes: int,
    max_items: int,
    idle_timeout_seconds: float,
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
    if inspect.isasyncgen(result) or hasattr(result, "__aiter__"):
        content = limited_async_stream(
            result,
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            max_items=max_items,
            idle_timeout_seconds=idle_timeout_seconds,
            on_finish=on_finish,
        )
    else:
        if isinstance(result, (str, bytes, bytearray, dict)):
            result = [result]
        elif not inspect.isgenerator(result) and not isinstance(result, Iterable):
            result = [result]
        content = limited_async_stream(
            iterate_in_threadpool(iter(result)),
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            max_items=max_items,
            idle_timeout_seconds=idle_timeout_seconds,
            on_finish=on_finish,
        )
    return StreamingResponse(
        content,
        status_code=status_code,
        headers=headers,
        media_type=media_type,
        background=background,
    )
