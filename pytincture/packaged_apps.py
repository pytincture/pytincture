"""Explicit, deployment-owned application discovery from installed wheels.

This module never downloads, installs, or automatically activates packages.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re

from packaging.specifiers import SpecifierSet

from .backend.safe_paths import validate_application_name

MANIFEST = "pytincture-app.json"


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("App paths must be relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("App paths must stay inside the package")
    return path


def _file(root, name):
    relative = _relative(name)
    path = root.joinpath(*relative.parts)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Packaged app paths must not contain symlinks")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing packaged app file: {name}")
    return path


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_app_manifest(modules_path, application, *, requires_pytincture=">=1.0.2,<2",
                       bff_manifest=None):
    """Inventory a dedicated app resource directory before building its wheel."""
    validate_application_name(application)
    SpecifierSet(requires_pytincture)
    root = Path(modules_path).absolute()
    _file(root, f"{application}.py")
    if bff_manifest is not None:
        _file(root, bff_manifest)
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Packaged app paths must not contain symlinks")
        if path.is_file() and path.name != MANIFEST and "__pycache__" not in path.parts and path.suffix != ".pyc":
            name = path.relative_to(root).as_posix()
            files[name] = _digest(_file(root, name))
    if not files or len(files) > 10000:
        raise ValueError("Packaged app inventory must contain 1–10000 files")
    document = {"schema": 1, "application": application,
                "requires_pytincture": requires_pytincture,
                "bff_manifest": bff_manifest, "files": files}
    target = root / MANIFEST
    if target.is_symlink():
        raise ValueError("Manifest must not be a symlink")
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return target


@dataclass(frozen=True)
class PackagedApplication:
    distribution: str
    version: str
    application: str
    modules_path: Path
    bff_manifest: Path | None


def load_packaged_app(distribution, *, expected_version=None):
    """Verify one explicitly selected installed distribution without importing it.

    The deployment must authenticate/approve the wheel itself. Inventory hashes
    detect incomplete or changed files; they are not publisher signatures.
    """
    from . import __version__

    dist = metadata.distribution(distribution)
    if expected_version is not None and dist.version != expected_version:
        raise ValueError("Installed microapp version does not match environment selection")
    inventory = list(dist.files or ())
    manifests = [p for p in inventory if p.name == MANIFEST]
    if len(manifests) != 1:
        raise ValueError("Distribution must contain exactly one pytincture-app.json")
    manifest_entry = manifests[0]
    _relative(str(manifest_entry))
    manifest_path = Path(dist.locate_file(manifest_entry)).absolute()
    root = manifest_path.parent
    _file(root, MANIFEST)
    if manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("App manifest exceeds 4 MiB")
    doc = json.loads(manifest_path.read_text())
    if not isinstance(doc, dict) or set(doc) != {"schema", "application", "requires_pytincture", "bff_manifest", "files"} or type(doc["schema"]) is not int or doc["schema"] != 1:
        raise ValueError("Invalid packaged app manifest schema")
    validate_application_name(doc["application"])
    if __version__ not in SpecifierSet(doc["requires_pytincture"]):
        raise ValueError("Microapp requires a different Pytincture version")
    files = doc["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= 10000:
        raise ValueError("Invalid packaged app inventory")
    recorded = {str(p) for p in inventory}
    prefix = manifest_entry.parent
    for name, digest in files.items():
        path = _file(root, name)
        if str(prefix / name) not in recorded:
            raise ValueError(f"App file is not owned by the distribution: {name}")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) or _digest(path) != digest:
            raise ValueError(f"Packaged app integrity check failed: {name}")
    for entry in inventory:
        try:
            relative = PurePosixPath(str(entry)).relative_to(prefix)
        except ValueError:
            continue
        if str(relative) == MANIFEST or "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        if str(relative) not in files:
            raise ValueError(f"Uninventoried file in app directory: {relative}")
    if f'{doc["application"]}.py' not in files:
        raise ValueError("Application entrypoint is not inventoried")
    bff = doc["bff_manifest"]
    if bff is not None and (not isinstance(bff, str) or bff not in files):
        raise ValueError("BFF manifest is not inventoried")
    return PackagedApplication(dist.metadata["Name"], dist.version, doc["application"], root,
                               _file(root, bff) if bff else None)


def create_packaged_app(distribution, config=None, *, expected_version=None):
    """Create the normal FastAPI service using a verified installed app root.

    All authentication, host, storage and delivery settings remain deployment
    configuration. App manifests cannot set them.
    """
    from .configuration import PytinctureConfig
    from .factory import create_app

    packaged = load_packaged_app(distribution, expected_version=expected_version)
    if config is None:
        config = PytinctureConfig.from_env()
    elif not isinstance(config, PytinctureConfig):
        raise TypeError("config must be PytinctureConfig or None")
    resolved = replace(config, modules_path=str(packaged.modules_path),
                       default_application=packaged.application,
                       bff_prebuilt_manifest=str(packaged.bff_manifest) if packaged.bff_manifest else None)
    app = create_app(resolved)
    app.state.pytincture_packaged_app = packaged
    return app


def main():
    parser = argparse.ArgumentParser(description="Inventory an app resource directory before wheel build")
    parser.add_argument("--modules-path", required=True)
    parser.add_argument("--application", required=True)
    parser.add_argument("--requires-pytincture", default=">=1.0.2,<2")
    parser.add_argument("--bff-manifest")
    args = parser.parse_args()
    print(build_app_manifest(args.modules_path, args.application,
                            requires_pytincture=args.requires_pytincture,
                            bff_manifest=args.bff_manifest))


if __name__ == "__main__":
    main()
