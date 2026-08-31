"""Backend-for-frontend operation discovery and registry state."""

import os
import threading
from collections.abc import Callable, Mapping
from typing import Any

from pytincture.dataclass import get_bff_manifest
from pytincture.backend.safe_paths import (
    UnsafePath,
    canonical_root,
    decode_python_source,
    read_contained_file,
)

_EXCLUDED_DIRECTORIES = {
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
}

BFFKey = tuple[str, str, str]
BFFOperation = dict[str, Any]
BFFRegistryFailures = dict[str, str]


def _manifest_failure_reason(exc: Exception) -> str:
    """Return a stable, non-sensitive reason for rejecting one source file."""
    if isinstance(exc, SyntaxError):
        return "invalid_python_syntax"
    if isinstance(exc, UnicodeDecodeError):
        return "invalid_python_encoding"
    if isinstance(exc, UnsafePath):
        return "unsafe_source_path"
    if isinstance(exc, OSError):
        return "unreadable_source"
    return "invalid_bff_manifest"


def _scan_bff_registry(
    modules_root: str,
    manifest_loader: Callable[
        [str], Mapping[tuple[str, str], BFFOperation]
    ],
) -> tuple[dict[BFFKey, BFFOperation], BFFRegistryFailures]:
    """Build a fail-closed registry while isolating failures to their files."""
    root_path = canonical_root(modules_root)
    registry: dict[BFFKey, BFFOperation] = {}
    failures: BFFRegistryFailures = {}
    if not os.path.isdir(root_path):
        return registry, failures

    for root, dirs, files in os.walk(root_path):
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".") and directory not in _EXCLUDED_DIRECTORIES
            and not os.path.islink(os.path.join(root, directory))
        ]
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("."):
                continue
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path):
                continue
            relative_path = os.path.relpath(file_path, root_path).replace(os.sep, "/")
            try:
                secure_file = read_contained_file(root_path, relative_path)
                if manifest_loader is get_bff_manifest:
                    source = decode_python_source(secure_file.content)
                    file_manifest = manifest_loader(file_path, source=source)
                else:
                    file_manifest = manifest_loader(file_path)
            except (OSError, SyntaxError, UnicodeDecodeError, UnsafePath, ValueError) as exc:
                # No operation from a rejected file enters the authorization
                # registry. Valid files remain available to unrelated apps.
                failures[relative_path] = _manifest_failure_reason(exc)
                continue
            for (class_name, function_name), operation in file_manifest.items():
                registry[(relative_path, class_name, function_name)] = {
                    **operation,
                    "_source_path": secure_file.path,
                    "_source_digest": secure_file.digest,
                }
    return registry, failures


def build_bff_registry(
    modules_root: str,
    manifest_loader: Callable[
        [str], Mapping[tuple[str, str], BFFOperation]
    ] = get_bff_manifest,
) -> dict[BFFKey, BFFOperation]:
    """Discover exports; malformed files contribute no callable operations."""
    registry, _failures = _scan_bff_registry(modules_root, manifest_loader)
    return registry


class BFFRegistry:
    """Own the root and immutable-at-read operation snapshot for one app."""

    def __init__(
        self,
        modules_root: str,
        manifest_loader: Callable[
            [str], Mapping[tuple[str, str], BFFOperation]
        ] = get_bff_manifest,
        *,
        autoload: bool = True,
    ):
        self._manifest_loader = manifest_loader
        self.root = canonical_root(modules_root)
        self.operations: dict[BFFKey, BFFOperation] = {}
        self.failures: BFFRegistryFailures = {}
        self.loaded = False
        self._lock = threading.RLock()
        if autoload:
            self.reload()

    def reload(self, modules_root: str | None = None) -> dict[BFFKey, BFFOperation]:
        with self._lock:
            if modules_root is not None:
                self.root = canonical_root(modules_root)
            self.operations, self.failures = _scan_bff_registry(
                self.root, self._manifest_loader
            )
            self.loaded = True
            return self.operations

    def operation(
        self,
        modules_root: str,
        relative_path: str,
        class_name: str,
        function_name: str,
    ) -> BFFOperation | None:
        with self._lock:
            root = canonical_root(modules_root)
            if root != self.root or not self.loaded:
                self.reload(root)
            key = (relative_path.replace(os.sep, "/"), class_name, function_name)
            operation = self.operations.get(key)
            if operation is None:
                return None
            try:
                current = read_contained_file(root, key[0])
            except (OSError, UnsafePath):
                self.reload(root)
                return None
            if current.digest != operation.get("_source_digest"):
                self.reload(root)
                operation = self.operations.get(key)
            return operation
