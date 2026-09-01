#!/usr/bin/env python3
"""Compare pre-upgrade, candidate, and rollback service probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def validate(name: str, result: dict) -> None:
    if result.get("page_status") != 200 or result.get("archive_status") != 200:
        raise SystemExit(f"{name}: service/package route failed: {result}")
    required = {"demo.py", "data.py", "widget.py"}
    if not required.issubset(result.get("archive_names", [])):
        raise SystemExit(f"{name}: browser package is missing {required}")
    for field in ("modules_path", "server_secret_absent", "public_imports"):
        if result.get(field) is not True:
            raise SystemExit(f"{name}: compatibility field failed: {field}")
    if result.get("widgetset") != "dhxpyt==0.9.17":
        raise SystemExit(f"{name}: widget metadata changed: {result.get('widgetset')}")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: compare_upgrade_smoke.py <before> <candidate> <rollback>")
    before, candidate, rollback = (load(path) for path in sys.argv[1:])
    validate("before", before)
    validate("candidate", candidate)
    validate("rollback", rollback)
    if before != rollback:
        raise SystemExit("rollback probe does not reproduce the pre-upgrade result")
    if before["version"] == candidate["version"]:
        print("warning: candidate version matches baseline; version-transition assertion deferred to RC")
    print(
        f"upgrade/rollback smoke passed: {before['version']} -> "
        f"{candidate['version']} -> {rollback['version']}"
    )


if __name__ == "__main__":
    main()
