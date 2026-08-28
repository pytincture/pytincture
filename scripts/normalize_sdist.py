#!/usr/bin/env python3
"""Normalize an sdist tarball so identical input produces identical bytes."""

from __future__ import annotations

import gzip
import os
import sys
import tarfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: normalize_sdist.py <archive.tar.gz> <unix-epoch>")
    archive_path = Path(sys.argv[1])
    epoch = int(sys.argv[2])
    temporary_path = archive_path.with_suffix(archive_path.suffix + ".normalized")

    with tarfile.open(archive_path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda member: member.name)
        with temporary_path.open("wb") as raw_output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=epoch) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as target:
                    for member in members:
                        member.mtime = epoch
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.pax_headers = {}
                        payload = source.extractfile(member) if member.isfile() else None
                        target.addfile(member, payload)

    os.replace(temporary_path, archive_path)
    print(f"Normalized {archive_path} with SOURCE_DATE_EPOCH={epoch}")


if __name__ == "__main__":
    main()
