#!/usr/bin/env python3
"""Verify the vendored Pyodide inventory, SPDX SBOM, and upstream release bytes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYODIDE = ROOT / "pytincture" / "frontend" / "pyodide" / "0.29.3"
FULL = PYODIDE / "full"
sys.path.insert(0, str(ROOT / "scripts"))
from generate_vendored_pyodide_sbom import build_sbom  # noqa: E402


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_local() -> None:
    sbom = json.loads((PYODIDE / "sbom.json").read_text(encoding="utf-8"))
    if sbom != build_sbom():
        raise SystemExit(
            "vendored Pyodide SBOM is stale; run scripts/generate_vendored_pyodide_sbom.py"
        )
    if sbom.get("spdxVersion") != "SPDX-2.3":
        raise SystemExit("vendored Pyodide SBOM must use SPDX 2.3")

    lock = json.loads((FULL / "pyodide-lock.json").read_text(encoding="utf-8"))
    lock_info = lock.get("info", {})
    if lock_info != {
        "abi_version": "2025_0",
        "arch": "wasm32",
        "platform": "emscripten_4_0_9",
        "python": "3.13.2",
        "version": "0.29.3",
    }:
        raise SystemExit("vendored Pyodide lock runtime identity mismatch")
    locked_micropip = lock.get("packages", {}).get("micropip", {})
    wheel_name = "micropip-0.11.0-py3-none-any.whl"
    if (
        locked_micropip.get("version") != "0.11.0"
        or locked_micropip.get("file_name") != wheel_name
        or locked_micropip.get("sha256") != _sha256((FULL / wheel_name).read_bytes())
    ):
        raise SystemExit("vendored micropip lock identity or checksum mismatch")

    sidecar = (FULL / f"{wheel_name}.metadata").read_bytes()
    metadata = BytesParser().parsebytes(sidecar)
    if metadata.get("Name") != "micropip" or metadata.get("Version") != "0.11.0":
        raise SystemExit("vendored micropip metadata identity mismatch")
    with zipfile.ZipFile(FULL / wheel_name) as wheel:
        wheel_metadata_names = [
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(wheel_metadata_names) != 1 or wheel.read(wheel_metadata_names[0]) != sidecar:
            raise SystemExit("vendored micropip sidecar differs from wheel METADATA")

    expected_files = {}
    for entry in sbom.get("files", []):
        checksum = next(
            (
                item.get("checksumValue")
                for item in entry.get("checksums", [])
                if item.get("algorithm") == "SHA256"
            ),
            None,
        )
        if not checksum:
            raise SystemExit(f"missing SHA-256 for {entry.get('fileName')}")
        expected_files[entry["fileName"]] = checksum
    actual_files = {
        path.relative_to(PYODIDE).as_posix() for path in FULL.iterdir() if path.is_file()
    }
    if actual_files != set(expected_files):
        raise SystemExit("vendored Pyodide file inventory differs from SBOM")
    for name, expected in expected_files.items():
        if _sha256((PYODIDE / name).read_bytes()) != expected:
            raise SystemExit(f"vendored Pyodide checksum mismatch: {name}")

    catalog = {
        package["name"].removeprefix("pyodide-index:"): package
        for package in sbom["packages"]
        if package.get("name", "").startswith("pyodide-index:")
    }
    expected_catalog_names = set(lock["packages"]) - {"micropip"}
    if set(catalog) != expected_catalog_names:
        missing = sorted(expected_catalog_names - set(catalog))
        unexpected = sorted(set(catalog) - expected_catalog_names)
        raise SystemExit(
            f"vendored Pyodide SBOM package catalog mismatch; missing={missing[:5]}, unexpected={unexpected[:5]}"
        )
    for name, package in catalog.items():
        locked = lock["packages"][name]
        digest = next(
            item["checksumValue"]
            for item in package["checksums"]
            if item["algorithm"] == "SHA256"
        )
        if package["versionInfo"] != locked["version"] or digest != locked["sha256"]:
            raise SystemExit(f"vendored Pyodide SBOM catalog mismatch: {name}")


def _read_upstream(path: Path | None, manifest: dict) -> bytes:
    expected_size = manifest["source"]["size"]
    if path is not None:
        content = path.read_bytes()
    else:
        request = urllib.request.Request(
            manifest["source"]["url"],
            headers={"User-Agent": "pytincture-release-verifier/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(expected_size + 1)
    if len(content) != expected_size:
        raise SystemExit("official Pyodide core archive size mismatch")
    if _sha256(content) != manifest["source"]["sha256"]:
        raise SystemExit("official Pyodide core archive checksum mismatch")
    return content


def _validate_upstream(path: Path | None = None) -> None:
    manifest = json.loads(
        (ROOT / "security" / "pyodide-upstream.json").read_text(encoding="utf-8")
    )
    content = _read_upstream(path, manifest)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:bz2") as archive:
        for name in manifest["exact_core_files"]:
            member = archive.extractfile(f"pyodide/{name}")
            if member is None or member.read() != (FULL / name).read_bytes():
                raise SystemExit(f"vendored Pyodide file differs from official release: {name}")
        upstream_lock_member = archive.extractfile("pyodide/pyodide-lock.json")
        if upstream_lock_member is None:
            raise SystemExit("official Pyodide archive is missing pyodide-lock.json")
        upstream_lock = json.loads(upstream_lock_member.read())

    vendored_lock = json.loads((FULL / "pyodide-lock.json").read_text(encoding="utf-8"))
    corrected_lock = copy.deepcopy(upstream_lock)
    correction = manifest["lock_metadata_corrections"]["info.version"]
    if upstream_lock["info"]["version"] != correction["upstream_archive"]:
        raise SystemExit("official Pyodide lock no longer has the documented metadata value")
    corrected_lock["info"]["version"] = correction["vendored"]
    if corrected_lock != vendored_lock:
        raise SystemExit("vendored Pyodide lock differs from official release beyond correction")
    micropip = upstream_lock["packages"]["micropip"]
    if _sha256((FULL / micropip["file_name"]).read_bytes()) != micropip["sha256"]:
        raise SystemExit("vendored micropip wheel differs from official Pyodide lock")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-upstream", action="store_true")
    parser.add_argument("--upstream-archive", type=Path)
    args = parser.parse_args()
    _validate_local()
    if args.verify_upstream or args.upstream_archive:
        _validate_upstream(args.upstream_archive)
    print("vendored Pyodide SBOM, package catalog, and checksums are valid")


if __name__ == "__main__":
    main()
