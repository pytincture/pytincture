import ast
from decimal import Subnormal
import hashlib
from html import escape
import os
import sys
from typing import Optional
from urllib.parse import quote
import uuid
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import inspect
import textwrap
from typing import Dict, Any, Mapping
from pytincture import get_modules_path
from pytincture.configuration import get_runtime_env

# Global set to track BFF endpoints
bff_routes: Dict[str, Dict] = {}


# Security contract: class-level export is intentional. Static discovery must
# prove these decorators came from Pytincture at this point in source order;
# same-named local/unrelated or subsequently rebound aliases never opt code in.
_PYTINCTURE_DECORATOR_MODULES = frozenset({"pytincture", "pytincture.dataclass"})
_PYTINCTURE_DECORATORS = frozenset({
    "backend_for_frontend",
    "bff_http_methods",
    "bff_policy",
    "bff_stream",
})


class _DecoratorBindings:
    """Known decorator/module bindings at one point in module execution."""

    def __init__(self):
        self.decorators: Dict[str, str] = {}
        self.modules: Dict[str, str] = {}

    def copy(self):
        copied = _DecoratorBindings()
        copied.decorators = dict(self.decorators)
        copied.modules = dict(self.modules)
        return copied

    def invalidate(self, name: str) -> None:
        self.decorators.pop(name, None)
        self.modules.pop(name, None)


class _BoundNameVisitor(ast.NodeVisitor):
    """Collect bindings created by a statement without entering nested scopes."""

    def __init__(self):
        self.names: set[str] = set()
        self.has_star_import = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            dotted_name = _dotted_attribute_name(node)
            if dotted_name:
                self.names.add(dotted_name.split(".", 1)[0])
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.has_star_import = True
            else:
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        self._visit_definition_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)
        self._visit_definition_expressions(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def _visit_definition_expressions(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, node.key, node.value)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.elt)

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        *results: ast.AST,
    ) -> None:
        # Comprehension targets are local to the comprehension, but assignment
        # expressions in its iterable, filters, or result bind in the containing
        # scope. Track those without treating ``for name in ...`` as a module
        # rebind.
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for result in results:
            self.visit(result)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)


