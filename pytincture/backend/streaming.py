"""Bounded BFF streaming with deterministic iterator cleanup."""

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterable, Callable, Iterable
from typing import Any

from fastapi.responses import StreamingResponse


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
    on_finish: Callable[[str, int], None] | None = None,
):
    started = time.monotonic()
    output_bytes = 0
    reason = "complete"
    iterator = iter(iterable)
    try:
        for item in iterator:
            if time.monotonic() - started > max_seconds:
                reason = "timeout"
                return
            serialized = serialize_stream_item(item, raw)
            output_bytes += _serialized_size(serialized)
            if output_bytes > max_bytes:
                reason = "byte-limit"
                return
            yield serialized
    except GeneratorExit:
        reason = "disconnect"
        raise
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
        if on_finish is not None:
            on_finish(reason, output_bytes)


async def limited_async_stream(
    iterable: AsyncIterable,
    *,
    raw: bool,
    max_seconds: float,
    max_bytes: int,
    on_finish: Callable[[str, int], None] | None = None,
):
    started = time.monotonic()
    output_bytes = 0
    reason = "complete"
    iterator = iterable.__aiter__()
    try:
        while True:
            remaining = max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                reason = "timeout"
                return
            try:
                item = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                reason = "timeout"
                return
            serialized = serialize_stream_item(item, raw)
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
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()
        if on_finish is not None:
            on_finish(reason, output_bytes)


def as_streaming_response(
    result: Any,
    *,
    raw: bool,
    media_type: str,
    max_seconds: float,
    max_bytes: int,
    on_finish: Callable[[str, int], None] | None = None,
) -> StreamingResponse:
    if isinstance(result, StreamingResponse):
        return result
    if inspect.isasyncgen(result) or hasattr(result, "__aiter__"):
        content = limited_async_stream(
            result,
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            on_finish=on_finish,
        )
    else:
        if isinstance(result, (str, bytes, bytearray, dict)):
            result = [result]
        elif not inspect.isgenerator(result) and not isinstance(result, Iterable):
            result = [result]
        content = limited_sync_stream(
            result,
            raw=raw,
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            on_finish=on_finish,
        )
    return StreamingResponse(content, media_type=media_type)
