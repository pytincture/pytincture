"""Collision-safe loading of application source modules."""

import hashlib
import importlib
import importlib.util
import os
import re
import sys
from importlib.machinery import ModuleSpec, SourceFileLoader

from pytincture.backend.safe_paths import (
    canonical_root,
    decode_python_source,
    read_contained_file,
)


def build_dynamic_module_name(
    file_path: str,
    name_hint: str,
    modules_root: str,
) -> str:
    """Build a stable, collision-resistant name for a source file."""
    absolute_path = os.path.abspath(file_path)
    root = os.path.abspath(modules_root or os.getcwd())
    try:
        relative_path = os.path.relpath(absolute_path, root)
    except ValueError:
        relative_path = os.path.basename(absolute_path)
    if relative_path.startswith(".."):
        relative_path = os.path.basename(absolute_path)

    sanitized_hint = re.sub(r"[^0-9a-zA-Z_]+", "_", name_hint).strip("_") or "module"
    sanitized_path = (
        re.sub(r"[^0-9a-zA-Z_]+", "_", relative_path.replace("\\", "/")).strip("_")
        or "source"
    )
    path_hash = hashlib.sha256(absolute_path.encode("utf-8")).hexdigest()[:12]
    return f"pytincture_dynamic_{sanitized_hint}_{sanitized_path}_{path_hash}"


def load_source_module(
    file_path: str,
    name_hint: str,
    modules_root: str,
    *,
    expected_digest: str | None = None,
):
    """Load source with import-compatible temporary ``sys.modules`` registration."""
    root = canonical_root(modules_root or os.getcwd())
    relative_path = os.path.relpath(os.path.abspath(file_path), root).replace(os.sep, "/")
    secure_file = read_contained_file(root, relative_path)
    if expected_digest is not None and secure_file.digest != expected_digest:
        raise ImportError("BFF source changed after registry discovery")

    # Give relative imports a real package context without putting app roots on
    # process-global sys.path or mixing same-named packages from different apps.
    namespace = "_pytincture_modules_" + hashlib.sha256(root.encode()).hexdigest()[:16]
    if namespace not in sys.modules:
        namespace_spec = ModuleSpec(namespace, loader=None, is_package=True)
        namespace_spec.submodule_search_locations = [root]
        sys.modules.setdefault(namespace, importlib.util.module_from_spec(namespace_spec))
    directories = relative_path.split("/")[:-1]
    package_name = ".".join([namespace, *directories])
    is_package = os.path.basename(secure_file.path) == "__init__.py"
    parent_name = package_name.rpartition(".")[0] if is_package and directories else package_name
    importlib.import_module(parent_name)
    module_name = (
        package_name if is_package else
        f"{package_name}.{build_dynamic_module_name(secure_file.path, name_hint, root)}"
    )
    loader = SourceFileLoader(module_name, secure_file.path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise ImportError(f"Unable to create import spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        source = decode_python_source(secure_file.content)
        exec(compile(source, secure_file.path, "exec"), module.__dict__)
    except Exception:
        if previous_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module
        raise
    return module
