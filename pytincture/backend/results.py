"""Bounded JSON encoding for ordinary and streamed BFF results."""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from collections.abc import AsyncIterable, Iterator, Mapping, Sequence, Set
from enum import Enum
from pathlib import PurePath
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from pytincture.backend.limit_diagnostics import LimitDetail, LimitViolation


class BFFResultLimitExceeded(ValueError):
    """Raised before an oversized or excessively complex result is retained."""

    def __init__(self, message: str | LimitViolation):
        self.violation = message if isinstance(message, LimitViolation) else None
        super().__init__(str(message))

    @property
    def detail(self):
        return LimitDetail(self.violation) if self.violation is not None else str(self)

    @classmethod
    def limit(cls, setting: str, limit: int, observed: int, stage: str):
        return cls(LimitViolation(setting, limit, observed, stage))


@dataclasses.dataclass(slots=True)
class _ResultBudget:
    max_bytes: int
    max_depth: int
    max_items: int
    items: int = 0

    def add_items(self, count: int) -> None:
        self.items += count
        if self.items > self.max_items:
            raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_ITEMS", self.max_items, self.items, "materialization")


def _close_iterator(iterator: Iterator[Any]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup must not replace the deterministic limit/error response.
            pass


def _materialize_bounded_result(
    value: Any,
    *,
    budget: _ResultBudget,
    depth: int = 0,
    active: set[int] | None = None,
) -> Any:
    """Materialize JSON containers and iterators before FastAPI converts them."""

    if depth > budget.max_depth:
        raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_DEPTH", budget.max_depth, depth, "materialization")
    if isinstance(value, AsyncIterable):
        raise BFFResultLimitExceeded(
            "Async BFF result iterables require an explicit streaming export"
        )
    if isinstance(value, str):
        if len(value) > budget.max_bytes:
            raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_BYTES", budget.max_bytes, len(value), "materialization")
        return value
    if isinstance(value, (bytes, bytearray)):
        if len(value) > budget.max_bytes:
            raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_BYTES", budget.max_bytes, len(value), "materialization")
        return value
    if value is None or isinstance(value, (bool, int, float, Enum, PurePath)):
        return value

    active = active if active is not None else set()
    identity = id(value)
    if identity in active:
        raise BFFResultLimitExceeded("BFF result contains a circular reference")

    if isinstance(value, BaseModel):
        active.add(identity)
        try:
            return _materialize_bounded_result(
                value.model_dump(mode="python"),
                budget=budget,
                depth=depth,
                active=active,
            )
        finally:
            active.remove(identity)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        active.add(identity)
        try:
            fields = dataclasses.fields(value)
            budget.add_items(len(fields))
            return {
                field.name: _materialize_bounded_result(
                    getattr(value, field.name),
                    budget=budget,
                    depth=depth + 1,
                    active=active,
                )
                for field in fields
            }
        finally:
            active.remove(identity)

    if isinstance(value, Mapping):
        budget.add_items(len(value))
        active.add(identity)
        try:
            return {
                _materialize_bounded_result(
                    key,
                    budget=budget,
                    depth=depth + 1,
                    active=active,
                ): _materialize_bounded_result(
                    item,
                    budget=budget,
                    depth=depth + 1,
                    active=active,
                )
                for key, item in value.items()
            }
        finally:
            active.remove(identity)

    if isinstance(value, (Sequence, Set, deque)):
        budget.add_items(len(value))
        active.add(identity)
        try:
            return [
                _materialize_bounded_result(
                    item,
                    budget=budget,
                    depth=depth + 1,
                    active=active,
                )
                for item in value
            ]
        finally:
            active.remove(identity)

    if isinstance(value, Iterator):
        active.add(identity)
        result = []
        try:
            while True:
                try:
                    item = next(value)
                except StopIteration:
                    break
                budget.add_items(1)
                result.append(
                    _materialize_bounded_result(
                        item,
                        budget=budget,
                        depth=depth + 1,
                        active=active,
                    )
                )
        except BaseException:
            _close_iterator(value)
            raise
        finally:
            active.remove(identity)
        return result

    return value


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
            raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_DEPTH", max_depth, depth, "JSON conversion")
        if isinstance(current, str):
            # Every Unicode code point requires at least one UTF-8 byte. This
            # rejects an obviously oversized string before JSON escaping makes
            # a second large copy.
            if len(current) > max_bytes:
                raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_BYTES", max_bytes, len(current), "JSON conversion")
            continue
        if isinstance(current, Mapping):
            item_count += len(current)
            if item_count > max_items:
                raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_ITEMS", max_items, item_count, "JSON conversion")
            stack.extend((key, depth + 1) for key in current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            item_count += len(current)
            if item_count > max_items:
                raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_ITEMS", max_items, item_count, "JSON conversion")
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

    _converted, payload = prepare_bff_result(
        value,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_items=max_items,
        compact=compact,
    )
    return payload


def prepare_bff_result(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int = 32,
    max_items: int = 10_000,
    compact: bool = True,
) -> tuple[Any, bytes]:
    """Return a bounded JSON-compatible value and its canonical wire bytes."""

    if min(max_bytes, max_depth, max_items) <= 0:
        raise ValueError("BFF result limits must be greater than zero")
    materialized = _materialize_bounded_result(
        value,
        budget=_ResultBudget(
            max_bytes=max_bytes,
            max_depth=max_depth,
            max_items=max_items,
        ),
    )
    converted = jsonable_encoder(materialized)
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
            raise BFFResultLimitExceeded.limit("BFF_RESULT_MAX_BYTES", max_bytes, len(output) + len(encoded), "JSON encoding")
        output.extend(encoded)
    return converted, bytes(output)
