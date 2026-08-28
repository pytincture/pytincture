"""Backend-for-frontend operation discovery and registry state."""

import os
from collections.abc import Callable, Mapping
from typing import Any

from pytincture.dataclass import get_bff_manifest

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


def build_bff_registry(
    modules_root: str,
    manifest_loader: Callable[
        [str], Mapping[tuple[str, str], BFFOperation]
    ] = get_bff_manifest,
) -> dict[BFFKey, BFFOperation]:
    """Discover exported BFF operations without importing application code."""
    root_path = os.path.abspath(modules_root)
    registry: dict[BFFKey, BFFOperation] = {}
    if not os.path.isdir(root_path):
        return registry

    for root, dirs, files in os.walk(root_path):
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".") and directory not in _EXCLUDED_DIRECTORIES
        ]
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("."):
                continue
            file_path = os.path.join(root, filename)
            relative_path = os.path.relpath(file_path, root_path).replace(os.sep, "/")
            try:
                file_manifest = manifest_loader(file_path)
            except (OSError, SyntaxError, ValueError) as exc:
                raise RuntimeError(
                    f"Unable to build BFF manifest for {relative_path}"
                ) from exc
            for (class_name, function_name), operation in file_manifest.items():
                registry[(relative_path, class_name, function_name)] = operation
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
        self.root = os.path.abspath(modules_root)
        self.operations: dict[BFFKey, BFFOperation] = {}
        self.loaded = False
        if autoload:
            self.reload()

    def reload(self, modules_root: str | None = None) -> dict[BFFKey, BFFOperation]:
        if modules_root is not None:
            self.root = os.path.abspath(modules_root)
        self.operations = build_bff_registry(self.root, self._manifest_loader)
        self.loaded = True
        return self.operations

    def operation(
        self,
        modules_root: str,
        relative_path: str,
        class_name: str,
        function_name: str,
    ) -> BFFOperation | None:
        root = os.path.abspath(modules_root)
        if root != self.root or not self.loaded:
            self.reload(root)
        return self.operations.get(
            (relative_path.replace(os.sep, "/"), class_name, function_name)
        )
