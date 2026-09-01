#!/usr/bin/env python3
"""Generate the complete SPDX inventory for the vendored Pyodide runtime."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYODIDE = ROOT / "pytincture" / "frontend" / "pyodide" / "0.29.3"
FULL = PYODIDE / "full"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_id(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9.-]", "-", name)
    return f"SPDXRef-Package-PyodideCatalog-{safe}"


def build_sbom() -> dict:
    lock = json.loads((FULL / "pyodide-lock.json").read_text(encoding="utf-8"))
    wheel_name = "micropip-0.11.0-py3-none-any.whl"
    with zipfile.ZipFile(FULL / wheel_name) as wheel:
        packaging_source = wheel.read(
            "micropip/_vendored/packaging/src/packaging/__init__.py"
        ).decode("utf-8")
    packaging_version = re.search(
        r'^__version__\s*=\s*"([^"]+)"', packaging_source, re.MULTILINE
    ).group(1)

    files = [
        (wheel_name, "micropip-wheel"),
        (f"{wheel_name}.metadata", "micropip-metadata"),
        ("pyodide-lock.json", "pyodide-lock"),
        ("pyodide.asm.js", "pyodide-asm-js"),
        ("pyodide.asm.wasm", "pyodide-wasm"),
        ("pyodide.js", "pyodide-js"),
        ("python_stdlib.zip", "python-stdlib"),
    ]
    file_entries = [
        {
            "fileName": f"full/{name}",
            "SPDXID": f"SPDXRef-File-{identifier}",
            "checksums": [{"algorithm": "SHA256", "checksumValue": _digest(FULL / name)}],
        }
        for name, identifier in files
    ]
    packages = [
        {
            "name": "pyodide",
            "SPDXID": "SPDXRef-Package-Pyodide",
            "versionInfo": "0.29.3",
            "downloadLocation": "https://github.com/pyodide/pyodide/releases/tag/0.29.3",
            "filesAnalyzed": True,
            "licenseConcluded": "Apache-2.0",
            "hasFiles": [
                "SPDXRef-File-pyodide-lock",
                "SPDXRef-File-pyodide-js",
                "SPDXRef-File-pyodide-asm-js",
                "SPDXRef-File-pyodide-wasm",
            ],
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": "pkg:github/pyodide/pyodide@0.29.3",
            }],
        },
        {
            "name": "cpython",
            "SPDXID": "SPDXRef-Package-CPython",
            "versionInfo": "3.13.2",
            "downloadLocation": "https://www.python.org/downloads/release/python-3132/",
            "filesAnalyzed": True,
            "licenseConcluded": "Python-2.0",
            "hasFiles": ["SPDXRef-File-python-stdlib"],
        },
        {
            "name": "micropip",
            "SPDXID": "SPDXRef-Package-Micropip",
            "versionInfo": "0.11.0",
            "downloadLocation": "https://github.com/pyodide/micropip/releases/tag/0.11.0",
            "filesAnalyzed": True,
            "licenseConcluded": "MPL-2.0",
            "hasFiles": [
                "SPDXRef-File-micropip-wheel",
                "SPDXRef-File-micropip-metadata",
            ],
        },
        {
            "name": "emscripten",
            "SPDXID": "SPDXRef-Package-Emscripten",
            "versionInfo": "4.0.9",
            "downloadLocation": "https://github.com/emscripten-core/emscripten/releases/tag/4.0.9",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "comment": "Build-tool identity from the Pyodide lock; no Emscripten package is shipped.",
        },
        {
            "name": "packaging (embedded in micropip)",
            "SPDXID": "SPDXRef-Package-MicropipVendoredPackaging",
            "versionInfo": packaging_version,
            "downloadLocation": f"https://pypi.org/project/packaging/{packaging_version}/",
            "filesAnalyzed": False,
            "licenseConcluded": "Apache-2.0 OR BSD-2-Clause",
            "comment": "Source is embedded inside the shipped micropip wheel.",
        },
        {
            "name": "mousebender (embedded in micropip)",
            "SPDXID": "SPDXRef-Package-MicropipVendoredMousebender",
            "versionInfo": "NOASSERTION",
            "downloadLocation": "https://github.com/pypa/mousebender",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "comment": "Source is embedded inside the shipped micropip wheel; upstream does not declare a version.",
        },
    ]
    relationships = [
        {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-Package-Pyodide"},
        {"spdxElementId": "SPDXRef-Package-Pyodide", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-Package-CPython"},
        {"spdxElementId": "SPDXRef-Package-Pyodide", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-Package-Micropip"},
        {"spdxElementId": "SPDXRef-Package-Micropip", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-Package-MicropipVendoredPackaging"},
        {"spdxElementId": "SPDXRef-Package-Micropip", "relationshipType": "CONTAINS", "relatedSpdxElement": "SPDXRef-Package-MicropipVendoredMousebender"},
        {"spdxElementId": "SPDXRef-Package-Emscripten", "relationshipType": "BUILD_TOOL_OF", "relatedSpdxElement": "SPDXRef-Package-Pyodide"},
    ]
    for name, package in sorted(lock["packages"].items()):
        if name == "micropip":
            continue
        package_id = _catalog_id(name)
        packages.append({
            "name": f"pyodide-index:{name}",
            "SPDXID": package_id,
            "versionInfo": package["version"],
            "downloadLocation": f"https://cdn.jsdelivr.net/pyodide/v0.29.3/full/{package['file_name']}",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": package["sha256"]}],
            "comment": "Exact package catalog entry available to the embedded runtime; the package file is not vendored in Pytincture.",
        })
        relationships.append({
            "spdxElementId": "SPDXRef-Package-Pyodide",
            "relationshipType": "OTHER",
            "relatedSpdxElement": package_id,
            "comment": "AVAILABLE_PACKAGE: exact entry in the embedded Pyodide package catalog.",
        })

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "pytincture-vendored-pyodide-0.29.3",
        "documentNamespace": "https://pytincture.dev/sbom/pyodide/0.29.3",
        "creationInfo": {
            "created": "2026-08-31T00:00:00Z",
            "creators": ["Tool: scripts/generate_vendored_pyodide_sbom.py"],
        },
        "packages": packages,
        "files": file_entries,
        "relationships": relationships,
    }


def main() -> None:
    output = PYODIDE / "sbom.json"
    output.write_text(json.dumps(build_sbom(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
