"""Bounded JSON encoding for ordinary and streamed BFF results."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi.encoders import jsonable_encoder


class BFFResultLimitExceeded(ValueError):
    """Raised before an oversized or excessively complex result is retained."""


def _validate_result_shape(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
) -> None:
    stack = [(value, 0)]
    item_count = 0
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            raise BFFResultLimitExceeded("BFF result nesting limit exceeded")
        if isinstance(current, str):
            # Every Unicode code point requires at least one UTF-8 byte. This
            # rejects an obviously oversized string before JSON escaping makes
            # a second large copy.
            if len(current) > max_bytes:
                raise BFFResultLimitExceeded("BFF result byte limit exceeded")
            continue
        if isinstance(current, Mapping):
            item_count += len(current)
            if item_count > max_items:
                raise BFFResultLimitExceeded("BFF result item limit exceeded")
            stack.extend((key, depth + 1) for key in current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            item_count += len(current)
            if item_count > max_items:
                raise BFFResultLimitExceeded("BFF result item limit exceeded")
            stack.extend((item, depth + 1) for item in current)


def encode_bff_result(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int = 32,
    max_items: int = 10_000,
    compact: bool = True,
) -> bytes:
    """Encode one FastAPI-compatible JSON value without exceeding limits."""

    if min(max_bytes, max_depth, max_items) <= 0:
        raise ValueError("BFF result limits must be greater than zero")
    converted = jsonable_encoder(value)
    _validate_result_shape(
        converted,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_items=max_items,
    )
    encoder_options = {"ensure_ascii": False, "allow_nan": False}
    if compact:
        encoder_options["separators"] = (",", ":")
    encoder = json.JSONEncoder(**encoder_options)
    output = bytearray()
    for chunk in encoder.iterencode(converted):
        encoded = chunk.encode("utf-8")
        if len(output) + len(encoded) > max_bytes:
            raise BFFResultLimitExceeded("BFF result byte limit exceeded")
        output.extend(encoded)
    return bytes(output)
