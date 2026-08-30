"""Application-page metadata discovered without executing browser code."""

import ast
import logging
import os
from collections.abc import Iterable
from urllib.parse import urlsplit

logger = logging.getLogger("pytincture.pages")


class EntryPointDiscoveryError(ValueError):
    """The browser entrypoint cannot be determined without executing code."""


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _literal_entrypoint(tree: ast.Module) -> str | None:
    """Return explicit literal entrypoint metadata, if declared."""
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "APP_ENTRYPOINT":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
                raise EntryPointDiscoveryError(
                    "APP_ENTRYPOINT must be a literal Python identifier string"
                )
            if target.id != "APP_CONFIG" or not isinstance(value, ast.Dict):
                continue
            for key, config_value in zip(value.keys, value.values):
                if not (
                    isinstance(key, ast.Constant)
                    and key.value == "entrypoint"
                ):
                    continue
                if isinstance(config_value, ast.Constant) and isinstance(
                    config_value.value, str
                ):
                    return config_value.value
                raise EntryPointDiscoveryError(
                    "APP_CONFIG['entrypoint'] must be a literal Python identifier string"
                )
    return None


def _main_window_base_names(tree: ast.Module) -> set[str]:
    """Collect syntactic names that unambiguously refer to MainWindow."""
    names: set[str] = set()
    module_aliases: set[str] = set()
    package_aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for imported in node.names:
                bound = imported.asname or imported.name
                if module == "dhxpyt.layout" and imported.name == "MainWindow":
                    names.add(bound)
                elif module == "dhxpyt" and imported.name == "layout":
                    module_aliases.add(bound)
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "dhxpyt.layout":
                    if imported.asname:
                        module_aliases.add(imported.asname)
                    else:
                        package_aliases.add("dhxpyt")
                elif imported.name == "dhxpyt":
                    package_aliases.add(imported.asname or "dhxpyt")

    names.update(f"{alias}.MainWindow" for alias in module_aliases)
    names.update(f"{alias}.layout.MainWindow" for alias in package_aliases)

    # Preserve simple, static aliases without evaluating assignment expressions.
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value_name = _dotted_name(node.value)
            if value_name not in names:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def find_main_window_subclass(
    file_path: str,
    _legacy_loader=None,
    *,
    source_code: str | None = None,
) -> str | None:
    """Discover a browser entrypoint using source syntax only.

    Explicit ``APP_ENTRYPOINT``/``APP_CONFIG['entrypoint']`` metadata wins.
    Otherwise the first top-level class directly inheriting from a recognized
    ``dhxpyt.layout.MainWindow`` alias is selected in source order.

    ``_legacy_loader`` remains accepted for call compatibility but is never
    invoked: server-side execution is intentionally forbidden.
    """
    try:
        if source_code is None:
            with open(file_path, encoding="utf-8") as source_file:
                source_code = source_file.read()
        tree = ast.parse(source_code, filename=file_path)
        declared = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        explicit = _literal_entrypoint(tree)
        if explicit is not None:
            if not explicit.isascii() or not explicit.isidentifier():
                raise EntryPointDiscoveryError(
                    "The configured browser entrypoint must be a Python identifier"
                )
            if explicit not in declared:
                raise EntryPointDiscoveryError(
                    f"Configured browser entrypoint {explicit!r} is not a top-level class or function"
                )
            return explicit

        main_window_names = _main_window_base_names(tree)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                _dotted_name(base) in main_window_names for base in node.bases
            ):
                return node.name

        # The long-standing service-mode convention needs no type inspection:
        # an application may expose a top-level class/callable matching its
        # module name. This also supports indirect MainWindow subclasses.
        conventional_name = os.path.basename(file_path).removesuffix(".py")
        if conventional_name in declared:
            return conventional_name

        if declared:
            raise EntryPointDiscoveryError(
                "Unable to determine the browser entrypoint statically; declare "
                "APP_ENTRYPOINT = 'Name' using a top-level class or function"
            )
    except EntryPointDiscoveryError:
        raise
    except SyntaxError as exc:
        raise EntryPointDiscoveryError(
            f"Unable to parse browser entrypoint source: {exc.msg}"
        ) from exc
    except Exception as exc:
        raise EntryPointDiscoveryError(
            f"Unable to inspect browser entrypoint source: {exc}"
        ) from exc
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
