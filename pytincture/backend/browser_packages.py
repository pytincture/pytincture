"""Discovery and construction of browser-safe application archives."""

import ast
import fnmatch
import importlib.metadata as importlib_metadata
import io
import json
import os
import re
import sys
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from pytincture.configuration import get_runtime_env
from pytincture.dataclass import has_bff_export_class
from pytincture.backend.safe_paths import (
    UnsafePath,
    canonical_root,
    decode_python_source,
    normalize_relative_path,
    read_contained_file,
    resolve_contained_path,
    stat_contained_file,
    validate_application_name,
)

_EXCLUDED_DIRECTORIES = {
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
}

_SENSITIVE_BROWSER_SUFFIXES = frozenset({
    ".bak",
    ".backup",
    ".db",
    ".dump",
    ".jks",
    ".key",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
})
_SENSITIVE_BROWSER_NAMES = frozenset({
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets",
    "secrets.json",
})
_SENSITIVE_BROWSER_NAME_TOKENS = frozenset({
    "credential",
    "credentials",
    "privatekey",
    "secret",
    "secrets",
})


@dataclass(frozen=True, slots=True)
class _AppcodeArchiveCacheEntry:
    value: bytes
    source_fingerprint: tuple[tuple[Any, ...], ...]
    directory_fingerprint: tuple[tuple[Any, ...], ...]


