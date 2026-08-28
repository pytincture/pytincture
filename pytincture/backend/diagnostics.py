"""Safe request diagnostics shared by middleware and exception handlers."""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any


def request_correlation_id(header_value: str | None = None) -> str:
    """Use a caller-provided request ID or generate a process-local correlation ID."""
    return header_value or uuid.uuid4().hex


def internal_error_payload(correlation_id: str) -> dict[str, str]:
    """Return the stable public payload for internal failures."""
    return {
        "detail": "Internal server error",
        "correlation_id": correlation_id,
    }


def sanitized_validation_errors(
    errors: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop submitted values and exception objects from validation responses."""
    allowed = {"loc", "msg", "type"}
    return [
        {key: value for key, value in error.items() if key in allowed}
        for error in errors
    ]
