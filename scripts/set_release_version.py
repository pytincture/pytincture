#!/usr/bin/env python3
"""Set one canonical release version and rebuild all synchronized runtime files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from versioning import npm_version_for_python


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text()
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Unable to update version in {path}")
    path.write_text(updated)


def replace_all(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text()
    updated, count = re.subn(pattern, replacement, source, flags=re.MULTILINE)
    if count == 0:
        raise SystemExit(f"Unable to find a runtime version in {path}")
    path.write_text(updated)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: set_release_version.py <PEP-440-version>")
    python_version = sys.argv[1]
    npm_version = npm_version_for_python(python_version)

    replace_once(
        ROOT / "pyproject.toml",
        r'^version = "[^"]+"$',
        f'version = "{python_version}"',
    )
    replace_once(
        ROOT / "pytincture" / "__init__.py",
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{python_version}"',
    )
    for relative_path in (
        "README.md",
        "pytincture/frontend/README.md",
    ):
        replace_all(
            ROOT / relative_path,
            r"@pytincture/runtime@[^/]+/dist/",
            f"@pytincture/runtime@{npm_version}/dist/",
        )
    subprocess.run(
        ["npm", "run", "build"],
        cwd=ROOT / "pytincture" / "frontend",
        check=True,
    )
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    print(f"Synchronized Python/browser {python_version} and npm {npm_version}")


if __name__ == "__main__":
    main()
