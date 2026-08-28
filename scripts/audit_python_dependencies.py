#!/usr/bin/env python3
"""Audit installed dependencies, allowing only explicitly tracked advisories."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "security" / "pip-audit-allowlist.json"


def main() -> None:
    record = json.loads(ALLOWLIST.read_text())
    advisories = record.get("advisories", [])
    if len(advisories) != len(set(advisories)):
        raise SystemExit("pip-audit allowlist contains duplicate advisory IDs")
    issue = record.get("tracking_issue", "")
    if advisories and not issue.startswith("https://github.com/pytincture/pytincture/issues/"):
        raise SystemExit("non-empty pip-audit allowlist requires a Pytincture tracking issue")

    command = [sys.executable, "-m", "pip_audit", "--local", "--progress-spinner", "off"]
    for advisory in advisories:
        command.extend(("--ignore-vuln", advisory))
    subprocess.run(command, check=True)
    if advisories:
        print(f"Temporarily ignored {len(advisories)} advisories tracked by {issue}")


if __name__ == "__main__":
    main()
