"""Fail-closed archive member validation for release tooling."""

from __future__ import annotations

import stat
import tarfile
import zipfile
from pathlib import PurePosixPath
from typing import Iterable


class ArchiveSafetyError(ValueError):
    """An archive contains an ambiguous, unsafe, or executable member type."""


def _normalized_name(name: str, *, directory: bool) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise ArchiveSafetyError("archive contains an invalid member name")
    raw = name[:-1] if directory and name.endswith("/") else name
    if not raw or raw.startswith("/") or raw.startswith("./") or "//" in raw:
        raise ArchiveSafetyError(f"archive contains an unsafe path: {name!r}")
    parts = raw.split("/")
    member = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in parts) or member.is_absolute():
        raise ArchiveSafetyError(f"archive contains an unsafe path: {name!r}")
    if member.as_posix() != raw:
        raise ArchiveSafetyError(f"archive contains a non-canonical path: {name!r}")
    return raw


def validate_tar_members(
    members: Iterable[tarfile.TarInfo],
) -> list[tarfile.TarInfo]:
    """Return members after accepting only unique regular files/directories."""

    checked = list(members)
    seen: set[str] = set()
    for member in checked:
        is_directory = member.isdir()
        if not (is_directory or member.isfile()):
            raise ArchiveSafetyError(
                f"archive member {member.name!r} is not a regular file or directory"
            )
        name = _normalized_name(member.name, directory=is_directory)
        if name in seen:
            raise ArchiveSafetyError(f"archive contains duplicate member {name!r}")
        seen.add(name)
        if member.size < 0:
            raise ArchiveSafetyError(f"archive member {name!r} has an invalid size")
        if is_directory and member.size != 0:
            raise ArchiveSafetyError(
                f"archive directory {name!r} has an unexpected payload"
            )
    return checked


def validate_zip_members(
    members: Iterable[zipfile.ZipInfo],
) -> list[zipfile.ZipInfo]:
    """Return members after accepting only unique regular files/directories."""

    checked = list(members)
    seen: set[str] = set()
    for member in checked:
        is_directory = member.is_dir()
        name = _normalized_name(member.filename, directory=is_directory)
        if name in seen:
            raise ArchiveSafetyError(f"archive contains duplicate member {name!r}")
        seen.add(name)
        mode = (member.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        expected_type = stat.S_IFDIR if is_directory else stat.S_IFREG
        if file_type not in {0, expected_type}:
            raise ArchiveSafetyError(
                f"archive member {member.filename!r} is not a regular file or directory"
            )
        if member.file_size < 0 or member.compress_size < 0:
            raise ArchiveSafetyError(f"archive member {name!r} has an invalid size")
        if is_directory and member.file_size != 0:
            raise ArchiveSafetyError(
                f"archive directory {name!r} has an unexpected payload"
            )
    return checked