def _directory_fingerprint(modules_root: str) -> tuple[tuple[Any, ...], ...]:
    """Record relevant directory identities without opening file contents."""
    root = canonical_root(modules_root)
    fingerprint: list[tuple[Any, ...]] = []
    for current, directories, _files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if not directory.startswith(".")
            and directory not in _EXCLUDED_DIRECTORIES
            and not os.path.islink(os.path.join(current, directory))
        )
        metadata = os.stat(current, follow_symlinks=False)
        relative = os.path.relpath(current, root).replace(os.sep, "/")
        fingerprint.append(
            (
                "" if relative == "." else relative,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        )
    return tuple(fingerprint)


def _source_fingerprint_is_current(
    modules_root: str,
    fingerprint: tuple[tuple[Any, ...], ...],
) -> bool:
    for (
        relative_path,
        device,
        inode,
        size,
        modified_ns,
        changed_ns,
        _digest,
    ) in fingerprint:
        try:
            current = stat_contained_file(modules_root, relative_path)
        except (OSError, UnsafePath):
            return False
        if current.identity != (device, inode, size, modified_ns, changed_ns):
            return False
    return True


class AppcodeArchiveCache:
    """Bounded per-worker cache for public, non-session-specific archives."""

    def __init__(self, max_entries: int, max_bytes: int = 128 * 1024 * 1024):
        if max_entries <= 0:
            raise ValueError("archive cache size must be greater than zero")
        if max_bytes <= 0:
            raise ValueError("archive cache byte limit must be greater than zero")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries: OrderedDict[
            tuple[Any, ...], _AppcodeArchiveCacheEntry
        ] = OrderedDict()
        self._current_bytes = 0
        self._lock = threading.Lock()

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    def _remove_locked(self, key: tuple[Any, ...]) -> None:
        removed = self._entries.pop(key, None)
        if removed is not None:
            self._current_bytes -= len(removed.value)

    def get(self, key: tuple[Any, ...], modules_root: str) -> bytes | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        try:
            current = (
                _source_fingerprint_is_current(
                    modules_root, entry.source_fingerprint
                )
                and _directory_fingerprint(modules_root)
                == entry.directory_fingerprint
            )
        except (OSError, UnsafePath):
            current = False
        with self._lock:
            if self._entries.get(key) is not entry:
                return None
            if not current:
                self._remove_locked(key)
                return None
            self._entries.move_to_end(key)
            return entry.value

    def put(
        self,
        key: tuple[Any, ...],
        value: bytes,
        *,
        modules_root: str,
        source_fingerprint: tuple[tuple[Any, ...], ...],
    ) -> None:
        if len(value) > self.max_bytes:
            with self._lock:
                self._remove_locked(key)
            return
        try:
            if not _source_fingerprint_is_current(modules_root, source_fingerprint):
                return
            directory_fingerprint = _directory_fingerprint(modules_root)
        except (OSError, UnsafePath):
            return
        entry = _AppcodeArchiveCacheEntry(
            value=value,
            source_fingerprint=source_fingerprint,
            directory_fingerprint=directory_fingerprint,
        )
        with self._lock:
            self._remove_locked(key)
            self._entries[key] = entry
            self._current_bytes += len(value)
            self._entries.move_to_end(key)
            while (
                len(self._entries) > self.max_entries
                or self._current_bytes > self.max_bytes
            ):
                _evicted_key, evicted = self._entries.popitem(last=False)
                self._current_bytes -= len(evicted.value)


def _analyze_local_python_module(
    file_path: str,
    modules_root: str,
    secure_files: dict[str, Any] | None = None,
    max_file_bytes: int | None = None,
    searched_directories: set[str] | None = None,
) -> tuple[bool, set[str]]:
    """Return the BFF-boundary flag and direct local Python imports."""
    try:
        root = canonical_root(modules_root)
        file_relative = os.path.relpath(file_path, root).replace(os.sep, "/")
        source_file = None if secure_files is None else secure_files.get(file_path)
        if source_file is None:
            source_file = read_contained_file(
                root, file_relative, max_bytes=max_file_bytes
            )
            if secure_files is not None:
                secure_files[file_path] = source_file
        source = decode_python_source(source_file.content)
        tree = ast.parse(source, filename=source_file.path)
    except UnsafePath:
        raise
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False, set()

    # The packaged form of a BFF module is a browser proxy. Its imports belong
    # to the server implementation and must not expand the browser dependency
    # graph. A module imported independently by browser code is still found
    # through that independent graph edge.
    if has_bff_export_class(source_file.path, source=source):
        return True, set()

    discovered: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_parts = file_relative.split("/")[:-1]
                climb = node.level - 1
                if climb > len(relative_parts):
                    continue
                package_parts = relative_parts[: len(relative_parts) - climb]
                if node.module:
                    candidates.append(".".join([*package_parts, *node.module.split(".")]))
                else:
                    candidates.extend(
                        ".".join([*package_parts, alias.name]) for alias in node.names
                    )
            elif node.module:
                candidates.append(node.module)
        for module_name in candidates:
            module_relative = module_name.replace(".", os.sep)
            for candidate in (
                os.path.join(modules_root, f"{module_relative}.py"),
                os.path.join(modules_root, module_relative, "__init__.py"),
            ):
                if searched_directories is not None:
                    parent = os.path.dirname(candidate)
                    while parent != root and not os.path.isdir(parent):
                        parent = os.path.dirname(parent)
                    if os.path.isdir(parent) and not os.path.islink(parent):
                        searched_directories.add(os.path.realpath(parent))
                candidate_relative = os.path.relpath(candidate, root).replace(os.sep, "/")
                try:
                    discovered.add(resolve_contained_path(root, candidate_relative))
                except UnsafePath:
                    if os.path.lexists(candidate):
                        raise
                    continue
    return False, discovered


def local_python_imports(file_path: str, modules_root: str) -> set[str]:
    """Return browser imports, stopping at a proven BFF module boundary."""
    return _analyze_local_python_module(file_path, modules_root)[1]


def _browser_package_initializers(file_path: str, modules_root: str) -> set[str]:
    """Return package initializers implicitly executed by a browser module."""
    root = canonical_root(modules_root)
    current = os.path.realpath(file_path)
    parent = os.path.dirname(current)
    initializers: set[str] = set()
    while parent != root and os.path.commonpath((root, parent)) == root:
        package_init = os.path.join(parent, "__init__.py")
        relative = os.path.relpath(package_init, root).replace(os.sep, "/")
        try:
            resolved = resolve_contained_path(root, relative)
        except UnsafePath:
            resolved = ""
        if resolved and resolved != current:
            initializers.add(resolved)
        parent = os.path.dirname(parent)
    return initializers


def browser_asset_path_is_safe(relative_path: str) -> bool:
    """Reject paths that should never enter or be served from browser files."""
    try:
        normalized = normalize_relative_path(relative_path)
    except UnsafePath:
        return False
    parts = normalized.split("/")
    if any(
        part.startswith(".") or part in _EXCLUDED_DIRECTORIES
        for part in parts
    ):
        return False

    filename = parts[-1].casefold()
    if filename.startswith(".env") or filename in _SENSITIVE_BROWSER_NAMES:
        return False
    if any(filename.endswith(suffix) for suffix in _SENSITIVE_BROWSER_SUFFIXES):
        return False
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", filename)
        if token
    }
    return not bool(tokens & _SENSITIVE_BROWSER_NAME_TOKENS)


