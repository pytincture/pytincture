#!/usr/bin/env python3
"""Verify vendored Pyodide identity, file inventory, and SPDX checksums."""

from __future__ import annotations

import hashlib
import json
from email.parser import BytesParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYODIDE = ROOT / "pytincture" / "frontend" / "pyodide" / "0.29.3"


def main() -> None:
    sbom = json.loads((PYODIDE / "sbom.json").read_text(encoding="utf-8"))
    if sbom.get("spdxVersion") != "SPDX-2.3":
        raise SystemExit("vendored Pyodide SBOM must use SPDX 2.3")
    packages = sbom.get("packages", [])
    package_by_name = {
        package.get("name"): package for package in packages if isinstance(package, dict)
    }
    expected_components = {
        "pyodide": ("0.29.3", "Apache-2.0"),
        "cpython": ("3.13.2", "Python-2.0"),
        "micropip": ("0.11.0", "MPL-2.0"),
        "emscripten": ("4.0.9", "NOASSERTION"),
    }
    if set(package_by_name) != set(expected_components):
        raise SystemExit("vendored Pyodide SBOM component inventory mismatch")
    for name, (version, license_name) in expected_components.items():
        package = package_by_name[name]
        if package.get("versionInfo") != version:
            raise SystemExit(f"vendored Pyodide SBOM {name} version mismatch")
        if package.get("licenseConcluded") != license_name:
            raise SystemExit(f"vendored Pyodide SBOM {name} license mismatch")
    lock = json.loads((PYODIDE / "full" / "pyodide-lock.json").read_text())
    lock_info = lock.get("info", {})
    if lock_info.get("version") != "0.29.3":
        raise SystemExit("vendored Pyodide lock metadata version mismatch")
    if lock_info.get("python") != "3.13.2":
        raise SystemExit("vendored Pyodide Python runtime version mismatch")
    if lock_info.get("platform") != "emscripten_4_0_9":
        raise SystemExit("vendored Pyodide Emscripten platform mismatch")
    locked_micropip = lock.get("packages", {}).get("micropip", {})
    if (
        locked_micropip.get("version") != "0.11.0"
        or locked_micropip.get("file_name") != "micropip-0.11.0-py3-none-any.whl"
    ):
        raise SystemExit("vendored micropip lock identity mismatch")

    metadata = BytesParser().parsebytes(
        (PYODIDE / "full" / "micropip-0.11.0-py3-none-any.whl.metadata").read_bytes()
    )
    if metadata.get("Name") != "micropip" or metadata.get("Version") != "0.11.0":
        raise SystemExit("vendored micropip metadata identity mismatch")

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

    file_ids = {
        entry.get("SPDXID")
        for entry in sbom.get("files", [])
        if isinstance(entry, dict)
    }
    owned_file_ids = {
        file_id
        for package in packages
        for file_id in package.get("hasFiles", [])
    }
    if owned_file_ids != file_ids:
        raise SystemExit("vendored Pyodide SBOM package/file ownership mismatch")

    required_relationships = {
        ("SPDXRef-Package-Pyodide", "CONTAINS", "SPDXRef-Package-CPython"),
        ("SPDXRef-Package-Pyodide", "CONTAINS", "SPDXRef-Package-Micropip"),
        ("SPDXRef-Package-Emscripten", "BUILD_TOOL_OF", "SPDXRef-Package-Pyodide"),
    }
    relationships = {
        (
            item.get("spdxElementId"),
            item.get("relationshipType"),
            item.get("relatedSpdxElement"),
        )
        for item in sbom.get("relationships", [])
        if isinstance(item, dict)
    }
    if not required_relationships.issubset(relationships):
        raise SystemExit("vendored Pyodide SBOM component relationships are incomplete")
    print("vendored Pyodide SBOM and checksums are valid")


if __name__ == "__main__":
    main()
