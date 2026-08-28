"""MCP exposure policy and FastAPI schema filtering."""

import copy
import json

from fastapi import FastAPI

FORBIDDEN_MCP_OPERATION_IDS = {
    "handleUserAuth",
    "mcpAuth",
    "logoutUser",
    "postLogs",
    "downloadAppcodePackage",
    "getLoginPage",
    "getMainApp",
    "issueBffReplayTokens",
    "initiateGoogleAuth",
    "handleGoogleAuthCallback",
    "initiateMicrosoftAuth",
    "handleMicrosoftAuthCallback",
    "initiateSamlAuth",
    "initiateSamlProviderAuth",
    "handleSamlAuthCallback",
    "handleSamlProviderAuthCallback",
}


class FilteredFastAPIApp:
    """Expose only explicitly selected OpenAPI operations to FastMCP."""

    def __init__(self, source_app: FastAPI, operation_ids: set[str]):
        self.source_app = source_app
        self.operation_ids = operation_ids
        self.title = source_app.title

    def openapi(self):
        schema = copy.deepcopy(self.source_app.openapi())
        filtered_paths = {}
        methods = {"get", "post", "put", "patch", "delete", "options", "head"}
        for path, path_item in schema.get("paths", {}).items():
            selected = {
                key: value
                for key, value in path_item.items()
                if key not in methods or value.get("operationId") in self.operation_ids
            }
            if any(key in selected for key in methods - {"options", "head"}):
                filtered_paths[path] = selected
        schema["paths"] = filtered_paths
        return schema

    async def __call__(self, scope, receive, send):
        await self.source_app(scope, receive, send)


def exposed_operation_ids(enabled: bool, raw_operations: str) -> set[str]:
    """Validate the explicit MCP operation allowlist."""
    if not enabled:
        return set()
    try:
        configured = json.loads(raw_operations)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP_EXPOSED_OPERATIONS must be a JSON list") from exc
    if not isinstance(configured, list) or any(
        not isinstance(value, str) for value in configured
    ):
        raise RuntimeError("MCP_EXPOSED_OPERATIONS must be a JSON list")
    requested = set(configured)
    disallowed = requested & FORBIDDEN_MCP_OPERATION_IDS
    if disallowed:
        raise RuntimeError(
            "MCP_EXPOSED_OPERATIONS contains session/login/application routes: "
            + ", ".join(sorted(disallowed))
        )
    return requested


def build_streamable_app(mcp_server, path: str = "/"):
    """Support the current and legacy FastMCP streamable HTTP builders."""
    http_builder = getattr(mcp_server, "http_app", None)
    if callable(http_builder):
        return http_builder(path=path, transport="streamable-http")
    streamable_builder = getattr(mcp_server, "streamable_http_app", None)
    if callable(streamable_builder):
        return streamable_builder(path=path)
    raise AttributeError(
        "FastMCP server does not expose streamable_http_app() or http_app()"
    )