def _literal_module_metadata(
    file_path: str,
    modules_root: str,
) -> tuple[str | None, str | None]:
    try:
        relative = os.path.relpath(file_path, modules_root).replace(os.sep, "/")
        source_file = read_contained_file(modules_root, relative)
        tree = ast.parse(
            decode_python_source(source_file.content), filename=source_file.path
        )
    except (OSError, SyntaxError, UnicodeDecodeError, UnsafePath):
        return None, None
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {
                "__widgetset__",
                "__version__",
            }:
                values[target.id] = node.value.value
    return values.get("__widgetset__"), values.get("__version__")


def _literal_external_metadata(file_path: Path) -> tuple[str | None, str | None]:
    """Read bounded literal metadata from installed source without importing it."""

    try:
        if not file_path.is_file() or file_path.stat().st_size > 256 * 1024:
            return None, None
        with file_path.open("rb") as source_file:
            content = source_file.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            return None, None
        tree = ast.parse(decode_python_source(content), filename=str(file_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None, None
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {
                "__widgetset__",
                "__version__",
            }:
                values[target.id] = node.value.value
    return values.get("__widgetset__"), values.get("__version__")


def _valid_widget_spec(widgetset: object, version: object) -> str | None:
    if not isinstance(widgetset, str) or not widgetset.strip():
        return None
    try:
        canonicalize_name(widgetset.strip(), validate=True)
    except ValueError:
        return None
    if version is None:
        return widgetset.strip()
    if not isinstance(version, str):
        return None
    try:
        Version(version.strip())
    except InvalidVersion:
        return None
    return f"{widgetset.strip()}=={version.strip()}"


def _installed_widget_metadata(
    module_name: str,
    distribution_names: tuple[str, ...] | list[str],
) -> str | None:
    """Inspect distribution metadata/source without executing package code."""

    discovered_specs: set[str] = set()
    for distribution_name in distribution_names:
        try:
            distribution = importlib_metadata.distribution(distribution_name)
        except importlib_metadata.PackageNotFoundError:
            continue
        declared_widgetset = distribution.metadata.get("Pytincture-Widgetset")
        if declared_widgetset:
            spec = _valid_widget_spec(declared_widgetset, distribution.version)
            if spec:
                discovered_specs.add(spec)
                continue
        owned_files = {
            str(path).replace("\\", "/"): path
            for path in distribution.files or ()
        }
        for relative in (f"{module_name}/__init__.py", f"{module_name}.py"):
            owned_path = owned_files.get(relative)
            if owned_path is None:
                continue
            widgetset, _declared_version = _literal_external_metadata(
                Path(distribution.locate_file(owned_path))
            )
            spec = _valid_widget_spec(widgetset, distribution.version)
            if spec:
                discovered_specs.add(spec)
                break
    if len(discovered_specs) == 1:
        return discovered_specs.pop()
    if discovered_specs:
        # Multiple distributions claiming one import package are ambiguous.
        return None

    # Some editable/legacy packages do not expose top-level distribution
    # metadata. PathFinder locates a top-level module without executing it.
    try:
        spec = PathFinder.find_spec(module_name)
    except (AttributeError, ImportError, ValueError):
        spec = None
    if (
        spec is not None
        and spec.origin
        and spec.origin not in {"built-in", "frozen"}
    ):
        widgetset, version = _literal_external_metadata(Path(spec.origin))
        return _valid_widget_spec(widgetset, version)
    return None


def discover_widgetset(
    application: str,
    modules_root: str,
) -> str:
    """Discover widget metadata without importing browser packages on the server."""
    try:
        sanitized_application = validate_application_name(application)
    except ValueError:
        return ""
    root = canonical_root(modules_root)
    try:
        source_file = read_contained_file(root, f"{sanitized_application}.py")
        app_file_path = source_file.path
        tree = ast.parse(
            decode_python_source(source_file.content), filename=app_file_path
        )
    except (OSError, SyntaxError, UnicodeDecodeError, UnsafePath):
        return ""

    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])

    installed_packages: dict[str, list[str]] | None = None
    for module_name in imports:
        local_candidates = (
            os.path.join(root, f"{module_name}.py"),
            os.path.join(root, module_name, "__init__.py"),
        )
        has_local_candidate = any(os.path.lexists(path) for path in local_candidates)
        for candidate in local_candidates:
            try:
                candidate = resolve_contained_path(
                    root,
                    os.path.relpath(candidate, root).replace(os.sep, "/"),
                )
            except UnsafePath:
                continue
            if os.path.isfile(candidate):
                widgetset, version = _literal_module_metadata(candidate, root)
                widget_spec = _valid_widget_spec(widgetset, version)
                if widget_spec:
                    return widget_spec
                break
        if not has_local_candidate:
            if module_name in sys.stdlib_module_names:
                continue
            if installed_packages is None:
                try:
                    installed_packages = importlib_metadata.packages_distributions()
                except Exception:
                    installed_packages = {}
            widget_spec = _installed_widget_metadata(
                module_name,
                installed_packages.get(module_name, ()),
            )
            if widget_spec:
                return widget_spec
    return ""


