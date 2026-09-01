#!/usr/bin/env python3
"""Validate RC progression and the evidence required for a final 1.0 release."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from versioning import npm_version_for_python


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    "CHANGELOG.md",
    "SECURITY.md",
    "docs/public-api.md",
    "docs/compatibility.md",
    "docs/migrations/0.10-to-1.0.md",
    "docs/production-deployment.md",
    "docs/releasing.md",
)
RC_VERSION = re.compile(r"^1\.0\.0rc([1-9]\d*)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def source_versions() -> tuple[str, str]:
    init = (ROOT / "pytincture" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)', init, re.MULTILINE)
    if not match:
        raise ValueError("pytincture.__version__ is missing")
    python_version = match.group(1)
    package = json.loads((ROOT / "pytincture" / "frontend" / "package.json").read_text())
    return python_version, package["version"]


def validate_static(record: dict) -> list[str]:
    failures = []
    required = {
        "schema_version",
        "minimum_observation_days",
        "observation_started_at",
        "observation_approval_url",
        "release_candidates",
        "representative_applications",
        "upgrade_exercises",
        "rollback_exercises",
        "performance_reviews",
        "repository_policy_reviews",
        "production_edge_reviews",
        "security_reviews",
        "defect_audits",
        "final_decision",
    }
    missing = sorted(required - set(record))
    if missing:
        failures.append(f"qualification record is missing keys: {', '.join(missing)}")
    if record.get("schema_version") != 1:
        failures.append("qualification schema_version must be 1")
    if record.get("minimum_observation_days", 0) < 30:
        failures.append("minimum_observation_days must be at least 30")
    try:
        parse_time(record.get("observation_started_at"), "observation_started_at")
    except ValueError as exc:
        failures.append(str(exc))
    if not str(record.get("observation_approval_url", "")).startswith(
        ("https://", "http://")
    ):
        failures.append("observation_approval_url must be an absolute HTTP(S) URL")
    apps = record.get("representative_applications", {})
    for mode in ("standalone", "authenticated_bff", "federated_auth"):
        if not isinstance(apps.get(mode), list):
            failures.append(f"representative_applications.{mode} must be a list")
    for document in REQUIRED_DOCS:
        if not (ROOT / document).is_file():
            failures.append(f"required release document is missing: {document}")
    try:
        python_version, npm_version = source_versions()
        expected_npm = npm_version_for_python(python_version)
        if npm_version != expected_npm:
            failures.append(
                f"npm version {npm_version} does not match Python release {python_version} ({expected_npm})"
            )
    except ValueError as exc:
        failures.append(str(exc))
    return failures


def validate_candidate(candidate: dict, index: int) -> list[str]:
    failures = []
    prefix = f"release_candidates[{index}]"
    version = candidate.get("version", "")
    if not RC_VERSION.fullmatch(version):
        failures.append(f"{prefix}.version must be 1.0.0rcN")
    for field in ("published_at", "ci_url"):
        if not candidate.get(field):
            failures.append(f"{prefix}.{field} is required")
    if candidate.get("published_at"):
        try:
            parse_time(candidate["published_at"], f"{prefix}.published_at")
        except ValueError as exc:
            failures.append(str(exc))
    if not COMMIT.fullmatch(candidate.get("commit_sha", "")):
        failures.append(f"{prefix}.commit_sha must be a full Git commit SHA")
    artifacts = candidate.get("artifacts", {})
    for artifact in ("wheel", "sdist", "npm"):
        if not SHA256.fullmatch(artifacts.get(artifact, "")):
            failures.append(f"{prefix}.artifacts.{artifact} must be a SHA-256 digest")
    if candidate.get("status") != "passed":
        failures.append(f"{prefix}.status must be passed")
    return failures


def passed_evidence(
    entries: list,
    name: str,
    required_version: str | None = None,
    not_before: datetime | None = None,
) -> list[str]:
    failures = []
    if not entries:
        return [f"{name} requires at least one recorded exercise"]
    required_version_found = required_version is None
    for index, entry in enumerate(entries):
        prefix = f"{name}[{index}]"
        if entry.get("status") != "passed":
            failures.append(f"{prefix}.status must be passed")
        if not entry.get("evidence_url"):
            failures.append(f"{prefix}.evidence_url is required")
        is_required_version = (
            required_version is not None
            and entry.get("version") == required_version
        )
        if is_required_version:
            required_version_found = True
        try:
            tested_at = parse_time(entry.get("tested_at"), f"{prefix}.tested_at")
            if (
                is_required_version
                and not_before is not None
                and tested_at < not_before
            ):
                failures.append(
                    f"{prefix}.tested_at must not predate {required_version} publication"
                )
        except ValueError as exc:
            failures.append(str(exc))
    if not required_version_found:
        failures.append(f"{name} must qualify {required_version} at least once")
    return failures


def validate_final(record: dict) -> list[str]:
    failures = []
    candidates = record.get("release_candidates", [])
    if len(candidates) < 2:
        failures.append("at least two release candidates must be recorded")
    for index, candidate in enumerate(candidates):
        failures.extend(validate_candidate(candidate, index))
    versions = [candidate.get("version") for candidate in candidates]
    if len(versions) != len(set(versions)):
        failures.append("release candidate versions must be unique")
    if not {"1.0.0rc1", "1.0.0rc2"}.issubset(versions):
        failures.append("qualification must include both 1.0.0rc1 and 1.0.0rc2")

    latest_version = ""
    latest_published_at = None
    if candidates:
        latest_candidate = max(
            candidates,
            key=lambda candidate: int(RC_VERSION.fullmatch(candidate.get("version", "rc0")).group(1))
            if RC_VERSION.fullmatch(candidate.get("version", ""))
            else -1,
        )
        latest_version = latest_candidate.get("version", "")
        try:
            latest_published_at = parse_time(
                latest_candidate.get("published_at"), "latest release candidate published_at"
            )
        except ValueError as exc:
            failures.append(str(exc))

    applications = record.get("representative_applications", {})
    for mode in ("standalone", "authenticated_bff", "federated_auth"):
        failures.extend(
            passed_evidence(
                applications.get(mode, []),
                f"representative_applications.{mode}",
                latest_version,
                latest_published_at,
            )
        )
    failures.extend(
        passed_evidence(
            record.get("upgrade_exercises", []),
            "upgrade_exercises",
            latest_version,
            latest_published_at,
        )
    )
    failures.extend(
        passed_evidence(
            record.get("rollback_exercises", []),
            "rollback_exercises",
            latest_version,
            latest_published_at,
        )
    )
    failures.extend(
        passed_evidence(
            record.get("performance_reviews", []),
            "performance_reviews",
            latest_version,
            latest_published_at,
        )
    )
    failures.extend(
        passed_evidence(
            record.get("repository_policy_reviews", []),
            "repository_policy_reviews",
            latest_version,
            latest_published_at,
        )
    )
    edge_reviews = record.get("production_edge_reviews", [])
    failures.extend(
        passed_evidence(
            edge_reviews,
            "production_edge_reviews",
            latest_version,
            latest_published_at,
        )
    )
    required_edge_checks = {
        "https_redirect",
        "hsts",
        "canonical_origin",
        "trusted_proxy_headers",
    }
    for index, review in enumerate(edge_reviews):
        if review.get("version") != latest_version:
            continue
        checks = review.get("checks", {})
        missing_checks = sorted(
            check for check in required_edge_checks if checks.get(check) is not True
        )
        if missing_checks:
            failures.append(
                f"production_edge_reviews[{index}].checks must pass: "
                f"{', '.join(missing_checks)}"
            )

    reviews = record.get("security_reviews", [])
    failures.extend(
        passed_evidence(reviews, "security_reviews", latest_version, latest_published_at)
    )
    if any(review.get("open_critical", 1) or review.get("open_high", 1) for review in reviews):
        failures.append("security reviews must report zero open critical/high findings")
    audits = record.get("defect_audits", [])
    failures.extend(passed_evidence(audits, "defect_audits", latest_version, latest_published_at))
    if any(audit.get("open_p0", 1) or audit.get("open_p1", 1) for audit in audits):
        failures.append("defect audits must report zero open P0/P1 defects")

    decision = record.get("final_decision", {})
    if decision.get("status") != "go":
        failures.append("final_decision.status must be go")
    if not decision.get("approvers"):
        failures.append("final_decision.approvers must name at least one approver")
    try:
        decision_time = parse_time(decision.get("decided_at"), "final_decision.decided_at")
    except ValueError as exc:
        failures.append(str(exc))
        decision_time = None
    if candidates and decision_time is not None:
        try:
            first_rc = min(
                parse_time(candidate["published_at"], "release candidate published_at")
                for candidate in candidates
            )
            observation_started_at = parse_time(
                record.get("observation_started_at"), "observation_started_at"
            )
            if observation_started_at < first_rc:
                failures.append(
                    "observation_started_at must not predate the first release candidate"
                )
            observed_days = (
                decision_time - observation_started_at
            ).total_seconds() / 86400
            if observed_days < record.get("minimum_observation_days", 30):
                failures.append(
                    f"RC observation period is {observed_days:.1f} days; at least "
                    f"{record.get('minimum_observation_days', 30)} are required"
                )
            last_rc = max(
                parse_time(candidate["published_at"], "release candidate published_at")
                for candidate in candidates
            )
            if decision_time < last_rc:
                failures.append("final go/no-go decision must occur after the latest RC")
        except ValueError as exc:
            failures.append(str(exc))
    return failures


def validate_release_ref(record: dict, release_ref: str) -> list[str]:
    version = release_ref.removeprefix("v")
    python_version, npm_version = source_versions()
    failures = []
    if version != python_version:
        failures.append(f"release tag {release_ref} does not match Python version {python_version}")
    expected_npm = npm_version_for_python(version)
    if npm_version != expected_npm:
        failures.append(f"npm version {npm_version} does not match release {expected_npm}")
    rc_match = RC_VERSION.fullmatch(version)
    if rc_match:
        required_prior = int(rc_match.group(1)) - 1
        recorded = {candidate.get("version") for candidate in record.get("release_candidates", [])}
        for index, candidate in enumerate(record.get("release_candidates", [])):
            if candidate.get("version") in {
                f"1.0.0rc{number}" for number in range(1, required_prior + 1)
            }:
                failures.extend(validate_candidate(candidate, index))
        for number in range(1, required_prior + 1):
            if f"1.0.0rc{number}" not in recorded:
                failures.append(f"1.0.0rc{number} evidence is required before releasing {version}")
    elif version == "1.0.0":
        failures.extend(validate_final(record))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification", type=Path, default=ROOT / "release" / "qualification.json")
    parser.add_argument("--release-ref")
    args = parser.parse_args()
    record = json.loads(args.qualification.read_text())
    failures = validate_static(record)
    if args.release_ref:
        try:
            failures.extend(validate_release_ref(record, args.release_ref))
        except ValueError as exc:
            failures.append(str(exc))
    if failures:
        print("Pytincture release gate: NO-GO")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print(
        "Pytincture release gate: GO"
        if args.release_ref
        else "Pytincture static release controls: PASS"
    )
    print(f"- Python version: {source_versions()[0]}")
    print(f"- npm version: {source_versions()[1]}")
    print(f"- recorded release candidates: {len(record['release_candidates'])}")


if __name__ == "__main__":
    main()
