#!/usr/bin/env python3
"""Verify vendored Pyodide identity, file inventory, and SPDX checksums."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYODIDE = ROOT / "pytincture" / "frontend" / "pyodide" / "0.29.3"


def main() -> None:
    sbom = json.loads((PYODIDE / "sbom.json").read_text(encoding="utf-8"))
    if sbom.get("spdxVersion") != "SPDX-2.3":
        raise SystemExit("vendored Pyodide SBOM must use SPDX 2.3")
    packages = sbom.get("packages", [])
    if len(packages) != 1 or packages[0].get("versionInfo") != "0.29.3":
        raise SystemExit("vendored Pyodide SBOM version mismatch")
    lock = json.loads((PYODIDE / "full" / "pyodide-lock.json").read_text())
    if lock.get("info", {}).get("version") != "0.29.3":
        raise SystemExit("vendored Pyodide lock metadata version mismatch")

    expected = {}
    for entry in sbom.get("files", []):
        checksums = entry.get("checksums", [])
        sha256 = next(
            (item.get("checksumValue") for item in checksums if item.get("algorithm") == "SHA256"),
            None,
        )
        if not sha256:
            raise SystemExit(f"missing SHA-256 for {entry.get('fileName')}")
        expected[entry["fileName"]] = sha256
    actual_files = {
        path.relative_to(PYODIDE).as_posix()
        for path in (PYODIDE / "full").iterdir()
        if path.is_file()
    }
    if actual_files != set(expected):
        raise SystemExit("vendored Pyodide file inventory differs from SBOM")
    for name, digest in expected.items():
        actual = hashlib.sha256((PYODIDE / name).read_bytes()).hexdigest()
        if actual != digest:
            raise SystemExit(f"vendored Pyodide checksum mismatch: {name}")
    print("vendored Pyodide SBOM and checksums are valid")


if __name__ == "__main__":
    main()
