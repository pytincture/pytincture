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
BFFManifestCache = dict[
    str, tuple[str, Mapping[tuple[str, str], BFFOperation]]
]


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
    *,
    manifest_cache: BFFManifestCache | None = None,
) -> tuple[dict[BFFKey, BFFOperation], BFFRegistryFailures]:
    """Build a fail-closed registry while isolating failures to their files."""
    root_path = canonical_root(modules_root)
    registry: dict[BFFKey, BFFOperation] = {}
    failures: BFFRegistryFailures = {}
    seen = set()
    if not os.path.isdir(root_path):
        if manifest_cache is not None:
            manifest_cache.clear()
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
                seen.add(relative_path)
                cached = manifest_cache.get(relative_path) if manifest_cache is not None else None
                if cached is not None and cached[0] == secure_file.digest:
                    file_manifest = cached[1]
                elif manifest_loader is get_bff_manifest:
                    source = decode_python_source(secure_file.content)
                    file_manifest = manifest_loader(file_path, source=source)
                else:
                    file_manifest = manifest_loader(file_path)
                if manifest_cache is not None:
                    manifest_cache[relative_path] = (secure_file.digest, file_manifest)
            except (OSError, SyntaxError, UnicodeDecodeError, UnsafePath, ValueError) as exc:
                # No operation from a rejected file enters the authorization
                # registry. Valid files remain available to unrelated apps.
                failures[relative_path] = _manifest_failure_reason(exc)
                if manifest_cache is not None:
                    manifest_cache.pop(relative_path, None)
                continue
            for (class_name, function_name), operation in file_manifest.items():
                registry[(relative_path, class_name, function_name)] = {
                    **operation,
                    "_source_path": secure_file.path,
                    "_source_digest": secure_file.digest,
                }
    if manifest_cache is not None:
        for deleted in manifest_cache.keys() - seen:
            del manifest_cache[deleted]
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
        revalidate: bool = True,
        prebuilt_manifest: str | None = None,
    ):
        self._manifest_loader = manifest_loader
        self.revalidate = revalidate
        self.prebuilt_manifest = prebuilt_manifest
        self.root = canonical_root(modules_root)
        self.operations: dict[BFFKey, BFFOperation] = {}
        self.failures: BFFRegistryFailures = {}
        self.loaded = False
        self._manifest_cache: BFFManifestCache = {}
        self._lock = threading.RLock()
        if autoload:
            self.reload()

    def reload(self, modules_root: str | None = None) -> dict[BFFKey, BFFOperation]:
        with self._lock:
            if modules_root is not None:
                self.root = canonical_root(modules_root)
            cache: BFFManifestCache = {}
            if not self.loaded and self.prebuilt_manifest and self._manifest_loader is get_bff_manifest:
                from pytincture.backend.bff_manifest import load_bff_manifest
                manifest_path = self.prebuilt_manifest
                if not os.path.isabs(manifest_path):
                    manifest_path = os.path.join(self.root, manifest_path)
                cache = load_bff_manifest(manifest_path)
            self.operations, self.failures = _scan_bff_registry(
                self.root, self._manifest_loader, manifest_cache=cache
            )
            self._manifest_cache = cache
            self.loaded = True
            return self.operations

    def refresh(self, modules_root: str) -> dict[BFFKey, BFFOperation]:
        """Recheck source bytes, reusing manifests only for unchanged files.

        Discover additions/deletions too, so startup policy validation cannot
        overlook edits made after construction. Explicit reload still reparses.
        Custom loaders retain their existing full-reload behavior.
        """
        with self._lock:
            root = canonical_root(modules_root)
            if root != self.root or not self.loaded or self._manifest_loader is not get_bff_manifest:
                return self.reload(root)
            cache = self._manifest_cache.copy()
            self.operations, self.failures = _scan_bff_registry(
                root, self._manifest_loader, manifest_cache=cache
            )
            self._manifest_cache = cache
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
            if not self.revalidate:
                # Opt-in immutable-code mode skips registry freshness only.
                # Execution must still verify the source against this digest.
                return operation
            try:
                current = read_contained_file(root, key[0])
            except (OSError, UnsafePath):
                self.reload(root)
                return None
            if current.digest != operation.get("_source_digest"):
                self.reload(root)
                operation = self.operations.get(key)
            return operation
