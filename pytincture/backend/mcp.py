"""Production-safe MCP configuration and explicit BFF tool registration."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")


@dataclass(frozen=True)
class MCPToolSpec:
    """One public MCP name bound to one exact BFF export."""

    name: str
    application: str
    module: str
    class_name: str
    method: str
    scopes: tuple[str, ...]
    description: str = ""


def _json_list(name: str, value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain valid JSON") from exc
    if not isinstance(parsed, list):
        raise RuntimeError(f"{name} must be a JSON list")
    return parsed


def parse_string_list(name: str, value: str) -> tuple[str, ...]:
    """Parse a non-empty JSON string list with no wildcard entries."""
    parsed = _json_list(name, value)
    if not parsed or any(not isinstance(item, str) or not item.strip() for item in parsed):
        raise RuntimeError(f"{name} must be a non-empty JSON string list")
    normalized = tuple(item.strip() for item in parsed)
    if any("*" in item for item in normalized):
        raise RuntimeError(f"{name} does not permit wildcard entries")
    return normalized


def parse_tool_specs(value: str) -> tuple[MCPToolSpec, ...]:
    """Parse explicit, purpose-bound MCP-to-BFF mappings."""
    configured = _json_list("MCP_TOOLS", value)
    specs: list[MCPToolSpec] = []
    names: set[str] = set()
    required = {"name", "application", "module", "class", "method", "scopes"}
    allowed = required | {"description"}
    for index, entry in enumerate(configured):
        if not isinstance(entry, dict) or set(entry) - allowed or not required <= set(entry):
            raise RuntimeError(
                f"MCP_TOOLS[{index}] must contain only name, application, module, "
                "class, method, scopes, and optional description"
            )
        name = str(entry["name"]).strip()
        application = str(entry["application"]).strip()
        module = str(entry["module"]).strip().replace("\\", "/")
        class_name = str(entry["class"]).strip()
        method = str(entry["method"]).strip()
        if not all(_IDENTIFIER.fullmatch(item) for item in (name, application, class_name, method)):
            raise RuntimeError(f"MCP_TOOLS[{index}] contains an invalid identifier")
        if not _MODULE.fullmatch(module) or ".." in module.split("/"):
            raise RuntimeError(f"MCP_TOOLS[{index}] contains an invalid module path")
        scopes_value = entry["scopes"]
        if (
            not isinstance(scopes_value, list)
            or not scopes_value
            or any(not isinstance(scope, str) or not scope.strip() or "*" in scope for scope in scopes_value)
        ):
            raise RuntimeError(f"MCP_TOOLS[{index}].scopes must be non-empty exact strings")
        if name in names:
            raise RuntimeError(f"MCP_TOOLS contains duplicate tool name {name!r}")
        names.add(name)
        specs.append(MCPToolSpec(
            name=name,
            application=application,
            module=module,
            class_name=class_name,
            method=method,
            scopes=tuple(scope.strip() for scope in scopes_value),
            description=str(entry.get("description", "")).strip(),
        ))
    if not specs:
        raise RuntimeError("MCP_TOOLS must explicitly define at least one tool")
    return tuple(specs)


def build_jwt_verifier(environment: Mapping[str, str]):
    """Build the mandatory bearer-token verifier for MCP."""
    try:
        from fastmcp.server.auth.providers.jwt import JWTVerifier
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("MCP requires the pytincture[mcp] extra") from exc
    public_key = environment.get("MCP_JWT_PUBLIC_KEY", "").strip() or None
    jwks_uri = environment.get("MCP_JWT_JWKS_URI", "").strip() or None
    issuer = environment.get("MCP_JWT_ISSUER", "").strip()
    audience = environment.get("MCP_JWT_AUDIENCE", "").strip()
    if bool(public_key) == bool(jwks_uri):
        raise RuntimeError("MCP requires exactly one of MCP_JWT_PUBLIC_KEY or MCP_JWT_JWKS_URI")
    if not issuer or not audience:
        raise RuntimeError("MCP requires MCP_JWT_ISSUER and MCP_JWT_AUDIENCE")
    if jwks_uri and not jwks_uri.lower().startswith("https://"):
        raise RuntimeError("MCP_JWT_JWKS_URI must use HTTPS")
    return JWTVerifier(
        public_key=public_key,
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=audience,
        algorithm=environment.get("MCP_JWT_ALGORITHM", "").strip() or None,
        ssrf_safe=bool(jwks_uri),
    )


def register_bff_tools(
    server,
    specs: Sequence[MCPToolSpec],
    operations: Mapping[tuple[str, str, str], Mapping[str, Any]],
    invoke: Callable[[MCPToolSpec, dict[str, Any]], Awaitable[Any]],
) -> None:
    """Register named tools whose input schema comes from the static BFF manifest."""
    from fastmcp.server.auth import require_scopes

    for spec in specs:
        operation = operations.get((spec.module, spec.class_name, spec.method))
        if operation is None or operation.get("kind") != "method":
            raise RuntimeError(f"MCP tool {spec.name!r} does not reference an exported BFF method")
        parameters = operation.get("parameters", ())
        if any(parameter.get("kind") in {"positional_only", "var_positional", "var_keyword"} for parameter in parameters):
            raise RuntimeError(f"MCP tool {spec.name!r} requires named, bounded parameters")
        if any(parameter.get("default_supported") is False for parameter in parameters):
            raise RuntimeError(f"MCP tool {spec.name!r} requires literal parameter defaults")
        annotation_types = {
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "dict": dict,
            "list": list,
            "Any": Any,
        }

        async def callback(_spec=spec, **arguments):
            return await invoke(_spec, arguments)

        callback.__name__ = spec.name
        callback.__doc__ = spec.description or f"Invoke {spec.class_name}.{spec.method}."
        callback.__annotations__ = {
            **{
                parameter["name"]: annotation_types.get(parameter.get("annotation"), Any)
                for parameter in parameters
            },
            "return": Any,
        }
        callback.__signature__ = inspect.Signature(
            [
                inspect.Parameter(
                    parameter["name"],
                    inspect.Parameter.KEYWORD_ONLY,
                    default=(inspect.Parameter.empty if parameter.get("required", True) else parameter.get("default")),
                    annotation=annotation_types.get(parameter.get("annotation"), Any),
                )
                for parameter in parameters
            ],
            return_annotation=Any,
        )
        server.tool(
            callback,
            name=spec.name,
            description=callback.__doc__,
            auth=require_scopes(*spec.scopes),
            run_in_thread=False,
        )


def build_streamable_app(
    mcp_server,
    *,
    allowed_hosts: Sequence[str],
    allowed_origins: Sequence[str],
    path: str = "/",
):
    """Build a stateless MCP transport with DNS-rebinding protection enabled."""
    return mcp_server.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=list(allowed_hosts),
        allowed_origins=list(allowed_origins),
    )
