#!/usr/bin/env python3
"""Rebuild the vendored stdlib from the pinned upstream archive and local patch."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from validate_vendored_pyodide import FULL, ROOT, _read_upstream


def patched_module(original: bytes, patch: dict) -> bytes:
    patch_path = ROOT / patch["patch"]
    if hashlib.sha256(original).hexdigest() != patch["original_sha256"]:
        raise ValueError("unexpected upstream tarfile source")
    if hashlib.sha256(patch_path.read_bytes()).hexdigest() != patch["patch_sha256"]:
        raise ValueError("stdlib patch checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="pytincture-stdlib-") as directory:
        source = Path(directory) / "tarfile.py"
        source.write_bytes(original)
        subprocess.run(
            ["git", "apply", "--no-index", str(patch_path)],
            cwd=directory,
            check=True,
        )
        result = source.read_bytes()
    if hashlib.sha256(result).hexdigest() != patch["patched_sha256"]:
        raise ValueError("patched tarfile checksum mismatch")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-archive", type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "security/pyodide-upstream.json").read_text())
    upstream = _read_upstream(args.upstream_archive, manifest)
    with tarfile.open(fileobj=io.BytesIO(upstream), mode="r:bz2") as archive:
        stdlib = archive.extractfile("pyodide/python_stdlib.zip").read()
    patch = manifest["stdlib_patch"]
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(stdlib)) as source:
        replacement = patched_module(source.read(patch["member"]), patch)
        with zipfile.ZipFile(output, "w") as target:
            target.comment = source.comment
            for member in source.infolist():
                content = replacement if member.filename == patch["member"] else source.read(member)
                target.writestr(member, content)
    (FULL / "python_stdlib.zip").write_bytes(output.getvalue())
    print("Rebuilt Pyodide stdlib; regenerate the SBOM and frontend integrity manifest.")


if __name__ == "__main__":
    main()