def configured_browser_files(
    modules_root: str,
    raw_patterns: str | None = None,
    max_files: int | None = None,
    *,
    max_directories: int | None = None,
    max_scanned_files: int | None = None,
    _scanned_directories: set[str] | None = None,
) -> set[str]:
    """Resolve explicitly configured browser-file globs inside ``modules_root``."""
    modules_root = canonical_root(modules_root)
    patterns = configured_browser_file_patterns(raw_patterns)
    if not patterns:
        return set()

    selected: set[str] = set()
    scanned_files = 0
    scanned_directories = (
        _scanned_directories if _scanned_directories is not None else set()
    )
    scan_limit = (
        max_scanned_files
        if max_scanned_files is not None
        else max_files * 100 if max_files is not None else None
    )
    for root, dirs, files in os.walk(modules_root):
        scanned_directories.add(root)
        if (
            max_directories is not None
            and len(scanned_directories) > max_directories
        ):
            raise HTTPException(
                status_code=413,
                detail="Appcode configured-directory scan limit exceeded",
            )
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".") and directory not in _EXCLUDED_DIRECTORIES
            and not os.path.islink(os.path.join(root, directory))
        ]
        for filename in files:
            scanned_files += 1
            if scan_limit is not None and scanned_files > scan_limit:
                raise HTTPException(
                    status_code=413,
                    detail="Appcode configured-file scan limit exceeded",
                )
            absolute = os.path.abspath(os.path.join(root, filename))
            if os.path.islink(absolute):
                continue
            relative = os.path.relpath(absolute, modules_root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                if not browser_asset_path_is_safe(relative):
                    raise RuntimeError(
                        "PYTINCTURE_BROWSER_FILES selects a hidden or sensitive path"
                    )
                selected.add(absolute)
                if max_files is not None and len(selected) > max_files:
                    raise HTTPException(
                        status_code=413,
                        detail="Appcode file-count limit exceeded",
                    )
    return selected


def configured_browser_file_patterns(
    raw_patterns: str | None = None,
) -> tuple[str, ...]:
    """Parse the browser-file glob setting without scanning the module root."""
    raw = (
        get_runtime_env("PYTINCTURE_BROWSER_FILES", "")
        if raw_patterns is None
        else raw_patterns
    ).strip()
    if not raw:
        return ()
    try:
        patterns = json.loads(raw)
    except json.JSONDecodeError:
        patterns = [value.strip() for value in raw.split(",") if value.strip()]
    if not isinstance(patterns, list) or any(
        not isinstance(value, str) for value in patterns
    ):
        raise RuntimeError(
            "PYTINCTURE_BROWSER_FILES must be a JSON list or comma-separated globs"
        )
    return tuple(patterns)


def configured_browser_asset_path_selected(
    relative_path: str,
    raw_patterns: str | None = None,
) -> bool:
    """Return whether a directly served asset is in the configured browser set."""
    if not browser_asset_path_is_safe(relative_path):
        return False
    normalized = normalize_relative_path(relative_path)
    return any(
        fnmatch.fnmatch(normalized, pattern)
        for pattern in configured_browser_file_patterns(raw_patterns)
    )


def browser_package_files(
    application: str,
    modules_root: str,
    raw_patterns: str | None = None,
    max_files: int | None = None,
    *,
    _secure_files: dict[str, Any] | None = None,
    _max_file_bytes: int | None = None,
    _max_directories: int | None = None,
    _max_scanned_files: int | None = None,
    _scanned_directories: set[str] | None = None,
) -> set[str]:
    """Return the transitive local imports and explicit files for an app."""
    try:
        application = validate_application_name(application)
        root = canonical_root(modules_root)
        entrypoint = resolve_contained_path(root, f"{application}.py")
    except (UnsafePath, ValueError):
        raise HTTPException(status_code=404, detail="Application entrypoint not found")

    selected = {entrypoint}
    pending = [entrypoint]
    while pending:
        try:
            current = pending.pop()
            is_bff_module, imported_files = _analyze_local_python_module(
                current,
                root,
                secure_files=_secure_files,
                max_file_bytes=_max_file_bytes,
                searched_directories=_scanned_directories,
            )
            if (
                _max_directories is not None
                and _scanned_directories is not None
                and len(_scanned_directories) > _max_directories
            ):
                raise HTTPException(
                    status_code=413,
                    detail="Appcode import-directory scan limit exceeded",
                )
            if not is_bff_module:
                imported_files |= _browser_package_initializers(current, root)
        except UnsafePath as exc:
            if "size limit" in str(exc):
                raise HTTPException(
                    status_code=413, detail="Appcode file-size limit exceeded"
                ) from exc
            raise HTTPException(
                status_code=404, detail="Application import path is unsafe"
            ) from exc
        for imported in imported_files:
            if imported not in selected:
                selected.add(imported)
                if max_files is not None and len(selected) > max_files:
                    raise HTTPException(status_code=413, detail="Appcode file-count limit exceeded")
                pending.append(imported)
    selected |= configured_browser_files(
        root,
        raw_patterns,
        max_files=max_files,
        max_directories=_max_directories,
        max_scanned_files=_max_scanned_files,
        _scanned_directories=_scanned_directories,
    )
    if max_files is not None and len(selected) > max_files:
        raise HTTPException(status_code=413, detail="Appcode file-count limit exceeded")
    return selected


def create_appcode_archive(
    host: str,
    protocol: str,
    application: str,
    modules_root: str,
    parser: Callable[..., Any],
    replay_client: Any = None,
    raw_patterns: str | None = None,
    *,
    max_files: int = 512,
    max_file_bytes: int = 4 * 1024 * 1024,
    max_total_bytes: int = 32 * 1024 * 1024,
    cache: AppcodeArchiveCache | None = None,
) -> io.BytesIO:
    """Build an explicit browser-safe application archive in memory."""
    try:
        application = validate_application_name(application)
        root = canonical_root(modules_root)
    except (UnsafePath, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Application entrypoint not found") from exc
    cache_key = (
        application,
        host,
        protocol,
        raw_patterns or "",
        max_files,
        max_file_bytes,
        max_total_bytes,
    )
    if cache is not None and replay_client is None:
        cached = cache.get(cache_key, root)
        if cached is not None:
            return io.BytesIO(cached)

    discovered_secure_files: dict[str, Any] = {}
    selected = sorted(
        browser_package_files(
            application,
            root,
            raw_patterns,
            max_files=max_files,
            _secure_files=discovered_secure_files,
            _max_file_bytes=max_file_bytes,
        )
    )
    fingerprint: list[tuple[Any, ...]] = []
    secure_files = []
    aggregate_bytes = 0
    for file_path in selected:
        relative_path = os.path.relpath(file_path, root).replace(os.sep, "/")
        try:
            secure_file = discovered_secure_files.get(file_path)
            if secure_file is None:
                secure_file = read_contained_file(
                    root, relative_path, max_bytes=max_file_bytes
                )
        except UnsafePath as exc:
            detail = (
                "Appcode file-size limit exceeded"
                if "size limit" in str(exc)
                else "Appcode file is unsafe"
            )
            raise HTTPException(status_code=413, detail=detail) from exc
        aggregate_bytes += secure_file.size
        if aggregate_bytes > max_total_bytes:
            raise HTTPException(status_code=413, detail="Appcode aggregate-size limit exceeded")
        fingerprint.append(
            (
                secure_file.relative_path,
                secure_file.device,
                secure_file.inode,
                secure_file.size,
                secure_file.modified_ns,
                secure_file.changed_ns,
                secure_file.digest,
            )
        )
        secure_files.append(secure_file)

    in_memory_zip = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
        output_bytes = 0
        for secure_file in secure_files:
            arcname = secure_file.relative_path
            if secure_file.path.endswith(".py"):
                file_contents = parser(
                    secure_file.path,
                    host,
                    protocol,
                    application=application,
                    replay_client=replay_client,
                    source_code=decode_python_source(secure_file.content),
                )
                payload = (file_contents or "").encode("utf-8")
            else:
                payload = secure_file.content
            if len(payload) > max_file_bytes:
                raise HTTPException(status_code=413, detail="Appcode generated file-size limit exceeded")
            output_bytes += len(payload)
            if output_bytes > max_total_bytes:
                raise HTTPException(status_code=413, detail="Appcode generated-size limit exceeded")
            zip_file.writestr(arcname, payload)
    in_memory_zip.seek(0)
    if cache is not None and replay_client is None:
        cache.put(
            cache_key,
            in_memory_zip.getvalue(),
            modules_root=root,
            source_fingerprint=tuple(fingerprint),
        )
    return in_memory_zip
