"""Canonical, import-free BFF request parsing and static signature validation."""

from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class BFFArguments:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class BFFRequestValidationError(ValueError):
    """A BFF body does not satisfy the public JSON/signature contract."""


def _reject_constant(value: str):
    raise BFFRequestValidationError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BFFRequestValidationError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _validate_json_limits(value: Any, *, max_depth: int, max_items: int) -> None:
    item_count = 0

    def visit(current: Any, depth: int) -> None:
        nonlocal item_count
        if depth > max_depth:
            raise BFFRequestValidationError("BFF JSON nesting limit exceeded")
        if isinstance(current, dict):
            item_count += len(current)
            if item_count > max_items:
                raise BFFRequestValidationError("BFF JSON item limit exceeded")
            for child in current.values():
                visit(child, depth + 1)
        elif isinstance(current, list):
            item_count += len(current)
            if item_count > max_items:
                raise BFFRequestValidationError("BFF JSON item limit exceeded")
            for child in current:
                visit(child, depth + 1)
        elif isinstance(current, float) and not math.isfinite(current):
            raise BFFRequestValidationError("non-finite JSON numbers are not allowed")

    visit(value, 1)


def parse_canonical_bff_body(
    body: bytes,
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
) -> BFFArguments:
    """Parse the single v1 body representation without accepting JSON aliases."""
    if len(body) > max_bytes:
        raise BFFRequestValidationError("BFF request body is too large")
    if not body:
        raise BFFRequestValidationError("BFF request body is required")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BFFRequestValidationError("BFF request body must be UTF-8 JSON") from exc
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except BFFRequestValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise BFFRequestValidationError("BFF request body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise BFFRequestValidationError("BFF request body must be an object")
    if set(data) != {"args", "kwargs"}:
        raise BFFRequestValidationError(
            "BFF request body must contain exactly args and kwargs"
        )
    args = data["args"]
    kwargs = data["kwargs"]
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise BFFRequestValidationError("BFF args must be an array and kwargs an object")
    _validate_json_limits(data, max_depth=max_depth, max_items=max_items)
    return BFFArguments(tuple(args), dict(kwargs))


def _annotation_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _subscript_arguments(node: ast.Subscript) -> tuple[ast.AST, ...]:
    if isinstance(node.slice, ast.Tuple):
        return tuple(node.slice.elts)
    return (node.slice,)


def _matches_annotation(value: Any, node: ast.AST) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _matches_annotation(value, node.left) or _matches_annotation(
            value, node.right
        )
    if isinstance(node, ast.Constant) and node.value is None:
        return value is None

    annotation_name = _annotation_name(node)
    if annotation_name in {"Any", "object"}:
        return True
    if annotation_name in {"None", "NoneType"}:
        return value is None
    if annotation_name == "str":
        return isinstance(value, str)
    if annotation_name == "bool":
        return isinstance(value, bool)
    if annotation_name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation_name == "float":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if annotation_name in {"dict", "Mapping", "MutableMapping"}:
        return isinstance(value, dict)
    if annotation_name in {"list", "Sequence", "MutableSequence"}:
        return isinstance(value, list)

    if not isinstance(node, ast.Subscript):
        # Application-specific annotations cannot be evaluated without
        # importing application code. Signature shape is still enforced.
        return True

    container_name = _annotation_name(node.value)
    arguments = _subscript_arguments(node)
    if container_name == "Annotated" and arguments:
        return _matches_annotation(value, arguments[0])
    if container_name == "Optional" and arguments:
        return value is None or _matches_annotation(value, arguments[0])
    if container_name == "Union":
        return any(_matches_annotation(value, argument) for argument in arguments)
    if container_name == "Literal":
        literal_values = []
        for argument in arguments:
            try:
                literal_values.append(ast.literal_eval(argument))
            except (TypeError, ValueError):
                return True
        return value in literal_values
    if container_name in {"list", "List", "Sequence", "MutableSequence"}:
        return isinstance(value, list) and (
            not arguments
            or all(_matches_annotation(item, arguments[0]) for item in value)
        )
    if container_name in {"dict", "Dict", "Mapping", "MutableMapping"}:
        if not isinstance(value, dict):
            return False
        if len(arguments) < 2:
            return True
        return all(
            _matches_annotation(key, arguments[0])
            and _matches_annotation(item, arguments[1])
            for key, item in value.items()
        )
    if container_name in {"tuple", "Tuple"}:
        if not isinstance(value, list):
            return False
        if not arguments:
            return True
        if (
            len(arguments) == 2
            and isinstance(arguments[1], ast.Constant)
            and arguments[1].value is Ellipsis
        ):
            return all(_matches_annotation(item, arguments[0]) for item in value)
        return len(value) == len(arguments) and all(
            _matches_annotation(item, annotation)
            for item, annotation in zip(value, arguments)
        )
    return True


def _value_matches_annotation(value: Any, annotation: str) -> bool:
    if not annotation or annotation == "Any":
        return True
    try:
        node = ast.parse(annotation, mode="eval").body
    except (SyntaxError, ValueError):
        return True
    return _matches_annotation(value, node)


def validate_bff_arguments(
    arguments: BFFArguments,
    parameters: Sequence[Mapping[str, Any]],
) -> None:
    """Bind JSON arguments to a static manifest signature before app import."""
    positional_parameters = [
        parameter
        for parameter in parameters
        if parameter.get("kind") in {"positional_only", "positional_or_keyword"}
    ]
    keyword_parameters = {
        str(parameter.get("name")): parameter
        for parameter in parameters
        if parameter.get("kind") in {"positional_or_keyword", "keyword_only"}
    }
    positional_only_names = {
        str(parameter.get("name"))
        for parameter in parameters
        if parameter.get("kind") == "positional_only"
    }
    var_positional = next(
        (parameter for parameter in parameters if parameter.get("kind") == "var_positional"),
        None,
    )
    var_keyword = next(
        (parameter for parameter in parameters if parameter.get("kind") == "var_keyword"),
        None,
    )
    if len(arguments.args) > len(positional_parameters) and var_positional is None:
        raise BFFRequestValidationError("too many positional BFF arguments")

    bound_names: set[str] = set()
    for index, value in enumerate(arguments.args):
        parameter = (
            positional_parameters[index]
            if index < len(positional_parameters)
            else var_positional
        )
        name = str(parameter.get("name"))
        if parameter is not var_positional:
            bound_names.add(name)
        if not _value_matches_annotation(value, str(parameter.get("annotation", "Any"))):
            raise BFFRequestValidationError(f"BFF argument {name} has the wrong type")

    for name, value in arguments.kwargs.items():
        if name in positional_only_names:
            raise BFFRequestValidationError(
                f"positional-only BFF argument cannot be named: {name}"
            )
        parameter = keyword_parameters.get(name)
        if parameter is None:
            if var_keyword is None:
                raise BFFRequestValidationError(f"unexpected BFF argument: {name}")
            parameter = var_keyword
        elif name in bound_names:
            raise BFFRequestValidationError(f"duplicate BFF argument: {name}")
        bound_names.add(name)
        if not _value_matches_annotation(value, str(parameter.get("annotation", "Any"))):
            raise BFFRequestValidationError(f"BFF argument {name} has the wrong type")

    missing = [
        str(parameter.get("name"))
        for parameter in parameters
        if parameter.get("required") is True
        and parameter.get("kind")
        in {"positional_only", "positional_or_keyword", "keyword_only"}
        and str(parameter.get("name")) not in bound_names
    ]
    if missing:
        raise BFFRequestValidationError(
            f"missing required BFF arguments: {', '.join(missing)}"
        )
