"""Collision-safe loading of application source modules."""

import hashlib
import importlib.util
import os
import re
import sys
from importlib.machinery import SourceFileLoader


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
    path_hash = hashlib.sha1(absolute_path.encode("utf-8")).hexdigest()[:12]
    return f"pytincture_dynamic_{sanitized_hint}_{sanitized_path}_{path_hash}"


def load_source_module(file_path: str, name_hint: str, modules_root: str):
    """Load source with import-compatible temporary ``sys.modules`` registration."""
    module_name = build_dynamic_module_name(file_path, name_hint, modules_root)
    loader = SourceFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise ImportError(f"Unable to create import spec for {file_path}")

    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        loader.exec_module(module)
    except Exception:
        if previous_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module
        raise
    return module
