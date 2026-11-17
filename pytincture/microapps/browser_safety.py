"""Reject private implementation artifacts before publishing browser bytes.

This is a static packaging boundary, not a sandbox or an approval service.
Browser code is public code and still requires review before publication.
"""
from __future__ import annotations

import ast
import hashlib
import io
from pathlib import PurePosixPath
import stat
import tokenize
import zipfile

from pytincture.backend.browser_packages import browser_asset_path_is_safe
from pytincture.dataclass import has_bff_export_class
from pytincture.packaged_apps import _relative

_OPAQUE_SUFFIXES = ('.pyc', '.pyo', '.mpy', '.so', '.pyd', '.dll', '.dylib',
                    '.whl', '.zip', '.pyt', '.pyz', '.egg', '.tar', '.tar.gz',
                    '.tar.bz2', '.tar.xz', '.tgz', '.tbz2', '.txz')
_BFF_MARKERS = {'backend_for_frontend', 'bff_http_methods', 'bff_policy', 'bff_stream', 'bff_external'}
_MAX_SOURCE = 4 * 1024 * 1024
_MAX_ARCHIVE = 256 * 1024 * 1024


def _python_tree(name, data):
    if len(data) > _MAX_SOURCE:
        raise ValueError('Browser Python source exceeds 4 MiB')
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        source = data.decode(encoding)
        return source, ast.parse(source, filename=name, feature_version=(3, 13))
    except (SyntaxError, UnicodeError, LookupError, ValueError):
        raise ValueError('Browser Python source cannot be inspected') from None


def _definitions(tree):
    # Also recognize a copied private class/function after its decorators or
    # comments were removed. Do not confuse empty constructors with private code.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not any(not isinstance(item, ast.Pass) and not (
                isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)) for item in node.body):
                continue
            yield hashlib.sha256(ast.dump(node, include_attributes=False).encode()).digest()


def _without_decorators(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
    return tree


def private_bff_definitions(root):
    """Fingerprint implementations in proven BFF modules without importing them."""
    definitions = set()
    for path in root.rglob('*.py'):
        try:
            source, tree = _python_tree(path.name, path.read_bytes())
        except ValueError:
            # An unused backend dependency may target a different Python syntax.
            # Browser source, in contrast, is always required to be inspectable.
            continue
        if has_bff_export_class(path.name, source=source):
            definitions.update(_definitions(_without_decorators(tree)))
    return definitions


def validate_browser_file(name, data, *, private_definitions=()):
    path = PurePosixPath(name)
    if (not browser_asset_path_is_safe(name) or path.name == 'pytincture-app.json'
            or path.parts[0] == 'pytincture' or name.lower().endswith(_OPAQUE_SUFFIXES)):
        raise ValueError('Browser artifact contains a private or opaque file')
    if not {suffix.lower() for suffix in path.suffixes} & {'.py', '.pyi'}:
        return
    source, tree = _python_tree(name, data)
    if has_bff_export_class(name, source=source):
        raise ValueError('Browser artifact contains a backend BFF implementation')
    for node in ast.walk(tree):
        for decorator in getattr(node, 'decorator_list', ()):
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            marker = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) else None
            if marker in _BFF_MARKERS:
                raise ValueError('Browser artifact contains a backend BFF marker')
        modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                   else [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else [])
        if any(module == 'pytincture' or module.startswith('pytincture.') for module in modules):
            raise ValueError('Browser artifact imports the server-side Pytincture package')
    if private_definitions and set(_definitions(_without_decorators(tree))) & set(private_definitions):
        raise ValueError('Browser artifact contains a copied private BFF implementation')


def browser_archive_files(path, *, private_root=None):
    """Read a bounded, inspected wheel or aggregate; never execute its contents."""
    private = private_bff_definitions(private_root) if private_root is not None else set()
    files = {}
    total = 0
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > 10000:
            raise ValueError('Browser archive contains too many files')
        for entry in archive.infolist():
            name = entry.filename.rstrip('/')
            _relative(name)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Browser archive symlinks are forbidden')
            if entry.is_dir():
                continue
            if name in files:
                raise ValueError('Browser archive contains duplicate files')
            total += entry.file_size
            if total > _MAX_ARCHIVE:
                raise ValueError('Browser archive exceeds 256 MiB')
            data = archive.read(entry)
            validate_browser_file(name, data, private_definitions=private)
            files[name] = data
    return files


def validate_browser_archive(path, *, private_root=None):
    browser_archive_files(path, private_root=private_root)
