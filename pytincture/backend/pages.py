"""Application-page metadata discovered without executing browser code."""

import ast
import inspect
import logging
import os
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("pytincture.pages")


def find_main_window_subclass(file_path: str, loader: Callable[[str, str], Any]):
    """Return the first direct ``MainWindow`` subclass in an application module."""
    try:
        module_name = os.path.basename(file_path).removesuffix(".py")
        module = loader(file_path, module_name)
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and any(
                base.__name__ == "MainWindow" for base in getattr(obj, "__bases__", ())
            ):
                return name
    except Exception as exc:
        logger.warning("Unable to find MainWindow subclass", exc_info=exc)
    return None


def find_app_string_setting(
    file_path: str,
    assignment_names: Iterable[str],
    config_keys: Iterable[str],
    *,
    source_code: str | None = None,
) -> str | None:
    """Read a literal string setting from app source without importing it."""
    try:
        if source_code is None:
            with open(file_path, encoding="utf-8") as source_file:
                source_code = source_file.read()
        tree = ast.parse(source_code)
    except Exception as exc:
        logger.warning("Unable to read app configuration", exc_info=exc)
        return None

    assignment_names = set(assignment_names)
    config_keys = set(config_keys)

    def extract_string(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in assignment_names:
                if value := extract_string(node.value):
                    return value
            if target.id == "APP_CONFIG" and isinstance(node.value, ast.Dict):
                for key, value_node in zip(node.value.keys, node.value.values):
                    if extract_string(key) in config_keys:
                        if value := extract_string(value_node):
                            return value
    return None


def normalize_app_asset_path(value: str | None) -> str | None:
    """Normalize a relative appcode asset and reject URLs or traversal."""
    if not value:
        return None
    candidate = value.strip()
    if candidate.startswith("/appcode/"):
        candidate = candidate[len("/appcode/") :]
    elif candidate.startswith("appcode/"):
        candidate = candidate[len("appcode/") :]
    elif candidate.startswith("/"):
        return None

    parsed = urlsplit(candidate)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in candidate
    ):
        return None
    segments = candidate.split("/")
    if any(not segment or segment in (".", "..") for segment in segments):
        return None
    return "/".join(segments)


def find_app_favicon(file_path: str, *, source_code: str | None = None) -> str | None:
    """Resolve an explicit favicon or a conventional favicon directory."""
    configured = find_app_string_setting(
        file_path,
        assignment_names=("APP_FAVICON",),
        config_keys=("favicon",),
        source_code=source_code,
    )
    if normalized := normalize_app_asset_path(configured):
        return normalized

    app_root = os.path.dirname(os.fspath(file_path))
    application = os.path.splitext(os.path.basename(os.fspath(file_path)))[0]
    for candidate in (f"favicon/{application}", "favicon"):
        candidate_path = os.path.join(app_root, *candidate.split("/"))
        if os.path.isdir(candidate_path) and not os.path.islink(candidate_path):
            return candidate
    return None