def _apply_binding_statement(bindings: _DecoratorBindings, node: ast.stmt) -> None:
    """Apply one definitely executed top-level/class binding statement."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            bindings.invalidate(bound_name)
            if alias.name in _PYTINCTURE_DECORATOR_MODULES:
                bindings.modules[bound_name] = (
                    alias.name if alias.asname else alias.name.split(".", 1)[0]
                )
        return

    if isinstance(node, ast.ImportFrom) and node.level == 0:
        imported_module = node.module or ""
        if any(alias.name == "*" for alias in node.names):
            bindings.decorators.clear()
            bindings.modules.clear()
            return
        for alias in node.names:
            bound_name = alias.asname or alias.name
            bindings.invalidate(bound_name)
            if (
                imported_module in _PYTINCTURE_DECORATOR_MODULES
                and alias.name in _PYTINCTURE_DECORATORS
            ):
                bindings.decorators[bound_name] = f"{imported_module}.{alias.name}"
            elif imported_module == "pytincture" and alias.name == "dataclass":
                bindings.modules[bound_name] = "pytincture.dataclass"
        return

    visitor = _BoundNameVisitor()
    visitor.visit(node)
    if visitor.has_star_import:
        bindings.decorators.clear()
        bindings.modules.clear()
    for name in visitor.names:
        bindings.invalidate(name)


def _binding_snapshots(
    statements: list[ast.stmt],
    initial: _DecoratorBindings | None = None,
) -> Dict[int, _DecoratorBindings]:
    bindings = initial.copy() if initial is not None else _DecoratorBindings()
    snapshots: Dict[int, _DecoratorBindings] = {}
    for statement in statements:
        snapshots[id(statement)] = bindings.copy()
        _apply_binding_statement(bindings, statement)
    return snapshots


def _statement_may_bind_name(node: ast.AST, name: str) -> bool:
    visitor = _BoundNameVisitor()
    visitor.visit(node)
    return visitor.has_star_import or name in visitor.names


def _definition_start_line(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    return min(
        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    )


def _definition_identity(node: ast.AST) -> Dict[str, Any]:
    """Return stable, non-secret source identity for one authorized definition."""
    fingerprint = hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()
    identity: Dict[str, Any] = {
        "line": getattr(node, "lineno", None),
        "end_line": getattr(node, "end_lineno", None),
        "sha256": fingerprint,
    }
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        identity["start_line"] = _definition_start_line(node)
    return identity


def _validate_export_binding(
    statements: list[ast.stmt],
    class_node: ast.ClassDef,
    class_bindings: _DecoratorBindings,
) -> None:
    """Reject source that can replace a statically authorized class binding."""
    duplicate_definitions = [
        statement
        for statement in statements
        if isinstance(statement, ast.ClassDef)
        and statement.name == class_node.name
    ]
    if len(duplicate_definitions) != 1:
        raise ValueError(
            f"exported BFF class {class_node.name!r} has duplicate definitions"
        )

    export_indexes = [
        index
        for index, decorator in enumerate(class_node.decorator_list)
        if _decorator_matches(
            decorator,
            decorator_name="backend_for_frontend",
            bindings=class_bindings,
        )[0]
    ]
    if len(export_indexes) != 1 or export_indexes[0] != 0:
        raise ValueError(
            "backend_for_frontend must be the single outermost export decorator"
        )

    class_index = statements.index(class_node)
    for statement in statements[class_index + 1:]:
        if _statement_may_bind_name(statement, class_node.name):
            raise ValueError(
                f"exported BFF class {class_node.name!r} is rebound after definition"
            )


def _dotted_attribute_name(node: ast.AST) -> Optional[str]:
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _decorator_matches(
    decorator: ast.AST,
    *,
    decorator_name: str,
    bindings: _DecoratorBindings,
) -> tuple[bool, ast.AST]:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator

    if isinstance(target, ast.Name):
        resolved = bindings.decorators.get(target.id)
        return resolved in {
            f"{module}.{decorator_name}"
            for module in _PYTINCTURE_DECORATOR_MODULES
        }, decorator

    if isinstance(target, ast.Attribute):
        dotted_target = _dotted_attribute_name(target)
        if not dotted_target:
            return False, decorator
        root_name, separator, remainder = dotted_target.partition(".")
        module_name = bindings.modules.get(root_name)
        if not module_name or not separator:
            return False, decorator
        resolved = f"{module_name}.{remainder}"
        return resolved in {
            f"{module}.{decorator_name}"
            for module in _PYTINCTURE_DECORATOR_MODULES
        }, decorator

    return False, decorator


def _module_relative_identifier(file_path: str) -> str:
    """
    Return a stable, POSIX-style path (including `.py`) for a module relative to MODULES_PATH.
    Falls back to the file basename when the module is outside the configured folder.
    """
    modules_root = os.path.abspath(get_modules_path() or os.getcwd())
    absolute_file = os.path.abspath(file_path)

    try:
        rel_path = os.path.relpath(absolute_file, modules_root)
    except ValueError:
        rel_path = os.path.basename(absolute_file)

    if rel_path.startswith(".."):
        rel_path = os.path.basename(absolute_file)

    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path:
        rel_path = os.path.basename(absolute_file)

    if not rel_path.lower().endswith(".py"):
        rel_path += ".py"

    return rel_path


def bff_stream(func=None, *, raw: bool = False, media_type: str = "text/event-stream"):
    """
    Mark a backend_for_frontend method as streaming.

    Args:
        raw: When False (default), streamed Python values will be JSON-encoded and newline-delimited.
             When True, values are forwarded as-is (strings/bytes recommended).
        media_type: Content type to advertise for the stream response.
    """

    def _apply(target):
        setattr(target, "_bff_streaming", True)
        setattr(target, "_bff_streaming_raw", raw)
        setattr(target, "_bff_streaming_media_type", media_type)
        return target

    if func is None:
        return _apply
    return _apply(func)


def bff_policy(**metadata):
    """
    Attach arbitrary policy metadata to a backend_for_frontend method.
    The metadata is later surfaced to the server-side policy hook so applications
    can run custom authorization/validation logic per call.
    """

    def _apply(target):
        existing = getattr(target, "_bff_policy", {})
        combined = {**existing, **metadata}
        setattr(target, "_bff_policy", combined)
        return target

    return _apply


def bff_http_methods(*methods: str):
    """Declare the HTTP methods permitted for a BFF operation.

    BFF methods default to POST. Declaring GET is an explicit promise that the
    operation is parameterless, read-only, and safe to repeat; PUT, PATCH, and
    DELETE are also supported for explicit APIs.
    """
    normalized = tuple(dict.fromkeys(str(method).upper() for method in methods))
    supported = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if not normalized or any(method not in supported for method in normalized):
        raise ValueError(
            "bff_http_methods requires one or more of GET, POST, PUT, PATCH, DELETE"
        )

    def _apply(target):
        setattr(target, "_bff_http_methods", normalized)
        return target

    return _apply


def _literal_keyword_metadata(
    decorators: list[ast.expr],
    *,
    decorator_name: str,
    bindings: _DecoratorBindings,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for decorator in decorators:
        matches, decorator_node = _decorator_matches(
            decorator,
            decorator_name=decorator_name,
            bindings=bindings,
        )
        if not matches or not isinstance(decorator_node, ast.Call):
            continue
        for keyword in decorator_node.keywords:
            if keyword.arg is None:
                raise ValueError(
                    f"{decorator_name} does not support **kwargs in the static BFF manifest"
                )
            try:
                metadata[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"{decorator_name} metadata must use literal values so policy can run before import"
                ) from exc
    return metadata


def _declared_http_methods(
    decorators: list[ast.expr],
    *,
    bindings: _DecoratorBindings,
) -> tuple[str, ...]:
    for decorator in decorators:
        matches, decorator_node = _decorator_matches(
            decorator,
            decorator_name="bff_http_methods",
            bindings=bindings,
        )
        if not matches:
            continue
        if not isinstance(decorator_node, ast.Call):
            raise ValueError("bff_http_methods must be called with explicit HTTP methods")
        try:
            methods = tuple(
                dict.fromkeys(str(ast.literal_eval(argument)).upper() for argument in decorator_node.args)
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("bff_http_methods values must be string literals") from exc
        supported = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if not methods or any(method not in supported for method in methods):
            raise ValueError(
                "bff_http_methods requires one or more of GET, POST, PUT, PATCH, DELETE"
            )
        return methods
    return ("POST",)


def _declared_stream(
    decorators: list[ast.expr],
    *,
    bindings: _DecoratorBindings,
) -> dict[str, Any]:
    for decorator in decorators:
        matches, decorator_node = _decorator_matches(
            decorator,
            decorator_name="bff_stream",
            bindings=bindings,
        )
        if not matches:
            continue
        if not isinstance(decorator_node, ast.Call):
            return {
                "enabled": True,
                "raw": False,
                "media_type": "text/event-stream",
            }
        metadata = _literal_keyword_metadata(
            [decorator_node], decorator_name="bff_stream", bindings=bindings
        )
        if decorator_node.args or set(metadata) - {"raw", "media_type"}:
            raise ValueError("bff_stream accepts only raw and media_type options")
        raw = metadata.get("raw", False)
        media_type = metadata.get("media_type", "text/event-stream")
        if not isinstance(raw, bool) or not isinstance(media_type, str) or not media_type:
            raise ValueError("bff_stream options must be a boolean and non-empty string")
        return {"enabled": True, "raw": raw, "media_type": media_type[:256]}
    return {"enabled": False, "raw": False, "media_type": "application/json"}


def _manifest_annotation(annotation: ast.expr | None) -> str:
    if annotation is None:
        return "Any"
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value[:256]
    try:
        rendered = ast.unparse(annotation)
    except (TypeError, ValueError):
        return "Any"
    return rendered[:256] or "Any"


def _manifest_parameters(member: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[dict, ...]:
    """Describe a method signature without importing or evaluating its module."""
    positional = [*member.args.posonlyargs, *member.args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(member.args.defaults))
    defaults.extend(member.args.defaults)
    parameters: list[dict] = []
    positional_only = len(member.args.posonlyargs)
    if member.args.posonlyargs and member.args.posonlyargs[0].arg in {"self", "cls"}:
        positional_only -= 1
    for index, (argument, default_node) in enumerate(zip(positional, defaults)):
        parameter = {
            "name": argument.arg,
            "kind": "positional_only" if index < positional_only else "positional_or_keyword",
            "required": default_node is None,
            "annotation": _manifest_annotation(argument.annotation),
        }
        if default_node is not None:
            try:
                parameter["default"] = ast.literal_eval(default_node)
            except (ValueError, TypeError):
                parameter["default"] = None
                parameter["default_supported"] = False
        parameters.append(parameter)
    if member.args.vararg is not None:
        parameters.append({
            "name": member.args.vararg.arg,
            "kind": "var_positional",
            "required": False,
            "annotation": _manifest_annotation(member.args.vararg.annotation),
        })
    for argument, default_node in zip(member.args.kwonlyargs, member.args.kw_defaults):
        parameter = {
            "name": argument.arg,
            "kind": "keyword_only",
            "required": default_node is None,
            "annotation": _manifest_annotation(argument.annotation),
        }
        if default_node is not None:
            try:
                parameter["default"] = ast.literal_eval(default_node)
            except (ValueError, TypeError):
                parameter["default"] = None
                parameter["default_supported"] = False
        parameters.append(parameter)
    if member.args.kwarg is not None:
        parameters.append({
            "name": member.args.kwarg.arg,
            "kind": "var_keyword",
            "required": False,
            "annotation": _manifest_annotation(member.args.kwarg.annotation),
        })
    return tuple(parameters)


def get_bff_manifest(
    file_path: str,
    *,
    source: str | None = None,
) -> Dict[tuple[str, str], Dict[str, Any]]:
    """Statically discover exported BFF operations without importing app code."""
    if source is None:
        with open(file_path, "r", encoding="utf-8") as source_file:
            source = source_file.read()
    module = ast.parse(source, filename=file_path)

    module_bindings = _binding_snapshots(module.body)
    manifest: Dict[tuple[str, str], Dict[str, Any]] = {}

    for class_node in (node for node in module.body if isinstance(node, ast.ClassDef)):
        class_bindings = module_bindings[id(class_node)]
        is_exported = any(
            _decorator_matches(
                decorator,
                decorator_name="backend_for_frontend",
                bindings=class_bindings,
            )[0]
            for decorator in class_node.decorator_list
        )
        if not is_exported:
            continue
        _validate_export_binding(module.body, class_node, class_bindings)

        class_policy = _literal_keyword_metadata(
            class_node.decorator_list,
            decorator_name="bff_policy",
            bindings=class_bindings,
        )
        class_definition = _definition_identity(class_node)
        member_bindings = _binding_snapshots(class_node.body, class_bindings)
        for member in class_node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if member.name.startswith("_"):
                    continue
                member_definition = _definition_identity(member)
                method_policy = _literal_keyword_metadata(
                    member.decorator_list,
                    decorator_name="bff_policy",
                    bindings=member_bindings[id(member)],
                )
                http_methods = _declared_http_methods(
                    member.decorator_list,
                    bindings=member_bindings[id(member)],
                )
                parameters = _manifest_parameters(member)
                stream = _declared_stream(
                    member.decorator_list,
                    bindings=member_bindings[id(member)],
                )
                if "GET" in http_methods and parameters:
                    raise ValueError(
                        "GET BFF operations must be parameterless and read-only"
                    )
                manifest[(class_node.name, member.name)] = {
                    "policy": {**class_policy, **method_policy},
                    "http_methods": http_methods,
                    "kind": "method",
                    "parameters": parameters,
                    "stream": stream,
                    "_class_definition": class_definition,
                    "_member_definition": member_definition,
                }
            elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                member_definition = _definition_identity(member)
                targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                for target in targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        manifest[(class_node.name, target.id)] = {
                            "policy": dict(class_policy),
                            "http_methods": ("GET",),
                            "kind": "attribute",
                            "_class_definition": class_definition,
                            "_member_definition": member_definition,
                        }
    return manifest


def has_bff_export_class(
    file_path: str,
    *,
    source: str | None = None,
) -> bool:
    """Return whether source contains a proven Pytincture BFF class.

    Browser packaging uses this static boundary to include the generated BFF
    proxy module without following its server-only imports. Keep this check on
    the same provenance rules as the callable manifest.
    """
    if source is None:
        with open(file_path, "r", encoding="utf-8") as source_file:
            source = source_file.read()
    module = ast.parse(source, filename=file_path)
    module_bindings = _binding_snapshots(module.body)
    return any(
        any(
            _decorator_matches(
                decorator,
                decorator_name="backend_for_frontend",
                bindings=module_bindings[id(class_node)],
            )[0]
            for decorator in class_node.decorator_list
        )
        for class_node in module.body
        if isinstance(class_node, ast.ClassDef)
    )


def _constructor_accepts_user_argument(cls) -> Optional[inspect.Parameter]:
    init_method = cls.__dict__.get("__init__")
    if init_method is None or init_method is object.__init__:
        return None

    try:
        signature = inspect.signature(init_method)
    except (TypeError, ValueError):
        return None

    parameters = list(signature.parameters.values())[1:]  # Skip self.
    for parameter in parameters:
        if parameter.name == "_user":
            return parameter
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return parameter
    return None


def _runtime_bff_definition_identity(cls) -> Dict[str, Any]:
    """Capture the decorated class source and direct runtime member identities."""
    identity: Dict[str, Any] = {
        "class_name": getattr(cls, "__name__", None),
        "class_qualname": getattr(cls, "__qualname__", None),
        "source_path": None,
        "class_sha256": None,
        "member_sha256": {},
        "runtime_attributes": {},
    }
    try:
        source_path = inspect.getsourcefile(cls) or inspect.getfile(cls)
        identity["source_path"] = os.path.realpath(source_path)
        source = textwrap.dedent(inspect.getsource(cls))
        parsed = ast.parse(source, filename=source_path)
        class_node = next(
            node
            for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name == cls.__name__
        )
    except (OSError, TypeError, SyntaxError, StopIteration):
        return identity

    identity["class_sha256"] = _definition_identity(class_node)["sha256"]
    member_sha256: Dict[str, str] = {}
    for member in class_node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            member_sha256[member.name] = _definition_identity(member)["sha256"]
        elif isinstance(member, (ast.Assign, ast.AnnAssign)):
            targets = member.targets if isinstance(member, ast.Assign) else [member.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    member_sha256[target.id] = _definition_identity(member)["sha256"]
    identity["member_sha256"] = member_sha256
    identity["runtime_attributes"] = {
        member_name: vars(cls)[member_name]
        for member_name in member_sha256
        if member_name in vars(cls)
        and not inspect.isfunction(vars(cls)[member_name])
        and not isinstance(vars(cls)[member_name], (staticmethod, classmethod))
    }

    return identity


def verify_bff_runtime_export(
    cls,
    *,
    class_name: str,
    member_name: str,
    operation: Mapping[str, Any],
    source_path: str,
) -> None:
    """Verify runtime dispatch still resolves the exact static BFF definition."""
    if not inspect.isclass(cls):
        raise ValueError("runtime BFF export is not a class")
    class_dict = vars(cls)
    if class_dict.get("_pytincture_bff_export") is not True:
        raise ValueError("runtime BFF class is not a Pytincture export")
    original = class_dict.get("_pytincture_bff_original")
    identity = class_dict.get("_pytincture_bff_definition")
    if not inspect.isclass(original) or not isinstance(identity, Mapping):
        raise ValueError("runtime BFF export identity is missing")

    expected_class = operation.get("_class_definition")
    expected_member = operation.get("_member_definition")
    if not isinstance(expected_class, Mapping) or not isinstance(
        expected_member, Mapping
    ):
        raise ValueError("static BFF definition identity is missing")
    if (
        cls.__name__ != class_name
        or original.__name__ != class_name
        or identity.get("class_name") != class_name
        or identity.get("class_qualname") != class_name
    ):
        raise ValueError("runtime BFF class identity does not match the manifest")
    if os.path.realpath(str(identity.get("source_path") or "")) != os.path.realpath(
        source_path
    ):
        raise ValueError("runtime BFF source does not match the manifest")
    if identity.get("class_sha256") != expected_class.get("sha256"):
        raise ValueError("runtime BFF class definition does not match the manifest")

    member_sha256 = identity.get("member_sha256")
    if not isinstance(member_sha256, Mapping) or member_sha256.get(
        member_name
    ) != expected_member.get("sha256"):
        raise ValueError("runtime BFF member definition does not match the manifest")
    if member_name not in vars(original):
        raise ValueError("runtime BFF member is not defined on the exported class")

    if operation.get("kind") == "method":
        current_member = vars(original).get(member_name)
        if isinstance(current_member, (staticmethod, classmethod)):
            current_member = current_member.__func__
        if not inspect.isfunction(current_member):
            raise ValueError("runtime BFF method identity is missing")
        try:
            current_member = inspect.unwrap(current_member)
        except ValueError as exc:
            raise ValueError("runtime BFF method identity is invalid") from exc
        current_code = getattr(current_member, "__code__", None)
        if current_code is None:
            raise ValueError("runtime BFF method identity is missing")
        if os.path.realpath(
            current_code.co_filename
        ) != os.path.realpath(source_path):
            raise ValueError("runtime BFF method source does not match the manifest")
        if current_code.co_firstlineno != expected_member.get("start_line"):
            raise ValueError("runtime BFF method does not match the manifest")
    elif operation.get("kind") == "attribute":
        runtime_attributes = identity.get("runtime_attributes")
        if not isinstance(runtime_attributes, Mapping) or member_name not in runtime_attributes:
            raise ValueError("runtime BFF attribute identity is missing")
        if vars(original).get(member_name) is not runtime_attributes[member_name]:
            raise ValueError("runtime BFF attribute does not match the manifest")

def get_method_info_from_node(class_node: ast.ClassDef) -> Dict[str, Any]:
    """Extract method information from a class AST node"""
    methods_info = {}
    
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith('_'):  # Skip private methods
                params = []
                for arg in node.args.args:
                    if arg.arg != 'self':  # Skip 'self' parameter
                        param_info = {
                            'name': arg.arg,
                            'type': 'any'  # Default type
                        }
                        # Try to get type annotation if it exists
                        if arg.annotation:
                            if isinstance(arg.annotation, ast.Name):
                                param_info['type'] = arg.annotation.id
                            elif isinstance(arg.annotation, ast.Constant):
                                param_info['type'] = arg.annotation.value
                            
                        params.append(param_info)
                
                methods_info[node.name] = {
                    'parameters': params,
                    'docstring': ast.get_docstring(node)[0:30] or f"Call {node.name[0:30]}",
                    'return_type': 'any'  # Default return type
                }
                
                # Try to get return type annotation if it exists
                if node.returns:
                    if isinstance(node.returns, ast.Name):
                        methods_info[node.name]['return_type'] = node.returns.id
                    elif isinstance(node.returns, ast.Constant):
                        methods_info[node.name]['return_type'] = node.returns.value
    
    return methods_info

def backend_for_frontend(cls):
    """
    A decorator that wraps `cls` in a proxy/wrapper class and generates OpenAPI specs.
    """
    print(f"Registering BFF class: {cls.__name__}")

    # Get module/file name consistently
    module_name = cls.__module__.split('.')[-1]

    # Compute the relative module identifier for routing (includes folders + .py)
    module_file = inspect.getfile(cls)
    module_identifier = _module_relative_identifier(module_file)

    # Register all methods
    for method_name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if not method_name.startswith('_'):
            route_path = (
                f"/{{application}}/classcall/{module_identifier}/"
                f"{cls.__name__}/{method_name}"
            )

            # Get method signature
            sig = inspect.signature(method)
            streaming_enabled = getattr(method, "_bff_streaming", False)
            streaming_raw = getattr(method, "_bff_streaming_raw", False)
            streaming_media_type = getattr(method, "_bff_streaming_media_type", "text/event-stream")
            declared_http_methods = getattr(method, "_bff_http_methods", ("POST",))

            # Create list of parameters in order (excluding self)
            param_list = [
                {
                    'name': name,
                    'type': str(param.annotation) if param.annotation != inspect.Parameter.empty else 'str',
                    'required': param.default == inspect.Parameter.empty
                }
                for name, param in sig.parameters.items()
                if name != 'self'
            ]

            # Create OpenAPI operation spec
            operation_id_full = f"call_{cls.__name__}_{method_name}"
            operation_id = operation_id_full[:50] if len(operation_id_full) > 50 else operation_id_full  # Truncate to ensure <64 chars
            responses_spec = {
                '200': {
                    'description': 'Streaming response' if streaming_enabled else 'Successful response',
                    'content': {
                        (streaming_media_type if streaming_enabled else 'application/json'): {
                            'schema': {
                                'type': 'string' if streaming_enabled else 'object'
                            }
                        }
                    }
                }
            }

            operation_spec = {
                'summary': method.__doc__ or f"Call {method_name} on {cls.__name__}",
                'operationId': operation_id,  # Useful, unique, short, and now truncated if needed
                'tags': [module_name],
                'parameters': [{
                    'name': 'application',
                    'in': 'path',
                    'required': True,
                    'schema': {'type': 'string'},
                }],
                'requestBody': {
                    'content': {
                        'application/json': {
                            'schema': {
                                'type': 'object',
                                'properties': {
                                    'args': {
                                        'type': 'array',
                                        'items': {},
                                        'description': 'Positional arguments in order: ' + 
                                            ', '.join([f"{p['name']}: {p['type']}" for p in param_list])
                                    },
                                    'kwargs': {
                                        'type': 'object',
                                        'properties': {
                                            param.name: {
                                                'type': 'string',
                                                'description': f"Type: {str(param.annotation) if param.annotation != inspect.Parameter.empty else 'str'}"
                                                + (f", Default: {param.default}" if param.default != inspect.Parameter.empty else "")
                                            }
                                            for param in sig.parameters.values()
                                            if param.name != 'self'
                                        }
                                    }
                                },
                                'required': ['args', 'kwargs'],
                                'additionalProperties': False,
                            }
                        }
                    }
                },
                'responses': responses_spec
            }
            operation_spec['x-bff-http-methods'] = list(declared_http_methods)

            if streaming_enabled:
                operation_spec['x-bff-streaming'] = True
                operation_spec['x-bff-streaming-raw'] = streaming_raw
                operation_spec['x-bff-streaming-media-type'] = streaming_media_type
            
            # Add example if we have parameters
            if param_list:
                operation_spec['requestBody']['content']['application/json']['examples'] = {
                    'args_example': {
                        'value': {
                            'args': [
                                {
                                    'name': p['name'],
                                    'type': p['type'],
                                    'value': 'example_value'
                                }
                                for p in param_list
                            ],
                            'kwargs': {}
                        }
                    },
                    'kwargs_example': {
                        'value': {
                            'args': [],
                            'kwargs': {
                                p['name']: 'example_value'
                                for p in param_list
                            }
                        }
                    }
                }
            
            bff_routes[route_path] = operation_spec

    class BackendForFrontendWrapper:
        def __init__(self, *args, **kwargs):
            self._user = kwargs.pop('_user', None)
            constructor_kwargs = dict(kwargs)
            constructor_args = list(args)
            user_parameter = _constructor_accepts_user_argument(cls)

            if self._user is not None and user_parameter is not None:
                if user_parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                    constructor_args.insert(0, self._user)
                else:
                    constructor_kwargs.setdefault('_user', self._user)

            self._real_instance = cls(*constructor_args, **constructor_kwargs)
            if self._user is not None and user_parameter is None:
                setattr(self._real_instance, '_user', self._user)

        def __getattr__(self, item):
            return getattr(self._real_instance, item)

    BackendForFrontendWrapper.__name__ = cls.__name__
    BackendForFrontendWrapper.__qualname__ = cls.__qualname__
    BackendForFrontendWrapper.__module__ = cls.__module__
    BackendForFrontendWrapper.__doc__ = cls.__doc__
    BackendForFrontendWrapper._pytincture_bff_export = True
    BackendForFrontendWrapper._pytincture_bff_original = cls
    BackendForFrontendWrapper._pytincture_bff_definition = (
        _runtime_bff_definition_identity(cls)
    )

    return BackendForFrontendWrapper


def _bff_annotation_json_type(annotation: str) -> str:
    normalized = str(annotation or "Any").replace("typing.", "")
    base = normalized.split("[", 1)[0]
    return {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "dict": "object",
        "Dict": "object",
        "Mapping": "object",
        "list": "array",
        "List": "array",
        "Sequence": "array",
    }.get(base, "string")


def add_bff_docs_to_app(
    app: FastAPI,
    operations: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
    asset_uuid: str | None = None,
    docs_mode: str = "public",
    authorize: Any = None,
):
    """
    Adds BFF-specific OpenAPI documentation to a FastAPI application
    """
    if operations is None:
        documented_routes = dict(bff_routes)
    else:
        documented_routes = {}
        for (module_path, class_name, method_name), operation in operations.items():
            if operation.get("kind") != "method":
                continue
            parameters = operation.get("parameters", ())
            keyword_parameters = [
                parameter
                for parameter in parameters
                if parameter.get("kind")
                in {"positional_or_keyword", "keyword_only"}
            ]
            has_var_keyword = any(
                parameter.get("kind") == "var_keyword"
                for parameter in parameters
            )
            documented_routes[
                f"/{{application}}/classcall/{module_path}/{class_name}/{method_name}"
            ] = {
                "summary": f"Call {method_name} on {class_name}",
                "operationId": f"call_{class_name}_{method_name}"[:50],
                "tags": [module_path],
                "parameters": [{
                    "name": "application",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "args": {
                                        "type": "array",
                                        "items": {},
                                    },
                                    "kwargs": {
                                        "type": "object",
                                        "properties": {
                                            parameter["name"]: {
                                                "type": _bff_annotation_json_type(
                                                    parameter.get("annotation", "Any")
                                                )
                                            }
                                            for parameter in keyword_parameters
                                        },
                                        "required": [
                                            parameter["name"]
                                            for parameter in keyword_parameters
                                            if parameter.get("required")
                                            and parameter.get("kind") == "keyword_only"
                                        ],
                                        "additionalProperties": has_var_keyword,
                                    }
                                },
                                "required": ["args", "kwargs"],
                                "additionalProperties": False,
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Successful response"}},
                "x-bff-http-methods": list(operation.get("http_methods", ("POST",))),
            }

    # Get configuration from environment variables or use defaults
    docs_path = get_runtime_env("BFF_DOCS_PATH", "bff-docs")
    docs_title = get_runtime_env("BFF_DOCS_TITLE", "pyTincture BFF API")
    
    # Ensure docs_path starts with /
    docs_path = f"/{docs_path.lstrip('/')}"
    openapi_path = f"{docs_path}/openapi.json"
    docs_asset_uuid = asset_uuid or uuid.uuid4().hex

    async def authorize_request(request: Request) -> None:
        if docs_mode != "authenticated":
            return
        if not callable(authorize):
            raise RuntimeError(
                "authenticated API documentation requires an authorization hook"
            )
        result = authorize(request)
        if inspect.isawaitable(result):
            await result

    def place_before_application_route(route) -> None:
        for index, existing in enumerate(app.routes[:-1]):
            if getattr(existing, "path", None) == "/{application}":
                app.routes.pop()
                app.routes.insert(index, route)
                break

    def custom_openapi():
        if not app.openapi_schema:
            openapi_schema = get_openapi(
                title=app.title,
                version=app.version or "1.0.0",
                description=app.description or "pyTincture API with Backend for Frontend specification",
                routes=app.routes
            )
            
            # Merge BFF paths into existing paths
            paths = openapi_schema.get("paths", {}).copy()
            # Collect unique tags from BFF
            new_tags = set()
            for route_path, operation_spec in documented_routes.items():
                if route_path not in paths:
                    paths[route_path] = {}
                paths[route_path]['post'] = operation_spec
                if 'tags' in operation_spec:
                    new_tags.update(operation_spec['tags'])
            
            openapi_schema["paths"] = paths
            
            # Add components section if needed
            if 'components' not in openapi_schema:
                openapi_schema['components'] = {}
            
            # Add schemas section if needed
            if 'schemas' not in openapi_schema['components']:
                openapi_schema['components']['schemas'] = {}
            
            # Merge tags: get existing tag names
            existing_tags = openapi_schema.get('tags', [])
            existing_tag_names = set(tag['name'] for tag in existing_tags)
            
            # Add new BFF tags if not already present
            for tag in sorted(new_tags - existing_tag_names):
                existing_tags.append({
                    'name': tag,
                    'description': f'Endpoints from {tag}'
                })
            openapi_schema['tags'] = existing_tags
            
            app.openapi_schema = openapi_schema
        
        return app.openapi_schema

    async def get_bff_docs(request: Request):
        await authorize_request(request)
        root_path = str(request.scope.get("root_path", "")).rstrip("/")
        encoded_uuid = quote(docs_asset_uuid, safe="")
        asset_prefix = f"{root_path}/frontend"
        swagger_css_url = (
            f"{asset_prefix}/vendor/swagger-ui/swagger-ui.css?uuid={encoded_uuid}"
        )
        swagger_js_url = (
            f"{asset_prefix}/vendor/swagger-ui/swagger-ui-bundle.js?uuid={encoded_uuid}"
        )
        docs_js_url = f"{asset_prefix}/bff-docs.js?uuid={encoded_uuid}"
        schema_url = f"{root_path}{openapi_path}?uuid={encoded_uuid}"
        content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(docs_title)}</title>
  <link rel="stylesheet" href="{escape(swagger_css_url, quote=True)}">
</head>
<body>
  <div id="swagger-ui" data-openapi-url="{escape(schema_url, quote=True)}"></div>
  <script defer src="{escape(swagger_js_url, quote=True)}"></script>
  <script defer src="{escape(docs_js_url, quote=True)}"></script>
</body>
</html>
"""
        return HTMLResponse(
            content,
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Security-Policy": (
                    "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "font-src 'self' data:; img-src 'self' data:; "
                    "connect-src 'self'; form-action 'self'"
                ),
            },
        )

    # Set the custom OpenAPI function
    app.openapi = custom_openapi

    if docs_mode != "disabled":
        @app.get(openapi_path, tags=["documentation"], include_in_schema=False)
        async def get_bff_openapi(request: Request):
            await authorize_request(request)
            return JSONResponse(
                custom_openapi(),
                headers={
                    "Cache-Control": "private, no-store, max-age=0",
                    "Vary": "Cookie, Authorization",
                },
            )

        app.get(docs_path, tags=["documentation"], include_in_schema=False)(
            get_bff_docs
        )
        place_before_application_route(app.routes[-1])

        for alias in ("/docs", "/redoc"):
            if alias == docs_path:
                continue

            async def docs_alias(request: Request):
                await authorize_request(request)
                return RedirectResponse(
                    docs_path,
                    status_code=307,
                    headers={"Cache-Control": "private, no-store, max-age=0"},
                )

            app.get(alias, include_in_schema=False)(docs_alias)
            place_before_application_route(app.routes[-1])

        if openapi_path != "/openapi.json":
            @app.get("/openapi.json", include_in_schema=False)
            async def get_openapi_alias(request: Request):
                await authorize_request(request)
                return JSONResponse(
                    custom_openapi(),
                    headers={
                        "Cache-Control": "private, no-store, max-age=0",
                        "Vary": "Cookie, Authorization",
                    },
                )
            place_before_application_route(app.routes[-1])

