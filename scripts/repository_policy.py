#!/usr/bin/env python3
"""Audit or apply Pytincture's versioned GitHub branch-protection policy."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "repository-policy-v1.json"


def api_request(url: str, token: str, *, method: str = "GET", body: dict | None = None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def expected_payload(contract: dict, profile: str) -> dict:
    return {
        "required_status_checks": {
            "strict": contract["strict"],
            "contexts": contract["profiles"][profile]["required_checks"],
        },
        "enforce_admins": contract["enforce_admins"],
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": contract["dismiss_stale_reviews"],
            "require_code_owner_reviews": contract["require_code_owner_reviews"],
            "required_approving_review_count": contract[
                "required_approving_review_count"
            ],
            "require_last_push_approval": contract["require_last_push_approval"],
        },
        "restrictions": None,
        "required_conversation_resolution": contract["required_conversation_resolution"],
        "allow_force_pushes": contract["allow_force_pushes"],
        "allow_deletions": contract["allow_deletions"],
        "block_creations": False,
        "required_linear_history": False,
        "allow_fork_syncing": True,
        "lock_branch": False,
    }


def validate_policy(actual: dict, contract: dict, profile: str) -> list[str]:
    failures = []
    required = set(contract["profiles"][profile]["required_checks"])
    checks = actual.get("required_status_checks") or {}
    present = set(checks.get("contexts", []))
    missing = sorted(required - present)
    if missing:
        failures.append(f"required status checks are missing: {', '.join(missing)}")
    if checks.get("strict") is not contract["strict"]:
        failures.append("strict up-to-date status checks are not enforced")
    reviews = actual.get("required_pull_request_reviews") or {}
    for key in (
        "required_approving_review_count",
        "require_code_owner_reviews",
        "dismiss_stale_reviews",
        "require_last_push_approval",
    ):
        if reviews.get(key) != contract[key]:
            failures.append(f"pull request review policy {key} must be {contract[key]}")
    for key in (
        "enforce_admins",
        "required_conversation_resolution",
        "allow_force_pushes",
        "allow_deletions",
    ):
        actual_value = (actual.get(key) or {}).get("enabled")
        if actual_value is not contract[key]:
            failures.append(f"branch policy {key} must be {contract[key]}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("bootstrap", "release"), default="release")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    contract = json.loads(CONTRACT.read_text())
    repository = os.environ.get("GITHUB_REPOSITORY", "pytincture/pytincture")
    token = os.environ.get("GITHUB_TOKEN", "")
    url = (
        f"https://api.github.com/repos/{repository}/branches/"
        f"{contract['branch']}/protection"
    )
    if args.apply:
        if not token:
            raise SystemExit("GITHUB_TOKEN with repository administration permission is required")
        api_request(url, token, method="PUT", body=expected_payload(contract, args.profile))

    try:
        actual = api_request(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(f"{contract['branch']} is not protected") from exc
        raise
    failures = validate_policy(actual, contract, args.profile)
    if failures:
        print(f"Pytincture repository policy ({args.profile}): NO-GO")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print(f"Pytincture repository policy ({args.profile}): GO")


if __name__ == "__main__":
    main()
