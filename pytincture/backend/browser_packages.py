"""Discovery and construction of browser-safe application archives."""

import ast
import fnmatch
import importlib
import io
import json
import os
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from pytincture.backend.safe_paths import (
    UnsafePath,
    canonical_root,
    decode_python_source,
    read_contained_file,
    resolve_contained_path,
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


class AppcodeArchiveCache:
    """Bounded per-worker cache for public, non-session-specific archives."""

    def __init__(self, max_entries: int):
        if max_entries <= 0:
            raise ValueError("archive cache size must be greater than zero")
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> bytes | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[Any, ...], value: bytes) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


def local_python_imports(file_path: str, modules_root: str) -> set[str]:
    """Return local Python files directly imported by a browser module."""
    try:
        root = canonical_root(modules_root)
        relative = os.path.relpath(file_path, root).replace(os.sep, "/")
        source_file = read_contained_file(root, relative)
        tree = ast.parse(
            decode_python_source(source_file.content), filename=source_file.path
        )
    except UnsafePath:
        raise
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    discovered: set[str] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            candidates.append(node.module)
        for module_name in candidates:
            relative = module_name.replace(".", os.sep)
            for candidate in (
                os.path.join(modules_root, f"{relative}.py"),
                os.path.join(modules_root, relative, "__init__.py"),
            ):
                candidate_relative = os.path.relpath(candidate, root).replace(os.sep, "/")
                try:
                    discovered.add(resolve_contained_path(root, candidate_relative))
                except UnsafePath:
                    if os.path.lexists(candidate):
                        raise
                    continue
    return discovered


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


def discover_widgetset(
    application: str,
    modules_root: str,
    importer: Callable[[str], Any] = importlib.import_module,
) -> str:
    """Discover widget metadata while avoiding imports for local application modules."""
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
                if widgetset:
                    return widgetset + (f"=={version}" if version else "")
                break
        if not has_local_candidate:
            try:
                module = importer(module_name)
            except ModuleNotFoundError:
                continue
            widgetset = getattr(module, "__widgetset__", None)
            if widgetset:
                version = getattr(module, "__version__", None)
                return widgetset + (f"=={version}" if version else "")
    return ""


def configured_browser_files(
    modules_root: str,
    raw_patterns: str | None = None,
    max_files: int | None = None,
) -> set[str]:
    """Resolve explicitly configured browser-file globs inside ``modules_root``."""
    modules_root = canonical_root(modules_root)
    raw = (
        os.getenv("PYTINCTURE_BROWSER_FILES", "")
        if raw_patterns is None
        else raw_patterns
    ).strip()
    if not raw:
        return set()
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

    selected: set[str] = set()
    scanned_files = 0
    for root, dirs, files in os.walk(modules_root):
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".") and directory not in _EXCLUDED_DIRECTORIES
            and not os.path.islink(os.path.join(root, directory))
        ]
        for filename in files:
            scanned_files += 1
            if max_files is not None and scanned_files > max_files * 100:
                raise HTTPException(
                    status_code=413,
                    detail="Appcode configured-file scan limit exceeded",
                )
            absolute = os.path.abspath(os.path.join(root, filename))
            if os.path.islink(absolute):
                continue
            relative = os.path.relpath(absolute, modules_root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                selected.add(absolute)
                if max_files is not None and len(selected) > max_files:
                    raise HTTPException(
                        status_code=413,
                        detail="Appcode file-count limit exceeded",
                    )
    return selected


def browser_package_files(
    application: str,
    modules_root: str,
    raw_patterns: str | None = None,
    max_files: int | None = None,
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
            imported_files = local_python_imports(pending.pop(), root)
        except UnsafePath as exc:
            raise HTTPException(
                status_code=404, detail="Application import path is unsafe"
            ) from exc
        for imported in imported_files:
            if imported not in selected:
                selected.add(imported)
                if max_files is not None and len(selected) > max_files:
                    raise HTTPException(status_code=413, detail="Appcode file-count limit exceeded")
                pending.append(imported)
    for python_file in tuple(selected):
        parent = os.path.dirname(python_file)
        while parent != root and os.path.commonpath((root, parent)) == root:
            package_init = os.path.join(parent, "__init__.py")
            try:
                selected.add(
                    resolve_contained_path(
                        root,
                        os.path.relpath(package_init, root).replace(os.sep, "/"),
                    )
                )
            except UnsafePath:
                pass
            parent = os.path.dirname(parent)
    selected |= configured_browser_files(root, raw_patterns, max_files=max_files)
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
    selected = sorted(
        browser_package_files(application, root, raw_patterns, max_files=max_files)
    )
    fingerprint: list[tuple[str, int, int, str]] = []
    secure_files = []
    aggregate_bytes = 0
    for file_path in selected:
        relative_path = os.path.relpath(file_path, root).replace(os.sep, "/")
        try:
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
                secure_file.size,
                secure_file.modified_ns,
                secure_file.digest,
            )
        )
        secure_files.append(secure_file)

    cache_key = (
        application,
        host,
        protocol,
        raw_patterns or "",
        tuple(fingerprint),
    )
    if cache is not None and replay_client is None:
        cached = cache.get(cache_key)
        if cached is not None:
            return io.BytesIO(cached)

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
        cache.put(cache_key, in_memory_zip.getvalue())
    return in_memory_zip