def get_imports_used_in_class(file_path, class_name, source_code=None):
    if source_code is None:
        with open(file_path, 'r') as file:
            source_code = file.read()
    tree = ast.parse(source_code)

    imports = set()
    imports_used = set()
    import_lines = set()

    # Collect imports in the file
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                import_lines.add(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module if node.module else ''
            for alias in node.names:
                imported_name = alias.asname if alias.asname else alias.name
                imports.add(imported_name)
                import_lines.add(f"from {module} import {imported_name}")

    # Find imports used in the specified class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Name) and subnode.id in imports:
                    imports_used.add(subnode.id)

    return import_lines, imports_used

def generate_stub_classes(
    file_path,
    return_url,
    return_protocol,
    application=None,
    replay_client=None,
    source_code=None,
):
    # Keep these parameters for parser compatibility, but never turn an HTTP
    # request's Host or forwarded headers into executable browser Python.
    del return_url, return_protocol
    if source_code is None:
        with open(file_path, 'r') as file:
            source_code = file.read()
    code = source_code
    
    file_identifier = _module_relative_identifier(file_path)
    application_prefix = (
        f"/{str(application).strip('/')}" if application else None
    )
    module = ast.parse(code)
    module_bindings = _binding_snapshots(module.body)
    class_nodes = [node for node in module.body if isinstance(node, ast.ClassDef)]
    replay_enabled = bool(replay_client)
    replay_capsule = str((replay_client or {}).get("capsule", ""))
    replay_key = tuple((replay_client or {}).get("key", b""))
    replay_low_watermark = int(get_runtime_env("BFF_REPLAY_TOKEN_LOW_WATERMARK", "3"))
    replay_state_url = "/_pytincture/state"

    decorated_class_nodes = [
        node for node in class_nodes
        if any(
            _decorator_matches(
                decorator,
                decorator_name="backend_for_frontend",
                bindings=module_bindings[id(node)],
            )[0]
            for decorator in node.decorator_list
        )
    ]

    if not decorated_class_nodes:
        return code
    if application_prefix is None:
        raise ValueError(
            "application is required when generating backend_for_frontend clients"
        )

    stub_class_code = """
class PytinctureBFFError(RuntimeError):
    \"\"\"A generated BFF request failed without exposing its response body.\"\"\"

    def __init__(self, status_code, operation, correlation_id=None):
        self.status_code = int(status_code)
        self.status = self.status_code
        self.operation = str(operation)
        self.correlation_id = str(correlation_id) if correlation_id else None
        message = f"BFF operation {self.operation} failed with HTTP {self.status_code}"
        if self.correlation_id:
            message += f" (request {self.correlation_id})"
        super().__init__(message)
"""
    class_imports = set()
    all_imports = set()
    used_imports = set()
    def _extract_stream_config(decorator_call):
        config = {
            "raw": False,
            "media_type": "text/event-stream"
        }
        if not isinstance(decorator_call, ast.Call):
            return config
        for keyword in decorator_call.keywords:
            if keyword.arg == "raw" and isinstance(keyword.value, ast.Constant):
                config["raw"] = bool(keyword.value.value)
            if keyword.arg == "media_type" and isinstance(keyword.value, ast.Constant):
                config["media_type"] = str(keyword.value.value)
        return config

    for class_node in class_nodes:
        class_name = class_node.name
        class_bindings = module_bindings[id(class_node)]
        member_bindings = _binding_snapshots(class_node.body, class_bindings)

        if any(
            _decorator_matches(
                decorator,
                decorator_name="backend_for_frontend",
                bindings=class_bindings,
            )[0]
            for decorator in class_node.decorator_list
        ):
            _, used_imports = get_imports_used_in_class(
                file_path, class_name, source_code=code
            )
            class_imports.update(used_imports)
            stub_class_code += f"\nclass {class_name}:\n"
            stub_class_code += f"    _pytincture_replay_enabled = {replay_enabled!r}\n"
            stub_class_code += f"    _pytincture_replay_capsule = {replay_capsule!r}\n"
            stub_class_code += f"    _pytincture_replay_key = {replay_key!r}\n"
            stub_class_code += f"    _pytincture_replay_low = {replay_low_watermark!r}\n"
            stub_class_code += "    _pytincture_replay_pool = []\n"
            stub_class_code += "    _pytincture_replay_refill_task = None\n"
            stub_class_code += "    _pytincture_sync_warning_emitted = False\n"
            stub_class_code += "    _pytincture_browser_timeout = 35.0\n"
            stub_class_code += "    def _bff_operation(self, url):\n"
            stub_class_code += "        method_name = str(url).rstrip('/').rsplit('/', 1)[-1]\n"
            stub_class_code += "        return f'{self.__class__.__name__}.{method_name}'\n"
            stub_class_code += "    def _raise_for_bff_status(self, status, url, correlation_id=None):\n"
            stub_class_code += "        if not 200 <= int(status) < 300:\n"
            stub_class_code += "            raise PytinctureBFFError(status, self._bff_operation(url), correlation_id)\n"
            stub_class_code += "    def _csrf_token(self):\n"
            stub_class_code += "        for cookie in str(document.cookie).split(';'):\n"
            stub_class_code += "            name, separator, value = cookie.strip().partition('=')\n"
            stub_class_code += "            if separator and name in {'__Host-pytincture-csrf', 'pytincture-dev-csrf'}:\n"
            stub_class_code += "                return value\n"
            stub_class_code += "        return ''\n"
            stub_class_code += "    def _decode_pytincture_state(self, encoded):\n"
            stub_class_code += "        padding = '=' * (-len(encoded) % 4)\n"
            stub_class_code += "        packed = base64.urlsafe_b64decode((encoded + padding).encode('ascii'))\n"
            stub_class_code += "        nonce, ciphertext, supplied_tag = packed[:16], packed[16:-16], packed[-16:]\n"
            stub_class_code += "        key = bytes(self._pytincture_replay_key)\n"
            stub_class_code += "        expected_tag = hmac.new(key, b'tag' + nonce + ciphertext, hashlib.sha256).digest()[:16]\n"
            stub_class_code += "        if not hmac.compare_digest(supplied_tag, expected_tag):\n"
            stub_class_code += "            raise RuntimeError('Unable to restore browser state')\n"
            stub_class_code += "        plaintext = bytearray()\n"
            stub_class_code += "        for offset in range(0, len(ciphertext), 32):\n"
            stub_class_code += "            counter = (offset // 32).to_bytes(4, 'big')\n"
            stub_class_code += "            stream = hmac.new(key, b'enc' + nonce + counter, hashlib.sha256).digest()\n"
            stub_class_code += "            plaintext.extend(value ^ stream[index] for index, value in enumerate(ciphertext[offset:offset + 32]))\n"
            stub_class_code += "        state = json.loads(bytes(plaintext).decode('utf-8'))\n"
            stub_class_code += "        if state.get('v') != 1 or not isinstance(state.get('items'), list):\n"
            stub_class_code += "            raise RuntimeError('Unable to restore browser state')\n"
            stub_class_code += "        return state['items']\n"
            stub_class_code += "    def _refill_pytincture_state_sync(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled:\n"
            stub_class_code += "            return\n"
            stub_class_code += "        req = XMLHttpRequest.new()\n"
            stub_class_code += f"        req.open('POST', {replay_state_url!r}, False)\n"
            stub_class_code += "        req.setRequestHeader('X-CSRF-Token', self._csrf_token())\n"
            stub_class_code += "        req.setRequestHeader('X-Pytincture-Client', self._pytincture_replay_capsule)\n"
            stub_class_code += "        req.send()\n"
            stub_class_code += "        if req.status != 200:\n"
            stub_class_code += "            raise RuntimeError('Unable to refresh browser state')\n"
            stub_class_code += "        self._pytincture_replay_pool.extend(self._decode_pytincture_state(str(req.responseText)))\n"
            stub_class_code += "    async def _await_bff(self, awaitable):\n"
            stub_class_code += "        return await asyncio.wait_for(awaitable, timeout=self._pytincture_browser_timeout)\n"
            stub_class_code += "    async def _request_pytincture_state(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled:\n"
            stub_class_code += "            return\n"
            stub_class_code += "        from js import fetch\n"
            stub_class_code += "        from pyodide.ffi import to_js\n"
            stub_class_code += "        options = {'method': 'POST', 'headers': {'X-CSRF-Token': self._csrf_token(), 'X-Pytincture-Client': self._pytincture_replay_capsule}}\n"
            stub_class_code += f"        response = await self._await_bff(fetch({replay_state_url!r}, to_js(options)))\n"
            stub_class_code += "        if response.status != 200:\n"
            stub_class_code += "            raise RuntimeError('Unable to refresh browser state')\n"
            stub_class_code += "        encoded = await self._await_bff(response.text())\n"
            stub_class_code += "        self._pytincture_replay_pool.extend(self._decode_pytincture_state(encoded))\n"
            stub_class_code += "    async def _refill_pytincture_state(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled:\n"
            stub_class_code += "            return\n"
            stub_class_code += "        owner = self.__class__\n"
            stub_class_code += "        task = owner._pytincture_replay_refill_task\n"
            stub_class_code += "        if task is None:\n"
            stub_class_code += "            task = asyncio.create_task(self._request_pytincture_state())\n"
            stub_class_code += "            owner._pytincture_replay_refill_task = task\n"
            stub_class_code += "        try:\n"
            stub_class_code += "            await task\n"
            stub_class_code += "        finally:\n"
            stub_class_code += "            if owner._pytincture_replay_refill_task is task:\n"
            stub_class_code += "                owner._pytincture_replay_refill_task = None\n"
            stub_class_code += "    def _schedule_pytincture_state_refill(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled or len(self._pytincture_replay_pool) > self._pytincture_replay_low:\n"
            stub_class_code += "            return\n"
            stub_class_code += "        task = asyncio.create_task(self._refill_pytincture_state())\n"
            stub_class_code += "        def report_refill(completed):\n"
            stub_class_code += "            try:\n"
            stub_class_code += "                completed.result()\n"
            stub_class_code += "            except asyncio.CancelledError:\n"
            stub_class_code += "                return\n"
            stub_class_code += "            except Exception:\n"
            stub_class_code += "                try:\n"
            stub_class_code += "                    from js import console\n"
            stub_class_code += "                    console.warn('Pytincture replay-token prefetch failed; the next BFF call will retry.')\n"
            stub_class_code += "                except Exception:\n"
            stub_class_code += "                    pass\n"
            stub_class_code += "        task.add_done_callback(report_refill)\n"
            stub_class_code += "    def _take_pytincture_state_sync(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled:\n"
            stub_class_code += "            return ''\n"
            stub_class_code += "        if not self._pytincture_replay_pool:\n"
            stub_class_code += "            self._refill_pytincture_state_sync()\n"
            stub_class_code += "        return self._pytincture_replay_pool.pop()\n"
            stub_class_code += "    async def _take_pytincture_state(self):\n"
            stub_class_code += "        if not self._pytincture_replay_enabled:\n"
            stub_class_code += "            return ''\n"
            stub_class_code += "        if not self._pytincture_replay_pool:\n"
            stub_class_code += "            await self._refill_pytincture_state()\n"
            stub_class_code += "        return self._pytincture_replay_pool.pop()\n"
            stub_class_code += "    def _warn_legacy_sync(self):\n"
            stub_class_code += "        owner = self.__class__\n"
            stub_class_code += "        if owner._pytincture_sync_warning_emitted:\n"
            stub_class_code += "            return\n"
            stub_class_code += "        owner._pytincture_sync_warning_emitted = True\n"
            stub_class_code += "        warnings.warn('Synchronous browser BFF calls are deprecated; await the generated *_async method instead.', DeprecationWarning, stacklevel=3)\n"
            stub_class_code += "    def fetch_sync(self, url, payload=None, method='GET', _replay_retry=True):\n"
            stub_class_code += "        self._warn_legacy_sync()\n"
            stub_class_code += "        replay_token = self._take_pytincture_state_sync()\n"
            stub_class_code += "        req = XMLHttpRequest.new()\n"
            stub_class_code += "        req.open(method, url, False)\n"
            stub_class_code += "        req.setRequestHeader('Content-Type', 'application/json')\n"
            stub_class_code += "        if method != 'GET':\n"
            stub_class_code += "            req.setRequestHeader('X-CSRF-Token', self._csrf_token())\n"
            stub_class_code += "        if replay_token:\n"
            stub_class_code += "            req.setRequestHeader('X-Pytincture-BFF-Token', replay_token)\n"
            stub_class_code += "        if payload and method != 'GET':\n"
            stub_class_code += "            req.send(json.dumps(payload, allow_nan=False))\n"
            stub_class_code += "        else:\n"
            stub_class_code += "            req.send()\n"
            stub_class_code += "        if _replay_retry and req.status == 409 and str(req.getResponseHeader('X-Pytincture-Replay')) == 'rejected':\n"
            stub_class_code += "            self._pytincture_replay_pool.clear()\n"
            stub_class_code += "            return self.fetch_sync(url, payload, method, False)\n"
            stub_class_code += f"        if req.status == 401:\n"
            stub_class_code += f"            from js import window\n"
            stub_class_code += f"            current_url = window.location.href.rstrip('/')\n"
            stub_class_code += f"            redirect_url = current_url + '/login'\n"
            stub_class_code += f"            window.location.href = redirect_url\n"
            stub_class_code += f"            return 'null'\n"
            stub_class_code += "        self._raise_for_bff_status(req.status, url, req.getResponseHeader('X-Request-ID'))\n"
            stub_class_code += f"        return StringIO(req.response).getvalue()\n"
            stub_class_code += f"\n"
            stub_class_code += f"    async def fetch(self, url, payload=None, method='GET', _replay_retry=True):\n"
            stub_class_code += f"        from js import fetch, window\n"
            stub_class_code += f"        from pyodide.ffi import to_js\n"
            stub_class_code += f"        options = {{'method': method, 'headers': {{'Content-Type': 'application/json'}}}}\n"
            stub_class_code += "        replay_token = await self._take_pytincture_state()\n"
            stub_class_code += f"        if method != 'GET':\n"
            stub_class_code += f"            options['headers']['X-CSRF-Token'] = self._csrf_token()\n"
            stub_class_code += "        if replay_token:\n"
            stub_class_code += "            options['headers']['X-Pytincture-BFF-Token'] = replay_token\n"
            stub_class_code += f"        if payload is not None and method != 'GET':\n"
            stub_class_code += f"            options['body'] = json.dumps(payload, allow_nan=False)\n"
            stub_class_code += f"        response = await self._await_bff(fetch(url, to_js(options)))\n"
            stub_class_code += "        if _replay_retry and response.status == 409 and response.headers.get('X-Pytincture-Replay') == 'rejected':\n"
            stub_class_code += "            self._pytincture_replay_pool.clear()\n"
            stub_class_code += "            return await self.fetch(url, payload, method, False)\n"
            stub_class_code += f"        if response.status == 401:\n"
            stub_class_code += f"            current_url = window.location.href.rstrip('/')\n"
            stub_class_code += f"            redirect_url = current_url + '/login'\n"
            stub_class_code += f"            window.location.href = redirect_url\n"
            stub_class_code += f"            return 'null'\n"
            stub_class_code += "        self._raise_for_bff_status(response.status, url, response.headers.get('X-Request-ID'))\n"
            stub_class_code += "        self._schedule_pytincture_state_refill()\n"
            stub_class_code += f"        return await self._await_bff(response.text())\n"

            streaming_methods = {}
            for node in class_node.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for decorator in node.decorator_list:
                        matches, decorator_node = _decorator_matches(
                            decorator,
                            decorator_name="bff_stream",
                            bindings=member_bindings[id(node)],
                        )
                        if matches:
                            streaming_methods[node.name] = _extract_stream_config(decorator_node)
                            break

            if streaming_methods:
                stub_class_code += f"    async def fetch_stream(self, url, payload=None, method='GET', _replay_retry=True):\n"
                stub_class_code += f"        from js import fetch, TextDecoder\n"
                stub_class_code += f"        from pyodide.ffi import to_js\n"
                stub_class_code += f"        options = {{'method': method, 'headers': {{'Content-Type': 'application/json'}}}}\n"
                stub_class_code += "        replay_token = await self._take_pytincture_state()\n"
                stub_class_code += f"        if method != 'GET':\n"
                stub_class_code += f"            options['headers']['X-CSRF-Token'] = self._csrf_token()\n"
                stub_class_code += "        if replay_token:\n"
                stub_class_code += "            options['headers']['X-Pytincture-BFF-Token'] = replay_token\n"
                stub_class_code += f"        body_payload = payload if payload is not None else {{'args': [], 'kwargs': {{}}}}\n"
                stub_class_code += f"        if method != 'GET':\n"
                stub_class_code += f"            options['body'] = json.dumps(body_payload, allow_nan=False)\n"
                stub_class_code += f"        response = await self._await_bff(fetch(url, to_js(options)))\n"
                stub_class_code += "        if _replay_retry and response.status == 409 and response.headers.get('X-Pytincture-Replay') == 'rejected':\n"
                stub_class_code += "            self._pytincture_replay_pool.clear()\n"
                stub_class_code += "            async for retry_chunk in self.fetch_stream(url, payload, method, False):\n"
                stub_class_code += "                yield retry_chunk\n"
                stub_class_code += "            return\n"
                stub_class_code += f"        if response.status == 401:\n"
                stub_class_code += f"            from js import window\n"
                stub_class_code += f"            current_url = window.location.href.rstrip('/')\n"
                stub_class_code += f"            redirect_url = current_url + '/login'\n"
                stub_class_code += f"            window.location.href = redirect_url\n"
                stub_class_code += f"            return\n"
                stub_class_code += "        self._raise_for_bff_status(response.status, url, response.headers.get('X-Request-ID'))\n"
                stub_class_code += "        self._schedule_pytincture_state_refill()\n"
                stub_class_code += f"        reader = response.body.getReader()\n"
                stub_class_code += f"        decoder = TextDecoder.new()\n"
                stub_class_code += f"        while True:\n"
                stub_class_code += f"            chunk = await self._await_bff(reader.read())\n"
                stub_class_code += f"            if chunk.done:\n"
                stub_class_code += f"                break\n"
                stub_class_code += f"            text = decoder.decode(chunk.value, to_js({{'stream': True}}))\n"
                stub_class_code += f"            if text:\n"
                stub_class_code += f"                yield text\n"
                stub_class_code += f"        final_text = decoder.decode()\n"
                stub_class_code += f"        if final_text:\n"
                stub_class_code += f"            yield final_text\n"

            declared_member_names = {
                node.name
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in class_node.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_'):
                    is_streaming = node.name in streaming_methods
                    stream_config = streaming_methods.get(node.name, {"raw": False})
                    is_async_method = isinstance(node, ast.AsyncFunctionDef)
                    declared_methods = _declared_http_methods(
                        node.decorator_list,
                        bindings=member_bindings[id(node)],
                    )
                    request_method = declared_methods[0]
                    if is_streaming:
                        stub_class_code += f"    async def {node.name}(self, *args, **kwargs):\n"
                        call_url = f"{application_prefix}/classcall/{file_identifier}/{class_name}/{node.name}"
                        stub_class_code += f"        url = {call_url!r}\n"
                        stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                        stub_class_code += f"        stream_iter = self.fetch_stream(url, payload, {request_method!r})\n"
                        if stream_config.get("raw"):
                            stub_class_code +=  "        async for chunk in stream_iter:\n"
                            stub_class_code +=  "            if chunk:\n"
                            stub_class_code +=  "                yield chunk\n"
                        else:
                            stub_class_code +=  "        buffer = ''\n"
                            stub_class_code +=  "        async for chunk in stream_iter:\n"
                            stub_class_code +=  "            if not chunk:\n"
                            stub_class_code +=  "                continue\n"
                            stub_class_code +=  "            buffer += chunk\n"
                            stub_class_code +=  "            while '\\n' in buffer:\n"
                            stub_class_code +=  "                line, buffer = buffer.split('\\n', 1)\n"
                            stub_class_code +=  "                line = line.strip()\n"
                            stub_class_code +=  "                if not line:\n"
                            stub_class_code +=  "                    continue\n"
                            stub_class_code +=  "                yield json.loads(line)\n"
                            stub_class_code +=  "        if buffer.strip():\n"
                            stub_class_code +=  "            yield json.loads(buffer)\n"
                    elif is_async_method:
                        stub_class_code += f"    async def {node.name}(self, *args, **kwargs):\n"
                        call_url = f"{application_prefix}/classcall/{file_identifier}/{class_name}/{node.name}"
                        stub_class_code += f"        url = {call_url!r}\n"
                        stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                        stub_class_code += f"        response = await self.fetch(url, payload, {request_method!r})\n"
                        stub_class_code +=  "        return json.loads(response)\n"
                    else:
                        stub_class_code += f"    def {node.name}(self, *args, **kwargs):\n"
                        call_url = f"{application_prefix}/classcall/{file_identifier}/{class_name}/{node.name}"
                        stub_class_code += f"        url = {call_url!r}\n"
                        stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                        stub_class_code += f"        response = self.fetch_sync(url, payload, {request_method!r})\n"
                        stub_class_code +=  "        return json.loads(response)\n"
                        async_companion = f"{node.name}_async"
                        if async_companion not in declared_member_names:
                            stub_class_code += f"    async def {async_companion}(self, *args, **kwargs):\n"
                            stub_class_code += f"        url = {call_url!r}\n"
                            stub_class_code += "        payload = {'args': args, 'kwargs': kwargs}\n"
                            stub_class_code += f"        response = await self.fetch(url, payload, {request_method!r})\n"
                            stub_class_code += "        return json.loads(response)\n"
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            property_name = target.id
                            stub_class_code +=  "    @property\n"
                            stub_class_code += f"    def {property_name}(self):\n"
                            call_url = f"{application_prefix}/classcall/{file_identifier}/{class_name}/{property_name}"
                            stub_class_code += f"        url = {call_url!r}\n"
                            stub_class_code +=  "        response = self.fetch_sync(url)\n"
                            stub_class_code +=  "        return json.loads(response)\n"
    all_imports.add("import json")
    all_imports.add("import base64")
    all_imports.add("import hashlib")
    all_imports.add("import hmac")
    all_imports.add("import asyncio")
    all_imports.add("import warnings")
    all_imports.add("from js import XMLHttpRequest, document")
    all_imports.add("from io import StringIO")
    for imp in all_imports:
        stub_class_code = f"{imp}\n" + stub_class_code

    return stub_class_code

def get_parsed_output(
    file_path,
    return_url,
    return_protocol="http",
    application=None,
    replay_client=None,
    source_code=None,
):
    stub_code = generate_stub_classes(
        file_path,
        return_url,
        return_protocol,
        application=application,
        replay_client=replay_client,
        source_code=source_code,
    )
    if stub_code:
        return stub_code

if __name__ == "__main__":
    import sys
    print(generate_stub_classes(sys.argv[1],"test","http"))
