#!/usr/bin/env python3
"""Validate Pytincture release contents, versions, dependencies, and hashes."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

from versioning import npm_version_for_python


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "release-artifacts-v1.json"


def _fail(message: str) -> None:
    raise SystemExit(f"release artifact validation failed: {message}")


def _source_versions() -> tuple[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project_version = project["project"]["version"]
    init_text = (ROOT / "pytincture" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)', init_text, re.MULTILINE)
    if not match:
        _fail("pytincture.__version__ is missing")
    runtime_text = (ROOT / "pytincture" / "frontend" / "pytincture.js").read_text()
    runtime_match = re.search(
        r'^const PYTINCTURE_RUNTIME_VERSION = ["\']([^"\']+)',
        runtime_text,
        re.MULTILINE,
    )
    if not runtime_match or runtime_match.group(1) != project_version:
        _fail("pytincture.js runtime version does not match pyproject.toml")
    return project_version, match.group(1)


def _normalized_tar_names(path: Path) -> tuple[set[str], tarfile.TarFile]:
    archive = tarfile.open(path, "r:gz")
    members = [member for member in archive.getmembers() if member.name]
    raw_name_list = [member.name.rstrip("/") for member in members]
    if len(raw_name_list) != len(set(raw_name_list)):
        archive.close()
        _fail(f"{path.name} contains duplicate archive members")
    raw_names = set(raw_name_list)
    roots = {name.split("/", 1)[0] for name in raw_names}
    if len(roots) != 1:
        archive.close()
        _fail(f"{path.name} must contain exactly one root directory")
    root = next(iter(roots))
    names = {
        member.name[len(root) + 1 :]
        for member in members
        if member.isfile() and member.name.startswith(root + "/")
    }
    return names, archive


SENSITIVE_PARTS = {
    ".env",
    ".git",
    ".github",
    "__pycache__",
    "id_rsa",
    "id_ed25519",
    "node_modules",
    "tests",
}
SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".sqlite", ".sqlite3")


def _normalize_inventory_name(name: str, version: str) -> str:
    name = re.sub(
        rf"^pytincture-{re.escape(version)}\.dist-info/",
        "{dist-info}/",
        name,
    )
    return name.replace(
        f"pytincture/frontend/integrity/pytincture-{version}.json",
        "pytincture/frontend/integrity/{framework-version}.json",
    ).replace(
        f"integrity/pytincture-{version}.json",
        "integrity/{framework-version}.json",
    )


def _check_contents(artifact: str, names: set[str], expected: list[str], version: str) -> str:
    normalized = {_normalize_inventory_name(name, version) for name in names}
    expected_set = set(expected)
    missing = sorted(expected_set - normalized)
    unexpected = sorted(normalized - expected_set)
    sensitive = sorted(
        name
        for name in normalized
        if any(part.lower() in SENSITIVE_PARTS for part in Path(name).parts)
        or name.lower().endswith(SENSITIVE_SUFFIXES)
    )
    if missing:
        _fail(f"{artifact} is missing: {', '.join(missing)}")
    if unexpected:
        _fail(f"{artifact} contains unexpected files: {', '.join(unexpected[:10])}")
    if sensitive:
        _fail(f"{artifact} contains sensitive files: {', '.join(sensitive[:10])}")
    return hashlib.sha256(
        ("\n".join(sorted(normalized)) + "\n").encode("utf-8")
    ).hexdigest()


def _requirement_name(value: str) -> str:
    return re.split(r"[ (<>=!~;\[]", value, maxsplit=1)[0].lower().replace("_", "-")


def inspect_wheel(path: Path, contract: dict, version: str) -> str:
    with zipfile.ZipFile(path) as archive:
        raw_names = archive.namelist()
        if len(raw_names) != len(set(raw_names)):
            _fail("wheel contains duplicate archive members")
        names = set(raw_names)
        inventory_hash = _check_contents(
            "wheel", names, contract["wheel_inventory"], version
        )
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            _fail("wheel is missing its MIT license file")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            _fail("wheel must contain exactly one METADATA file")
        metadata = email.parser.Parser().parsestr(archive.read(metadata_names[0]).decode())
        if metadata["Version"] != version:
            _fail(f"wheel version {metadata['Version']} does not match {version}")
        requirements = metadata.get_all("Requires-Dist", [])
        base = sorted(
            _requirement_name(item)
            for item in requirements
            if "extra ==" not in item and "extra == " not in item
        )
        if base != sorted(contract["base_dependencies"]):
            _fail(f"wheel base dependencies differ: {base}")
        extras = sorted(set(metadata.get_all("Provides-Extra", [])))
        if extras != sorted(contract["extras"]):
            _fail(f"wheel extras differ: {extras}")
        for runtime_path in (
            "pytincture/frontend/pytincture.js",
            "pytincture/frontend/dist/pytincture.js",
            "pytincture/frontend/dist/pytincture.esm.js",
            "pytincture/frontend/dist/pytincture.min.js",
        ):
            if version.encode() not in archive.read(runtime_path):
                _fail(f"wheel runtime {runtime_path} does not embed version {version}")
        return inventory_hash


def inspect_sdist(path: Path, contract: dict, version: str) -> str:
    names, archive = _normalized_tar_names(path)
    try:
        inventory_hash = _check_contents(
            "sdist", names, contract["sdist_inventory"], version
        )
        pkg_info = next((name for name in names if name == "PKG-INFO"), None)
        if pkg_info is None:
            _fail("sdist has no PKG-INFO")
        root = archive.getmembers()[0].name.split("/", 1)[0]
        member = archive.extractfile(f"{root}/PKG-INFO")
        metadata = email.parser.Parser().parsestr(member.read().decode() if member else "")
        if metadata["Version"] != version:
            _fail(f"sdist version {metadata['Version']} does not match {version}")
        return inventory_hash
    finally:
        archive.close()


def inspect_npm(path: Path, contract: dict, version: str) -> str:
    names, archive = _normalized_tar_names(path)
    try:
        inventory_hash = _check_contents(
            "npm package", names, contract["inventory"], version
        )
        member = archive.extractfile("package/package.json")
        package = json.loads(member.read() if member else b"{}")
        npm_version = npm_version_for_python(version)
        if package.get("version") != npm_version:
            _fail(f"npm version {package.get('version')} does not match {npm_version}")
        for runtime_path in (
            "package/dist/pytincture.js",
            "package/dist/pytincture.esm.js",
            "package/dist/pytincture.min.js",
        ):
            member = archive.extractfile(runtime_path)
            if member is None or version.encode() not in member.read():
                _fail(f"npm runtime {runtime_path} does not embed version {version}")
        return inventory_hash
    finally:
        archive.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--npm", type=Path, required=True)
    args = parser.parse_args()

    contract = json.loads(CONTRACT.read_text())
    project_version, runtime_version = _source_versions()
    npm_version = npm_version_for_python(project_version)
    package = json.loads((ROOT / "pytincture" / "frontend" / "package.json").read_text())
    lock = json.loads((ROOT / "pytincture" / "frontend" / "package-lock.json").read_text())
    source_versions = {
        "pyproject": project_version,
        "python": runtime_version,
        "npm": package.get("version"),
        "npm_lock": lock.get("version"),
        "npm_lock_root": lock.get("packages", {}).get("", {}).get("version"),
    }
    if source_versions != {
        "pyproject": project_version,
        "python": project_version,
        "npm": npm_version,
        "npm_lock": npm_version,
        "npm_lock_root": npm_version,
    }:
        _fail(f"source versions differ: {source_versions}")

    inventory_hashes = {
        "wheel": inspect_wheel(args.wheel, contract["python"], project_version),
        "sdist": inspect_sdist(args.sdist, contract["python"], project_version),
        "npm": inspect_npm(args.npm, contract["npm"], project_version),
    }
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (args.wheel, args.sdist, args.npm)
    }
    print(
        json.dumps(
            {
                "python_version": project_version,
                "npm_version": npm_version,
                "sha256": hashes,
                "inventory_sha256": inventory_hashes,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
