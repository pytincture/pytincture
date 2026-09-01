#!/usr/bin/env python3
"""Fail closed when tracked source contains common credential material."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
PATTERNS = {
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,255}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        content = path.read_bytes()
        if b"\x00" in content:
            continue
        text = content.decode("utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(Finding(path=path, line=line_number, rule=rule))
    return findings


def _tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = [path.resolve() for path in args.paths] if args.paths else _tracked_paths()
    findings = scan_paths(paths)
    if findings:
        for finding in findings:
            try:
                display = finding.path.relative_to(ROOT)
            except ValueError:
                display = finding.path
            print(f"{display}:{finding.line}: possible secret ({finding.rule})")
        raise SystemExit(f"secret scan failed with {len(findings)} finding(s)")
    print(f"secret scan passed for {len(paths)} file(s)")


if __name__ == "__main__":
    main()
