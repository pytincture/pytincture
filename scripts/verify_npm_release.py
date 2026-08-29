#!/usr/bin/env python3
"""Validate trusted release metadata and the retained npm artifact."""

from __future__ import annotations

import argparse
import json
import re
import tarfile
from pathlib import Path
from typing import Any, Mapping

from versioning import npm_version_for_python


EXPECTED_PACKAGE = "@pytincture/runtime"
EXPECTED_WORKFLOW_PATH = ".github/workflows/ci.yml"
_TAG = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?(?:\.dev(?:0|[1-9]\d*))?)$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


class ReleaseVerificationError(ValueError):
    """Raised when publication inputs do not describe one trusted release."""


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


def verify_release_metadata(
    *,
    repository: str,
    release_tag: str,
    tag_sha: str,
    release: Mapping[str, Any],
    run: Mapping[str, Any],
) -> tuple[str, str]:
    """Return canonical Python/npm versions after checking release provenance."""

    tag_match = _TAG.fullmatch(release_tag)
    _require(tag_match is not None, "release tag is not a supported Pytincture version")
    _require(_SHA.fullmatch(tag_sha) is not None, "tag SHA must be a full commit SHA")
    _require(release.get("tag_name") == release_tag, "release tag does not match")
    _require(release.get("draft") is False, "release must not be a draft")
    _require(bool(release.get("published_at")), "release must be published")

    run_repository = run.get("repository")
    _require(isinstance(run_repository, dict), "workflow run repository is missing")
    _require(
        run_repository.get("full_name") == repository,
        "workflow run belongs to a different repository",
    )
    _require(run.get("path") == EXPECTED_WORKFLOW_PATH, "unexpected signer workflow")
    _require(run.get("event") == "release", "workflow run was not release-triggered")
    _require(run.get("status") == "completed", "workflow run is not complete")
    _require(run.get("conclusion") == "success", "workflow run did not succeed")
    _require(run.get("head_branch") == release_tag, "workflow run tag does not match")
    _require(run.get("head_sha") == tag_sha, "workflow run commit does not match tag")
    _require(isinstance(run.get("id"), int) and run["id"] > 0, "workflow run id is invalid")

    python_version = tag_match.group("version")
    return python_version, npm_version_for_python(python_version)


def verify_npm_artifact(artifact_dir: Path, npm_version: str) -> Path:
    """Return the only expected npm tarball after checking its package metadata."""

    root = artifact_dir.resolve(strict=True)
    candidates = sorted(artifact_dir.glob("*.tgz"))
    _require(len(candidates) == 1, "expected exactly one retained npm tarball")
    artifact = candidates[0]
    _require(artifact.is_file() and not artifact.is_symlink(), "npm artifact must be a regular file")
    resolved = artifact.resolve(strict=True)
    _require(resolved.parent == root, "npm artifact escapes the artifact directory")
    expected_name = f"pytincture-runtime-{npm_version}.tgz"
    _require(artifact.name == expected_name, "npm artifact filename does not match release version")

    try:
        with tarfile.open(resolved, mode="r:gz") as package:
            members = [member for member in package.getmembers() if member.name == "package/package.json"]
            _require(len(members) == 1, "npm artifact must contain one package/package.json")
            member = members[0]
            _require(member.isfile(), "package/package.json must be a regular file")
            _require(member.size <= 1024 * 1024, "package/package.json is unexpectedly large")
            package_file = package.extractfile(member)
            _require(package_file is not None, "package/package.json could not be read")
            metadata = json.loads(package_file.read().decode("utf-8"))
    except (tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"invalid npm artifact: {exc}") from exc

    _require(isinstance(metadata, dict), "npm package metadata must be an object")
    _require(metadata.get("name") == EXPECTED_PACKAGE, "unexpected npm package name")
    _require(metadata.get("version") == npm_version, "npm package version does not match release")
    return resolved


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
        python_version, npm_version = verify_release_metadata(
            repository=args.repository,
            release_tag=args.release_tag,
            tag_sha=args.tag_sha,
            release=_load_object(args.release_json, "release"),
            run=_load_object(args.run_json, "workflow run"),
        )
        npm_path = verify_npm_artifact(args.artifact_dir, npm_version)
    except (OSError, ReleaseVerificationError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        json.dumps(
            {
                "python_version": python_version,
                "npm_version": npm_version,
                "npm_path": str(npm_path),
                "dist_tag": "next" if "-" in npm_version else "latest",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
