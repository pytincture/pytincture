#!/usr/bin/env python3
"""Validate trusted release metadata and retained Python artifacts."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from verify_npm_release import ReleaseVerificationError, verify_release_metadata


EXPECTED_PROJECT = "pytincture"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseVerificationError(message)


def _load_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"invalid {label} JSON: {exc}") from exc
    _require(isinstance(value, dict), f"{label} JSON must be an object")
    return value


def _metadata_identity(content: bytes, label: str, version: str) -> None:
    try:
        metadata = email.parser.BytesParser().parsebytes(content)
    except (TypeError, ValueError) as exc:
        raise ReleaseVerificationError(f"invalid {label} metadata: {exc}") from exc
    _require(metadata.get("Name") == EXPECTED_PROJECT, f"unexpected {label} project name")
    _require(metadata.get("Version") == version, f"{label} version does not match release")


def _safe_member_name(name: str) -> bool:
    member = PurePosixPath(name)
    return bool(name) and not member.is_absolute() and ".." not in member.parts and "\\" not in name


def _verify_wheel(path: Path, version: str) -> None:
    expected_metadata = f"pytincture-{version}.dist-info/METADATA"
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            _require(all(_safe_member_name(member.filename) for member in members), "wheel contains an unsafe path")
            metadata_members = [member for member in members if member.filename == expected_metadata]
            _require(len(metadata_members) == 1, "wheel must contain exact release METADATA")
            member = metadata_members[0]
            mode = member.external_attr >> 16
            _require(not stat.S_ISLNK(mode), "wheel METADATA must not be a symlink")
            _require(member.file_size <= 1024 * 1024, "wheel METADATA is unexpectedly large")
            _metadata_identity(archive.read(member), "wheel", version)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(f"invalid Python wheel: {exc}") from exc


def _verify_sdist(path: Path, version: str) -> None:
    expected_root = f"pytincture-{version}"
    expected_metadata = f"{expected_root}/PKG-INFO"
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            _require(all(_safe_member_name(member.name) for member in members), "sdist contains an unsafe path")
            roots = {PurePosixPath(member.name).parts[0] for member in members if member.name}
            _require(roots == {expected_root}, "sdist root does not match release version")
            metadata_members = [member for member in members if member.name == expected_metadata]
            _require(len(metadata_members) == 1, "sdist must contain exact release PKG-INFO")
            member = metadata_members[0]
            _require(member.isfile(), "sdist PKG-INFO must be a regular file")
            _require(member.size <= 1024 * 1024, "sdist PKG-INFO is unexpectedly large")
            metadata_file = archive.extractfile(member)
            _require(metadata_file is not None, "sdist PKG-INFO could not be read")
            _metadata_identity(metadata_file.read(), "sdist", version)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseVerificationError(f"invalid Python sdist: {exc}") from exc


def verify_python_artifacts(
    artifact_dir: Path,
    version: str,
) -> tuple[Path, Path]:
    """Return the exact wheel and sdist after identity and manifest checks."""

    root = artifact_dir.resolve(strict=True)
    wheels = sorted(artifact_dir.glob("*.whl"))
    sdists = sorted(artifact_dir.glob("*.tar.gz"))
    _require(len(wheels) == 1, "expected exactly one retained Python wheel")
    _require(len(sdists) == 1, "expected exactly one retained Python sdist")
    wheel = wheels[0]
    sdist = sdists[0]
    expected_names = {
        wheel: f"pytincture-{version}-py3-none-any.whl",
        sdist: f"pytincture-{version}.tar.gz",
    }
    for artifact, expected_name in expected_names.items():
        _require(artifact.name == expected_name, "Python artifact filename does not match release version")
        _require(artifact.is_file() and not artifact.is_symlink(), "Python artifact must be a regular file")
        _require(artifact.resolve(strict=True).parent == root, "Python artifact escapes artifact directory")

    _verify_wheel(wheel, version)
    _verify_sdist(sdist, version)

    manifest = _load_object(artifact_dir / "SHA256SUMS.json", "release hash manifest")
    _require(manifest.get("python_version") == version, "hash manifest version does not match release")
    hashes = manifest.get("sha256")
    _require(isinstance(hashes, dict), "hash manifest sha256 field must be an object")
    for artifact in (wheel, sdist):
        expected_digest = hashes.get(artifact.name)
        _require(
            isinstance(expected_digest, str) and len(expected_digest) == 64,
            f"hash manifest is missing {artifact.name}",
        )
        actual_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        _require(actual_digest == expected_digest, f"hash manifest mismatch for {artifact.name}")
    return wheel.resolve(), sdist.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--tag-sha", required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        python_version, _npm_version = verify_release_metadata(
            repository=args.repository,
            release_tag=args.release_tag,
            tag_sha=args.tag_sha,
            release=_load_object(args.release_json, "release"),
            run=_load_object(args.run_json, "workflow run"),
        )
        wheel_path, sdist_path = verify_python_artifacts(
            args.artifact_dir,
            python_version,
        )
    except (OSError, ReleaseVerificationError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        json.dumps(
            {
                "python_version": python_version,
                "wheel_path": str(wheel_path),
                "sdist_path": str(sdist_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
