"""Safe request diagnostics shared by middleware and exception handlers."""

import json
import logging
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any


def request_correlation_id(header_value: str | None = None) -> str:
    """Use a caller-provided request ID or generate a process-local correlation ID."""
    if header_value and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", header_value):
        return header_value
    return uuid.uuid4().hex


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


def structured_log(
    target: logging.Logger,
    level: int,
    event: str,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Emit a stable JSON event suitable for local or centralized collectors."""
    payload = {"event": event}
    payload.update(
        {
            key: value
            for key, value in fields.items()
            if value is not None and isinstance(value, (str, int, float, bool))
        }
    )
    target.log(
        level,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        exc_info=exc_info,
    )


def readiness_report(
    modules_root: str,
    frontend_root: str,
    stores: Mapping[str, Any] | None = None,
) -> tuple[bool, dict[str, bool]]:
    """Check filesystem and optional shared-state dependencies without mutation."""
    checks = {
        "modules_path": os.path.isdir(modules_root)
        and os.access(modules_root, os.R_OK),
        "frontend_index": os.path.isfile(os.path.join(frontend_root, "index.html")),
        "frontend_runtime": os.path.isfile(
            os.path.join(frontend_root, "pytincture.js")
        ),
    }
    for name, store in (stores or {}).items():
        try:
            checks[name] = bool(store.ping())
        except Exception:
            checks[name] = False
    return all(checks.values()), checks
