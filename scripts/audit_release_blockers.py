#!/usr/bin/env python3
"""Fail when open GitHub issues carry a release-blocking label."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


BLOCKING_LABELS = {
    "priority:p0",
    "priority:p1",
    "security:critical",
    "security:high",
    "release-blocker",
}


def main() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "pytincture/pytincture")
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    issues = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": page})
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/issues?{query}",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    blockers = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        labels = {label["name"].casefold() for label in issue.get("labels", [])}
        matched = sorted(labels & BLOCKING_LABELS)
        if matched:
            blockers.append((issue["number"], issue["title"], matched, issue["html_url"]))
    if blockers:
        print("Pytincture issue audit: NO-GO")
        for number, title, labels, url in blockers:
            print(f"- #{number} {title} ({', '.join(labels)}): {url}")
        raise SystemExit(1)
    print("Pytincture issue audit: GO (zero open labeled release blockers)")


if __name__ == "__main__":
    main()
