"""Canonical, no-symlink filesystem access for application-owned files."""

from __future__ import annotations

import hashlib
import keyword
import os
import re
import stat
import tokenize
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath


APPLICATION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED_APPLICATION_NAMES = frozenset(
    {
        "__init__",
        "__main__",
        "__pycache__",
        "appcode",
        "auth",
        "classcall",
        "con",
        "docs",
        "favicon",
        "frontend",
        "healthz",
        "logs",
        "nul",
        "mcp",
        "openapi",
        "prn",
        "readyz",
        "redoc",
        "static",
        "aux",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


class UnsafePath(ValueError):
    """Raised when a requested path is not a stable regular file under its root."""


@dataclass(frozen=True, slots=True)
class SecureFile:
    path: str
    relative_path: str
    content: bytes
    digest: str
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int

    @property
    def identity(self) -> tuple[int, int, int, int, int]:
        return (
            self.device,
            self.inode,
            self.size,
            self.modified_ns,
            self.changed_ns,
        )


@dataclass(frozen=True, slots=True)
class SecureFileMetadata:
    """Identity and size metadata collected through a no-follow open."""

    path: str
    relative_path: str
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int

    @property
    def identity(self) -> tuple[int, int, int, int, int]:
        return (
            self.device,
            self.inode,
            self.size,
            self.modified_ns,
            self.changed_ns,
        )


@dataclass(slots=True)
class SecureFileHandle:
    """A no-follow file descriptor paired with its verified identity."""

    descriptor: int
    metadata: SecureFileMetadata

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def validate_application_name(value: str) -> str:
    """Require one non-reserved ASCII Python identifier."""
    candidate = str(value or "")
    if (
        not APPLICATION_NAME.fullmatch(candidate)
        or keyword.iskeyword(candidate)
        or candidate.casefold() in RESERVED_APPLICATION_NAMES
    ):
        raise ValueError("application must be a non-reserved Python identifier")
    return candidate


def decode_python_source(content: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(BytesIO(content).readline)
    return content.decode(encoding)


def canonical_root(root: str) -> str:
    resolved = os.path.realpath(os.path.abspath(os.fspath(root)))
    if not os.path.isdir(resolved):
        raise UnsafePath("filesystem root is not a directory")
    return resolved


def normalize_relative_path(value: str) -> str:
    """Normalize a POSIX relative path and reject cross-platform traversal syntax."""
    candidate = str(value or "")
    if not candidate or "\\" in candidate or "\x00" in candidate or ":" in candidate:
        raise UnsafePath("invalid relative path")
    pure = PurePosixPath(candidate)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafePath("invalid relative path")
    return "/".join(pure.parts)


def resolve_contained_path(
    root: str,
    relative_path: str,
    *,
    require_file: bool = True,
) -> str:
    """Resolve an existing path while rejecting every symlink component."""
    root_path = canonical_root(root)
    normalized = normalize_relative_path(relative_path)
    current = root_path
    parts = normalized.split("/")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise UnsafePath("path does not exist") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePath("symlinks are not allowed")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise UnsafePath("path parent is not a directory")
    final = os.path.realpath(current)
    try:
        contained = os.path.commonpath((root_path, final)) == root_path
    except ValueError:
        contained = False
    if not contained:
        raise UnsafePath("path escapes its root")
    try:
        final_metadata = os.stat(final, follow_symlinks=False)
    except OSError as exc:
        raise UnsafePath("path changed during resolution") from exc
    if require_file and not stat.S_ISREG(final_metadata.st_mode):
        raise UnsafePath("path is not a regular file")
    if not require_file and not stat.S_ISDIR(final_metadata.st_mode):
        raise UnsafePath("path is not a directory")
    return final


def _open_relative_nofollow(root: str, relative_path: str) -> int:
    """Open a file through no-follow directory descriptors when supported."""
    normalized = normalize_relative_path(relative_path)
    parts = normalized.split("/")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        root_fd = os.open(root, os.O_RDONLY | directory | nofollow)
        descriptors.append(root_fd)
        parent_fd = root_fd
        for part in parts[:-1]:
            parent_fd = os.open(
                part,
                os.O_RDONLY | directory | nofollow,
                dir_fd=parent_fd,
            )
            descriptors.append(parent_fd)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | nofollow | getattr(os, "O_BINARY", 0),
            dir_fd=parent_fd,
        )
        return file_fd
    except (NotImplementedError, TypeError):
        path = resolve_contained_path(root, normalized)
        return os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_BINARY", 0),
        )
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def open_contained_file(
    root: str,
    relative_path: str,
    *,
    max_bytes: int | None = None,
    expected: SecureFileMetadata | None = None,
) -> SecureFileHandle:
    """Open a contained regular file without reading or following links."""
    root_path = canonical_root(root)
    normalized = normalize_relative_path(relative_path)
    path = resolve_contained_path(root_path, normalized)
    try:
        descriptor = _open_relative_nofollow(root_path, normalized)
    except OSError as exc:
        raise UnsafePath("unable to open contained file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePath("path is not a regular file")
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise UnsafePath("file changed during secure open") from exc
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise UnsafePath("file changed during secure open")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise UnsafePath("file exceeds configured size limit")
        secure_metadata = SecureFileMetadata(
            path=path,
            relative_path=normalized,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
            changed_ns=metadata.st_ctime_ns,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
        if expected is not None and secure_metadata.identity != expected.identity:
            raise UnsafePath("file changed after metadata validation")
        return SecureFileHandle(descriptor=descriptor, metadata=secure_metadata)
    except BaseException:
        os.close(descriptor)
        raise


def stat_contained_file(
    root: str,
    relative_path: str,
    *,
    max_bytes: int | None = None,
) -> SecureFileMetadata:
    """Return no-follow metadata without reading file contents."""
    handle = open_contained_file(root, relative_path, max_bytes=max_bytes)
    try:
        return handle.metadata
    finally:
        handle.close()


def hash_open_file(handle: SecureFileHandle) -> str:
    """Hash an open file incrementally and rewind it for subsequent streaming."""
    digest = hashlib.sha256()
    os.lseek(handle.descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(handle.descriptor, 64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(handle.descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def read_contained_file(
    root: str,
    relative_path: str,
    *,
    max_bytes: int | None = None,
) -> SecureFile:
    """Atomically open and read a contained regular file without following links."""
    handle = open_contained_file(root, relative_path, max_bytes=max_bytes)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(handle.descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise UnsafePath("file exceeds configured size limit")
            chunks.append(chunk)
        content = b"".join(chunks)
    finally:
        handle.close()
    metadata = handle.metadata
    return SecureFile(
        path=metadata.path,
        relative_path=metadata.relative_path,
        content=content,
        digest=hashlib.sha256(content).hexdigest(),
        size=len(content),
        modified_ns=metadata.modified_ns,
        changed_ns=metadata.changed_ns,
        device=metadata.device,
        inode=metadata.inode,
    )
