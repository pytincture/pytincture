#!/usr/bin/env python3
"""Install one built wheel with dependencies exported from the frozen uv lock."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def install_locked(python: Path, artifact: Path, extra: str | None = None) -> None:
    if not python.is_file():
        raise SystemExit(f"target Python does not exist: {python}")
    if not artifact.is_file() or not (
        artifact.suffix == ".whl" or artifact.name.endswith(".tar.gz")
    ):
        raise SystemExit(f"candidate Python artifact does not exist: {artifact}")

    with tempfile.TemporaryDirectory(prefix="pytincture-locked-install-") as directory:
        requirements = Path(directory) / "requirements.txt"
        export = [
            "uv",
            "export",
            "--quiet",
            "--frozen",
            "--format",
            "requirements-txt",
            "--no-emit-project",
            "--no-dev",
            "--output-file",
            str(requirements),
        ]
        if extra:
            export.extend(("--extra", extra))
        subprocess.run(export, cwd=ROOT, check=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--requirements",
                str(requirements),
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                str(artifact),
            ],
            cwd=ROOT,
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument(
        "--extra", choices=("oauth", "password", "redis", "saml", "mcp", "dev")
    )
    args = parser.parse_args()
    # Preserve a virtual environment's interpreter path instead of resolving
    # its symlink to the base interpreter.
    install_locked(args.python.absolute(), args.artifact.resolve(), args.extra)


if __name__ == "__main__":
    main()
