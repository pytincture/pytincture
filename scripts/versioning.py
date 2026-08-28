#!/usr/bin/env python3
"""Map the canonical PEP 440 framework version to npm SemVer."""

from __future__ import annotations

import re
import sys


_VERSION = re.compile(
    r"^(?P<base>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>0|[1-9]\d*))?"
    r"(?:\.dev(?P<dev_number>0|[1-9]\d*))?$"
)


def npm_version_for_python(version: str) -> str:
    match = _VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"unsupported Pytincture release version: {version}")
    base = ".".join(match.group(name) for name in ("base", "minor", "patch"))
    if match.group("pre"):
        label = {"a": "alpha", "b": "beta", "rc": "rc"}[match.group("pre")]
        mapped = f"{base}-{label}.{match.group('pre_number')}"
        if match.group("dev_number"):
            mapped += f".dev.{match.group('dev_number')}"
        return mapped
    if match.group("dev_number"):
        return f"{base}-dev.{match.group('dev_number')}"
    return base


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: versioning.py <python-version>")
    try:
        print(npm_version_for_python(sys.argv[1]))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
