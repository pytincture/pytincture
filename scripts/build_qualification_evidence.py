#!/usr/bin/env python3
"""Build one portable, integrity-checked qualification evidence document."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


SCHEMA_ID = (
    "https://github.com/pytincture/pytincture/contracts/"
    "qualification-evidence-v1.schema.json"
)
EXERCISES = (
    "standalone",
    "authenticated_bff",
    "federated_auth",
    "upgrade_rollback",
    "performance_service",
)
STATUSES = {
    "success": "passed",
    "failure": "failed",
    "passed": "passed",
    "failed": "failed",
    "cancelled": "cancelled",
    "skipped": "skipped",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_kind(filename: str) -> str | None:
    if filename.endswith(".whl"):
        return "wheel"
    if filename.endswith(".tar.gz"):
        return "sdist"
    if filename.endswith(".tgz"):
        return "npm"
    return None


def load_artifacts(path: Path) -> tuple[str, str, dict[str, str], dict[str, str]]:
    manifest = json.loads(path.read_text())
    hashes = manifest.get("sha256", {})
    files: dict[str, str] = {}
    normalized_hashes: dict[str, str] = {}
    for filename, digest in hashes.items():
        kind = _artifact_kind(filename)
        if kind is None:
            continue
        if kind in files:
            raise ValueError(f"artifact manifest contains multiple {kind} files")
        artifact_path = path.parent / filename
        if not artifact_path.is_file():
            raise ValueError(f"artifact file is missing: {filename}")
        actual_digest = _sha256(artifact_path)
        if digest != actual_digest:
            raise ValueError(f"artifact digest does not match: {filename}")
        files[kind] = filename
        normalized_hashes[kind] = digest
    missing = sorted({"wheel", "sdist", "npm"} - set(files))
    if missing:
        raise ValueError(f"artifact manifest is missing: {', '.join(missing)}")
    return (
        str(manifest.get("python_version") or ""),
        str(manifest.get("npm_version") or ""),
        files,
        normalized_hashes,
    )


def load_results(
    specifications: list[str], *, allow_missing: bool = False
) -> tuple[dict[str, dict], dict[str, str]]:
    results = {}
    hashes = {}
    for specification in specifications:
        label, separator, raw_path = specification.partition("=")
        if not separator or not label or not raw_path:
            raise ValueError("each --result must use LABEL=PATH")
        if label in results:
            raise ValueError(f"duplicate result label: {label}")
        path = Path(raw_path)
        if not path.is_file() and allow_missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error": "qualification result file was not produced",
                        "expected_path": raw_path,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"result {label} must contain a JSON object")
        results[label] = payload
        hashes[label] = _sha256(path)
    if not results:
        raise ValueError("at least one --result is required")
    return results, hashes


def utc_timestamp(value: str | None = None) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("tested_at must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("tested_at must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def github_run_url(environment: Mapping[str, str]) -> str:
    required = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    if not all(environment.get(name) for name in required):
        return ""
    return (
        f"{environment['GITHUB_SERVER_URL'].rstrip('/')}"
        f"/{environment['GITHUB_REPOSITORY']}/actions/runs/"
        f"{environment['GITHUB_RUN_ID']}"
    )


def build_evidence(args: argparse.Namespace, environment: Mapping[str, str]) -> dict:
    status = STATUSES[args.status]
    version, npm_version, artifact_files, artifact_hashes = load_artifacts(
        args.artifact_manifest
    )
    results, result_hashes = load_results(
        args.result, allow_missing=status != "passed"
    )
    commit_sha = args.commit_sha or environment.get("GITHUB_SHA", "")
    evidence_url = args.evidence_url or github_run_url(environment)
    evidence = {
        "$schema": SCHEMA_ID,
        "schema_version": 1,
        "exercise": args.exercise,
        "status": status,
        "version": version,
        "npm_version": npm_version,
        "tested_at": utc_timestamp(args.tested_at),
        "commit_sha": commit_sha,
        "evidence_url": evidence_url,
        "run": {
            "id": args.run_id or environment.get("GITHUB_RUN_ID", ""),
            "attempt": int(
                args.run_attempt or environment.get("GITHUB_RUN_ATTEMPT", "1")
            ),
            "job": args.job or environment.get("GITHUB_JOB", ""),
            "event": args.event or environment.get("GITHUB_EVENT_NAME", ""),
            "ref": args.ref or environment.get("GITHUB_REF", ""),
        },
        "artifact_files": artifact_files,
        "artifact_sha256": artifact_hashes,
        "result_sha256": result_hashes,
        "results": results,
    }
    failures = validate_evidence(evidence)
    if failures:
        raise ValueError("; ".join(failures))
    return evidence


def validate_evidence(evidence: dict) -> list[str]:
    failures = []
    required_strings = (
        "exercise",
        "status",
        "version",
        "npm_version",
        "tested_at",
        "commit_sha",
        "evidence_url",
    )
    for field in required_strings:
        if not isinstance(evidence.get(field), str) or not evidence[field]:
            failures.append(f"{field} is required")
    if evidence.get("$schema") != SCHEMA_ID or evidence.get("schema_version") != 1:
        failures.append("unsupported qualification evidence schema")
    if evidence.get("exercise") not in EXERCISES:
        failures.append("exercise is not supported")
    if evidence.get("status") not in set(STATUSES.values()):
        failures.append("status is not supported")
    if not COMMIT.fullmatch(str(evidence.get("commit_sha", ""))):
        failures.append("commit_sha must be a full lowercase Git SHA")
    try:
        utc_timestamp(evidence.get("tested_at"))
    except ValueError as exc:
        failures.append(str(exc))
    if not str(evidence.get("evidence_url", "")).startswith(("https://", "http://")):
        failures.append("evidence_url must be an absolute HTTP(S) URL")
    run = evidence.get("run", {})
    if not isinstance(run, dict):
        failures.append("run must be an object")
        run = {}
    for field in ("id", "job", "event", "ref"):
        if not isinstance(run.get(field), str) or not run[field]:
            failures.append(f"run.{field} is required")
    if not isinstance(run.get("attempt"), int) or run.get("attempt", 0) < 1:
        failures.append("run.attempt must be a positive integer")
    for map_name in ("artifact_files", "artifact_sha256"):
        values = evidence.get(map_name, {})
        if not isinstance(values, dict):
            failures.append(f"{map_name} must be an object")
            values = {}
        missing = sorted({"wheel", "sdist", "npm"} - set(values))
        if missing:
            failures.append(f"{map_name} is missing: {', '.join(missing)}")
        for kind, value in values.items():
            if not isinstance(value, str) or not value:
                failures.append(f"{map_name}.{kind} is required")
    artifact_hashes = evidence.get("artifact_sha256", {})
    if not isinstance(artifact_hashes, dict):
        artifact_hashes = {}
    for kind, digest in artifact_hashes.items():
        if not SHA256.fullmatch(str(digest)):
            failures.append(f"artifact_sha256.{kind} must be a SHA-256 digest")
    results = evidence.get("results", {})
    result_hashes = evidence.get("result_sha256", {})
    if not isinstance(results, dict) or not results:
        failures.append("results must not be empty")
        results = {}
    if not isinstance(result_hashes, dict):
        failures.append("result_sha256 must be an object")
        result_hashes = {}
    if set(results) != set(result_hashes):
        failures.append("result_sha256 keys must match results")
    if evidence.get("status") == "passed":
        for label, result in results.items():
            if not isinstance(result, dict):
                failures.append(f"results.{label} must be an object")
            elif result.get("status") not in (None, "passed"):
                failures.append(f"results.{label}.status contradicts passed evidence")
    for label, digest in result_hashes.items():
        if not SHA256.fullmatch(str(digest)):
            failures.append(f"result_sha256.{label} must be a SHA-256 digest")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exercise", choices=EXERCISES, required=True)
    parser.add_argument("--status", choices=tuple(STATUSES), required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--result", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tested-at")
    parser.add_argument("--commit-sha")
    parser.add_argument("--evidence-url")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--job")
    parser.add_argument("--event")
    parser.add_argument("--ref")
    args = parser.parse_args()
    try:
        evidence = build_evidence(args, os.environ)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"qualification evidence generation failed: {exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
