import asyncio
import base64
import fnmatch
import hashlib
import hmac
import importlib
import inspect
import io
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import uuid
import zipfile
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)
from urllib.parse import parse_qsl, quote, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree

# FastAPI / Starlette
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import escape

# Pydantic for JSON validation
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.config import Config
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Match, Route

# Pytincture
from pytincture import __version__, get_modules_path
from pytincture.backend.auth import (
    SENSITIVE_USER_CLAIM_KEYS,
    allowed_email,
    local_user_claims,
    normalize_roles,
    verify_password,
)
from pytincture.backend.bff import BFFRegistry
from pytincture.backend.bff import build_bff_registry as _build_bff_registry
from pytincture.backend.browser_packages import (
    AppcodeArchiveCache,
    browser_package_files,
    configured_browser_files,
    create_appcode_archive,
    discover_widgetset,
    local_python_imports,
)
from pytincture.backend.diagnostics import (
    internal_error_payload,
    readiness_report,
    request_correlation_id,
    sanitized_validation_errors,
    structured_log,
)
from pytincture.backend.mcp import (
    MCPToolSpec,
    build_jwt_verifier,
    build_streamable_app,
    parse_string_list,
    parse_tool_specs,
    register_bff_tools,
)
from pytincture.backend.limits import AdmissionRejected, AsyncAdmissionGate
from pytincture.backend.middleware import (
    RequestBodyLimitMiddleware,
    RotatingSessionMiddleware,
)
from pytincture.backend.pages import (
    EntryPointDiscoveryError,
    find_app_favicon as _find_app_favicon_metadata,
)
from pytincture.backend.pages import (
    find_app_string_setting,
    normalize_app_asset_path,
)
from pytincture.backend.pages import (
    find_main_window_subclass as _find_main_window,
)
from pytincture.backend.saml import (
    SAMLProviderCatalog,
    SlidingWindowRateLimiter,
    split_csv,
    validate_saml_response_xml,
)
from pytincture.backend.saml import (
    allowed_roles as saml_allowed_roles,
)
from pytincture.backend.saml import (
    normalize_provider as normalize_saml_provider,
)
from pytincture.backend.saml import (
    normalize_provider_id as normalize_saml_provider_id,
)
from pytincture.backend.saml import (
    provider_value as saml_provider_value,
)
from pytincture.backend.safe_paths import (
    UnsafePath,
    decode_python_source,
    normalize_relative_path,
    read_contained_file,
    resolve_contained_path,
    validate_application_name,
)
from pytincture.backend.saml import (
    role_attribute_keys as saml_role_attribute_keys,
)
from pytincture.backend.source_loading import (
    build_dynamic_module_name,
    load_source_module,
)
from pytincture.backend.storage import RedisDict
from pytincture.backend.streaming import as_streaming_response
from pytincture.dataclass import (
    add_bff_docs_to_app,
    get_bff_manifest,
    get_parsed_output,
)

# ========================
#  FASTAPI SETUP
# ========================

app = FastAPI(title="pyTincture API")
_BASE_APP_LIFESPAN = app.router.lifespan_context
logger = logging.getLogger("pytincture.security")
_configured_log_level = os.getenv("PYTINCTURE_LOG_LEVEL", "INFO").strip().upper()
logger.setLevel(getattr(logging, _configured_log_level, logging.INFO))
TRUST_PROXY_HEADERS = os.getenv("PYTINCTURE_TRUST_PROXY_HEADERS", "false").lower() == "true"
CANONICAL_ORIGIN = os.getenv("PYTINCTURE_CANONICAL_ORIGIN", "").strip().rstrip("/")
ALLOWED_HOSTS = tuple(
    host.strip()
    for host in os.getenv("PYTINCTURE_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)
if CANONICAL_ORIGIN:
    _canonical_parts = urlsplit(CANONICAL_ORIGIN)
    if (
        _canonical_parts.scheme not in {"http", "https"}
        or not _canonical_parts.netloc
        or not _canonical_parts.hostname
        or _canonical_parts.username is not None
        or _canonical_parts.password is not None
        or _canonical_parts.path
        or _canonical_parts.query
        or _canonical_parts.fragment
    ):
        raise RuntimeError(
            "PYTINCTURE_CANONICAL_ORIGIN must be an HTTP(S) origin without a path"
        )
    try:
        _canonical_parts.port
    except ValueError as exc:
        raise RuntimeError("PYTINCTURE_CANONICAL_ORIGIN contains an invalid port") from exc
# One cache namespace is shared by every browser served by this process. A
# service restart creates a new value and invalidates the previous instance's
# frontend assets without changing application URLs.
FRONTEND_INSTANCE_UUID = uuid.uuid4().hex


class _OptionalOAuthError(Exception):
    """Placeholder used until the OAuth extra is loaded."""


class _OptionalSamlValidationError(Exception):
    """Placeholder used until the SAML extra is loaded."""


class _DisabledMCP:
    """Compatibility surface when MCP is intentionally not installed/enabled."""

    async def list_tools(self):
        return []


OAuth = None
OAuthError = _OptionalOAuthError
FastMCP = None
OneLogin_Saml2_Auth = None
OneLogin_Saml2_Settings = None
OneLogin_Saml2_ValidationError = _OptionalSamlValidationError

# Discover installed extras without making them mandatory for a base import.
try:
    from authlib.integrations.starlette_client import OAuth as _InstalledOAuth
    from authlib.integrations.starlette_client import OAuthError as _InstalledOAuthError

    OAuth = _InstalledOAuth
    OAuthError = _InstalledOAuthError
except ImportError:
    pass

try:
    from fastmcp import FastMCP as _InstalledFastMCP

    FastMCP = _InstalledFastMCP
except ImportError:
    pass

try:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth as _InstalledSamlAuth
    from onelogin.saml2.errors import (
        OneLogin_Saml2_ValidationError as _InstalledSamlValidationError,
    )
    from onelogin.saml2.settings import OneLogin_Saml2_Settings as _InstalledSamlSettings

    OneLogin_Saml2_Auth = _InstalledSamlAuth
    OneLogin_Saml2_ValidationError = _InstalledSamlValidationError
    OneLogin_Saml2_Settings = _InstalledSamlSettings
except ImportError:
    pass


def _optional_dependency_error(feature: str, extra: str, exc: ImportError) -> RuntimeError:
    return RuntimeError(
        f"{feature} requires optional dependencies; install pytincture[{extra}]"
    )


def _build_dynamic_module_name(file_path: str, name_hint: str) -> str:
    """
    Build a stable module name for manually loaded source files.

    The name includes a sanitized hint for readability and a path hash to avoid
    collisions between different files that share a class name or basename.
    """
    return build_dynamic_module_name(file_path, name_hint, get_modules_path())


def _load_source_module(
    file_path: str,
    name_hint: str,
    *,
    expected_digest: str | None = None,
):
    """
    Load a Python source file using importlib-compatible sys.modules registration.
    """
    return load_source_module(
        file_path,
        name_hint,
        get_modules_path(),
        expected_digest=expected_digest,
    )

def reload_mcp_tools():
    global FastMCP, mcp, mcp_http_app
    
    # Step 1: Remove existing MCP-mounted routes to avoid duplicates
    # Filter out routes starting with "/mcp" to avoid duplicate mounts.
    app.router.routes = [
        route for route in app.router.routes
        if not route.path.startswith("/mcp")
    ]
    
    app.router.lifespan_context = _BASE_APP_LIFESPAN
    if os.getenv("ENABLE_MCP", "false").lower() != "true":
        mcp = _DisabledMCP()
        mcp_http_app = None
        return

    if FastMCP is None:
        try:
            from fastmcp import FastMCP as _FastMCP
        except ImportError as exc:
            raise _optional_dependency_error("MCP", "mcp", exc) from exc
        FastMCP = _FastMCP
    tool_specs = parse_tool_specs(os.getenv("MCP_TOOLS", "[]"))
    for tool_spec in tool_specs:
        validate_application_name(tool_spec.application)
    allowed_hosts = parse_string_list(
        "MCP_ALLOWED_HOSTS", os.getenv("MCP_ALLOWED_HOSTS", "[]")
    )
    allowed_origins = parse_string_list(
        "MCP_ALLOWED_ORIGINS", os.getenv("MCP_ALLOWED_ORIGINS", "[]")
    )
    auth = build_jwt_verifier(os.environ)
    operations = reload_bff_registry(get_modules_path())
    mcp = FastMCP(
        name="pytincture",
        auth=auth,
        mask_error_details=True,
        strict_input_validation=True,
    )
    register_bff_tools(mcp, tool_specs, operations, _invoke_mcp_bff)
    logger.info("MCP tools reloaded: %s", [spec.name for spec in tool_specs])

    mcp_http_app = build_streamable_app(
        mcp,
        path="/",
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    app.mount("/mcp", mcp_http_app)
    app.router.routes.insert(0, app.router.routes.pop())

    class _ExactMCPRoute:
        async def __call__(self, scope, receive, send):
            child_scope = dict(scope)
            child_scope["root_path"] = scope.get("root_path", "") + "/mcp"
            child_scope["path"] = "/"
            child_scope["raw_path"] = b"/"
            await mcp_http_app(child_scope, receive, send)

    app.router.routes.insert(0, Route("/mcp", endpoint=_ExactMCPRoute()))
    from fastmcp.utilities.lifespan import combine_lifespans

    app.router.lifespan_context = combine_lifespans(
        _BASE_APP_LIFESPAN, mcp_http_app.lifespan
    )

def _local_python_imports(file_path: str, modules_root: str) -> Set[str]:
    """Return local Python files directly imported by a browser module."""
    return local_python_imports(file_path, modules_root)


def _configured_browser_files(modules_root: str) -> Set[str]:
    return configured_browser_files(
        modules_root,
        os.getenv("PYTINCTURE_BROWSER_FILES", ""),
    )


def _browser_package_files(application: str) -> Set[str]:
    validate_application_name(application)
    return browser_package_files(
        application,
        get_modules_path(),
        os.getenv("PYTINCTURE_BROWSER_FILES", ""),
    )


def create_appcode_pkg_in_memory(host, protocol, application, replay_client=None):
    """Generate an explicit browser-safe app package in memory."""
    validate_application_name(application)
    return create_appcode_archive(
        host,
        protocol,
        application,
        get_modules_path(),
        get_parsed_output,
        replay_client,
        os.getenv("PYTINCTURE_BROWSER_FILES", ""),
        max_files=APPCODE_MAX_FILES,
        max_file_bytes=APPCODE_MAX_FILE_BYTES,
        max_total_bytes=APPCODE_MAX_TOTAL_BYTES,
        cache=APPCODE_ARCHIVE_CACHE,
    )


def _get_default_application() -> Optional[str]:
    configured = os.getenv("PYTINCTURE_DEFAULT_APPLICATION", "").strip()
    if not configured:
        return None
    try:
        validate_application_name(configured)
    except ValueError as exc:
        raise RuntimeError("PYTINCTURE_DEFAULT_APPLICATION is invalid") from exc
    return configured


@app.get("/", include_in_schema=False)
async def default_application_redirect():
    default_application = _get_default_application()
    if default_application is None:
        raise HTTPException(status_code=404, detail="No default application configured")
    application_path = quote(default_application, safe="")
    return RedirectResponse(url=f"/{application_path}", status_code=302)


@app.get("/healthz", include_in_schema=False)
async def health_check():
    """Process liveness: successful while the ASGI worker can answer requests."""
    return {"status": "ok", "version": __version__}


@app.get("/readyz", include_in_schema=False)
async def readiness_check():
    """Traffic readiness for application files, frontend assets, and shared stores."""
    stores = {}
    if USE_REDIS_INSTANCE == "true":
        stores = {
            "session_store": USER_SESSION_DICT,
            "revocation_store": AUTH_SESSION_REVOCATIONS,
            "replay_store": BFF_REPLAY_TOKEN_STORE,
        }
    ready, checks = readiness_report(get_modules_path(), STATIC_PATH, stores)
    return JSONResponse(
        {"status": "ready" if ready else "not-ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.get("/favicon.ico", operation_id="getFavicon", responses={200: {"description": "Response (binary content for favicon.ico, or empty if not implemented)"}, 404: {"description": "JSONResponse (if file not found, but currently not handled)"}})
async def favicon():
    """
    Serves the favicon.ico file.
    """
    pass

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_validation_errors(exc.errors())},
    )


@app.middleware("http")
async def application_name_middleware(request: Request, call_next):
    """Apply one application identifier policy to every matching route."""
    for route in request.app.router.routes:
        match, child_scope = route.matches(request.scope)
        if match is not Match.FULL:
            continue
        application = child_scope.get("path_params", {}).get("application")
        if application is not None:
            try:
                validate_application_name(application)
            except ValueError:
                return JSONResponse(
                    {"detail": "Application not found"}, status_code=404
                )
        break
    return await call_next(request)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    started = time.monotonic()
    correlation_id = request_correlation_id(request.headers.get("x-request-id"))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = correlation_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; connect-src 'self' https:; "
        "worker-src 'self' blob:",
    )
    csrf_token = request.session.get("csrf_token") if hasattr(request, "session") else None
    if csrf_token:
        response.set_cookie(
            "pytincture_csrf",
            csrf_token,
            max_age=AUTH_SESSION_MAX_AGE_SECONDS,
            secure=AUTH_SESSION_HTTPS_ONLY,
            httponly=False,
            samesite=AUTH_SESSION_SAME_SITE,
        )
    structured_log(
        logger,
        logging.INFO,
        "request.complete",
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
    )
    return response


@app.exception_handler(HTTPException)
async def sanitized_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        correlation_id = getattr(
            request.state,
            "correlation_id",
            request_correlation_id(),
        )
        structured_log(
            logger,
            logging.WARNING if exc.status_code in {503, 504} else logging.ERROR,
            "request.error",
            exc_info=exc.status_code not in {503, 504},
            correlation_id=correlation_id,
            status_code=exc.status_code,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            internal_error_payload(correlation_id),
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def sanitized_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(
        request.state,
        "correlation_id",
        request_correlation_id(),
    )
    structured_log(
        logger,
        logging.ERROR,
        "request.error",
        exc_info=True,
        correlation_id=correlation_id,
        status_code=500,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        internal_error_payload(correlation_id),
        status_code=500,
    )

def get_widgetset(application, static_path):
    """
    Scan the application file and its imports to find the widgetset.
    """
    return discover_widgetset(application, static_path)

def create_pytincture_pkg_in_memory():
    """Generate a pytincture widgetset package in memory."""
    pytincture_folder = os.path.join(os.path.dirname(__file__), "../../pytincture")
    file_to_replace = "pytincture/__init__.py"

    in_memory_zip = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(pytincture_folder):
            dirs[:] = [
                directory
                for directory in dirs
                if not directory.startswith(".")
                and directory
                not in {"__pycache__", ".venv", "venv", "node_modules", "build", "dist"}
            ]
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.join(pytincture_folder, '..'))
                if arcname == file_to_replace:
                    zipf.writestr(arcname, '')
                else:
                    zipf.write(file_path, arcname)
    in_memory_zip.seek(0)
    return in_memory_zip

allowed_origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
allow_all_methods = ["*"]
allow_all_headers = ["*"]

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=allow_all_methods,
        allow_headers=allow_all_headers,
        expose_headers=["X-Pytincture-SHA256", "X-Request-ID"],
    )
else:
    logger.info("CORS middleware disabled; set CORS_ALLOWED_ORIGINS to enable it")

if ALLOWED_HOSTS:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=ALLOWED_HOSTS,
        www_redirect=False,
    )

# Mount the frontend static files
STATIC_PATH = os.path.join(os.path.dirname(__file__), "../frontend/")
_PUBLIC_FRAMEWORK_FILES = frozenset({
    "pytincture.js",
    "dist/pytincture.js",
    "dist/pytincture.js.map",
    "dist/pytincture.esm.js",
    "dist/pytincture.esm.js.map",
    "dist/pytincture.min.js",
    "dist/pytincture.min.js.map",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl.metadata",
    "pyodide/0.29.3/full/pyodide-lock.json",
    "pyodide/0.29.3/full/pyodide.asm.js",
    "pyodide/0.29.3/full/pyodide.asm.wasm",
    "pyodide/0.29.3/full/pyodide.js",
    "pyodide/0.29.3/full/python_stdlib.zip",
})


class _ManifestStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        normalized = path.replace("\\", "/").lstrip("/")
        if normalized not in _PUBLIC_FRAMEWORK_FILES:
            raise HTTPException(status_code=404, detail="Static asset not found")
        return await super().get_response(normalized, scope)


USE_REDIS_INSTANCE = os.environ.get("USE_REDIS_INSTANCE", "false").lower()
if  USE_REDIS_INSTANCE == "true":
    REDIS_UPSTASH_INSTANCE_URL = os.environ.get("REDIS_UPSTASH_INSTANCE_URL", "")
    REDIS_UPSTASH_INSTANCE_TOKEN =  os.environ.get("REDIS_UPSTASH_INSTANCE_TOKEN", "")
    _remote_store_options = {
        "timeout_seconds": float(os.getenv("REMOTE_STORE_TIMEOUT_SECONDS", "2")),
        "failure_threshold": int(os.getenv("REMOTE_STORE_FAILURE_THRESHOLD", "3")),
        "cooldown_seconds": float(os.getenv("REMOTE_STORE_COOLDOWN_SECONDS", "15")),
    }
    USER_SESSION_DICT = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="session",
        **_remote_store_options,
    )
    AUTH_SESSION_REVOCATIONS = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="revoked-session:",
        cache_reads=False,
        **_remote_store_options,
    )
    BFF_REPLAY_TOKEN_STORE = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="bff-replay-token:",
        cache_reads=False,
        **_remote_store_options,
    )
else:
    USER_SESSION_DICT = {}
    AUTH_SESSION_REVOCATIONS = {}
    BFF_REPLAY_TOKEN_STORE = {}

MODULE_PATH = get_modules_path()


def build_bff_registry(modules_root: Optional[str] = None) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    """Build the complete exported BFF registry without importing application code."""
    return _build_bff_registry(
        modules_root or get_modules_path(),
        manifest_loader=get_bff_manifest,
    )


BFF_REGISTRY_ROOT = os.path.realpath(MODULE_PATH)
_BFF_REGISTRY_STATE = BFFRegistry(
    BFF_REGISTRY_ROOT,
    get_bff_manifest,
    autoload=False,
)
BFF_REGISTRY = _BFF_REGISTRY_STATE.operations


def reload_bff_registry(modules_root: Optional[str] = None):
    """Rebuild exported BFF operations, for example after development-time file changes."""
    global BFF_REGISTRY_ROOT, BFF_REGISTRY
    BFF_REGISTRY = _BFF_REGISTRY_STATE.reload(modules_root or get_modules_path())
    BFF_REGISTRY_ROOT = _BFF_REGISTRY_STATE.root
    return BFF_REGISTRY


def _registered_bff_operation(
    modules_root: str, relative_path: str, class_name: str, function_name: str
) -> Optional[Dict[str, Any]]:
    global BFF_REGISTRY_ROOT, BFF_REGISTRY
    operation = _BFF_REGISTRY_STATE.operation(
        modules_root,
        relative_path,
        class_name,
        function_name,
    )
    BFF_REGISTRY_ROOT = _BFF_REGISTRY_STATE.root
    BFF_REGISTRY = _BFF_REGISTRY_STATE.operations
    return operation

try:
    ALLOWED_NOAUTH_CLASSCALLS = json.loads(os.environ.get("ALLOWED_NOAUTH_CLASSCALLS", "[]"))
except json.JSONDecodeError as e:
    raise RuntimeError("Invalid JSON in ALLOWED_NOAUTH_CLASSCALLS environment variable") from e


def _service_worker_response(request: Request, *, allowed_scope: str) -> Response:
    """Serve bytes that change when the requested cache namespace changes."""

    marker_input = ":".join(
        (
            request.query_params.get("uuid", "")[:256],
            request.query_params.get("release", "")[:256],
        )
    )
    marker = hashlib.sha256(marker_input.encode("utf-8")).hexdigest()
    with open(os.path.join(STATIC_PATH, "sw.js"), encoding="utf-8") as worker_file:
        worker_source = worker_file.read()
    return Response(
        content=f"// pytincture-worker-namespace: {marker}\n{worker_source}",
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Service-Worker-Allowed": allowed_scope,
        },
    )


@app.get("/frontend/sw.js", include_in_schema=False)
def service_worker_script(request: Request):
    """Serve the standalone worker with a framework-only maximum scope."""

    return _service_worker_response(request, allowed_scope="/")


@app.get("/{application}/frontend/sw.js", include_in_schema=False)
def application_service_worker_script(application: str, request: Request):
    """Serve an application-isolated worker under its frontend asset scope."""
    try:
        validate_application_name(application)
    except ValueError:
        raise HTTPException(status_code=404, detail="Application not found")
    return _service_worker_response(request, allowed_scope=f"/{application}/")


app.mount("/{application}/frontend", _ManifestStaticFiles(directory=STATIC_PATH), name="static")
app.mount("/frontend", _ManifestStaticFiles(directory=STATIC_PATH), name="static_frontend")

BFF_POLICY_HOOK: Optional[Callable[..., Any]] = None
USER_AUTHENTICATOR: Optional[Callable[..., Any]] = None


def set_bff_policy_hook(hook: Optional[Callable[..., Any]]):
    """
    Register (or clear) a global hook that runs before each backend_for_frontend call.
    The hook receives the resolved user session, policy metadata, and request context.
    """
    global BFF_POLICY_HOOK
    BFF_POLICY_HOOK = hook
    return hook


def _configured_bff_policy_hook() -> Optional[Callable[..., Any]]:
    if BFF_POLICY_HOOK is not None:
        return BFF_POLICY_HOOK
    dotted_path = os.getenv("BFF_POLICY_HOOK_PATH", "").strip()
    if not dotted_path:
        return None
    module_name, separator, attribute_name = dotted_path.rpartition(".")
    if not separator:
        raise RuntimeError("BFF_POLICY_HOOK_PATH must be a dotted callable path")
    hook = getattr(importlib.import_module(module_name), attribute_name)
    if not callable(hook):
        raise RuntimeError("BFF_POLICY_HOOK_PATH must resolve to a callable")
    return hook


def _validate_bff_policy_configuration() -> None:
    operations = reload_bff_registry(get_modules_path())
    if any(operation.get("policy") for operation in operations.values()):
        if _configured_bff_policy_hook() is None:
            raise RuntimeError(
                "@bff_policy exports require BFF_POLICY_HOOK_PATH or "
                "set_bff_policy_hook() before application startup"
            )


def validate_bff_policy_configuration() -> None:
    """Fail closed before serving when declared BFF policy cannot run."""
    _validate_bff_policy_configuration()


app.router.add_event_handler("startup", validate_bff_policy_configuration)


def set_user_authenticator(authenticator: Optional[Callable[..., Any]]):
    """Register a local email/password authenticator that returns trusted user claims."""
    global USER_AUTHENTICATOR
    USER_AUTHENTICATOR = authenticator
    return authenticator


def revoke_session(session_id: str) -> None:
    """Revoke through an optional shared store; base deployments stay stateless."""
    if session_id:
        if USE_REDIS_INSTANCE != "true":
            return
        expires_at = time.time() + AUTH_SESSION_MAX_AGE_SECONDS
        set_with_ttl = getattr(AUTH_SESSION_REVOCATIONS, "set_with_ttl", None)
        if callable(set_with_ttl):
            set_with_ttl(session_id, expires_at, AUTH_SESSION_MAX_AGE_SECONDS)
        else:
            for revoked_id, revoked_until in tuple(AUTH_SESSION_REVOCATIONS.items()):
                try:
                    if float(revoked_until) <= time.time():
                        del AUTH_SESSION_REVOCATIONS[revoked_id]
                except (TypeError, ValueError):
                    continue
            AUTH_SESSION_REVOCATIONS[session_id] = expires_at


def _session_is_revoked(session_id: str) -> bool:
    try:
        expires_at = AUTH_SESSION_REVOCATIONS.get(session_id)
    except Exception:  # noqa: BLE001 - configured revocation store must fail closed
        if USE_REDIS_INSTANCE == "true":
            logger.exception("Shared session revocation lookup failed; rejecting session")
            return True
        raise
    if expires_at is None:
        return False
    try:
        if float(expires_at) > time.time():
            return True
    except (TypeError, ValueError):
        return True
    try:
        del AUTH_SESSION_REVOCATIONS[session_id]
    except (KeyError, TypeError):
        pass
    return False


def _configured_user_authenticator() -> Optional[Callable[..., Any]]:
    if USER_AUTHENTICATOR is not None:
        return USER_AUTHENTICATOR
    dotted_path = os.getenv("AUTH_USER_AUTHENTICATOR", "").strip()
    if not dotted_path:
        return None
    module_name, separator, attribute_name = dotted_path.rpartition(".")
    if not separator:
        raise RuntimeError("AUTH_USER_AUTHENTICATOR must be a dotted callable path")
    authenticator = getattr(importlib.import_module(module_name), attribute_name)
    if not callable(authenticator):
        raise RuntimeError("AUTH_USER_AUTHENTICATOR must resolve to a callable")
    return authenticator


def _allowed_email(email: str) -> bool:
    return allowed_email(email, os.getenv("ALLOWED_EMAILS", ""))


def _is_loopback_development_request(request: Request) -> bool:
    """Return whether the connection peer is a literal loopback address.

    Host and forwarded headers describe the requested origin, not the network
    peer, and are attacker-controlled unless a trusted proxy replaces them.
    Development password bypass therefore never consults those headers.
    """

    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _verify_configured_password(email: str, password: str) -> bool:
    return verify_password(email, password, os.getenv("AUTH_PASSWORD_HASHES", ""))


_SENSITIVE_USER_CLAIM_KEYS = SENSITIVE_USER_CLAIM_KEYS


def _configured_local_user_claims(email: str) -> Dict[str, Any]:
    """Load profile claims separately from password verification.

    AUTH_USER_CLAIMS is the preferred source. DEFAULT_APP_USERS remains a
    compatibility fallback, but any password or token fields are discarded.
    """
    raw_claims = os.getenv("AUTH_USER_CLAIMS", "").strip()
    source_name = "AUTH_USER_CLAIMS"
    if not raw_claims:
        raw_claims = os.getenv("DEFAULT_APP_USERS", "").strip()
        source_name = "DEFAULT_APP_USERS"
    return local_user_claims(email, raw_claims, source_name)


async def _authenticate_local_user(
    request: Request, email: str, password: str
) -> Dict[str, Any]:
    if not ENABLE_USER_LOGIN:
        raise HTTPException(status_code=403, detail="User login not enabled")
    normalized_email = str(email or "").strip().casefold()
    if len(normalized_email) > AUTH_LOGIN_EMAIL_MAX_CHARS or len(password or "") > AUTH_LOGIN_PASSWORD_MAX_CHARS:
        raise HTTPException(status_code=400, detail="Login fields exceed configured limits")
    if not normalized_email or not isinstance(password, str) or not _allowed_email(normalized_email):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    peer = request.client.host if request.client is not None else "unknown"
    account_key = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
    retry_after = 0
    for key in (f"peer:{peer}", f"account:{account_key}"):
        allowed, retry_after = AUTH_LOGIN_RATE_LIMITER.allow(key)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts",
                headers={"Retry-After": str(retry_after)},
            )

    try:
        await PASSWORD_HASH_GATE.acquire()
    except AdmissionRejected as exc:
        raise HTTPException(
            status_code=503,
            detail="Password verification capacity is temporarily exhausted",
            headers={"Retry-After": "1"},
        ) from exc

    release_gate = True
    verifier_task = None
    try:
        authenticator = _configured_user_authenticator()
        if authenticator is not None and inspect.iscoroutinefunction(authenticator):
            authenticated = await asyncio.wait_for(
                authenticator(
                    email=normalized_email, password=password, request=request
                ),
                timeout=AUTH_PASSWORD_HASH_TIMEOUT_SECONDS,
            )
        else:
            verifier = authenticator or _verify_configured_password
            verifier_args = () if authenticator is not None else (normalized_email, password)
            verifier_kwargs = (
                {"email": normalized_email, "password": password, "request": request}
                if authenticator is not None
                else {}
            )
            verifier_task = asyncio.create_task(
                run_in_threadpool(verifier, *verifier_args, **verifier_kwargs)
            )
            try:
                authenticated = await asyncio.wait_for(
                    asyncio.shield(verifier_task),
                    timeout=AUTH_PASSWORD_HASH_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if not verifier_task.done():
                    verifier_task.add_done_callback(
                        lambda _task: PASSWORD_HASH_GATE.release()
                    )
                    release_gate = False
                raise
        if authenticator is not None:
            if inspect.isawaitable(authenticated):
                authenticated = await asyncio.wait_for(
                    authenticated, timeout=AUTH_PASSWORD_HASH_TIMEOUT_SECONDS
                )
            if not authenticated:
                raise HTTPException(status_code=401, detail="Invalid email or password")
            if authenticated is True:
                return {"email": normalized_email}
            if not isinstance(authenticated, dict):
                raise RuntimeError("User authenticator must return a mapping, True, or False")
            return {**authenticated, "email": normalized_email}
        password_valid = bool(authenticated)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Password verification timed out",
            headers={"Retry-After": "1"},
        ) from exc
    finally:
        if release_gate:
            PASSWORD_HASH_GATE.release()

    if password_valid:
        return _configured_local_user_claims(normalized_email)

    if (
        ENABLE_DEV_EMAIL_LOGIN
        and os.getenv("ALLOWED_EMAILS", "").strip()
        and _is_loopback_development_request(request)
    ):
        logger.warning("Using loopback-only development email login for %s", normalized_email)
        return _configured_local_user_claims(normalized_email)

    raise HTTPException(status_code=401, detail="Invalid email or password")


def _normalize_file_identifier(value: str) -> str:
    try:
        return normalize_relative_path(value)
    except UnsafePath:
        return ""


def _canonical_bff_identifier(value: str) -> str:
    normalized = _normalize_file_identifier(value)
    if not normalized:
        return ""
    if not normalized.endswith(".py"):
        normalized += ".py"
    return normalized


def is_noauth_allowed(
    file_name: str,
    class_name: str,
    function_name: str,
    application: Optional[str] = None,
) -> bool:
    """
    Check if the given file, class, and function is allowed to be called without auth.
    Module matching is exact after adding an omitted `.py` suffix. Auth-enabled
    applications must also name the application so one grant cannot authorize a
    same-named operation in another application.
    """
    requested_file = _canonical_bff_identifier(file_name)
    if not requested_file:
        return False

    for entry in ALLOWED_NOAUTH_CLASSCALLS:
        entry_file = _canonical_bff_identifier(str(entry.get("file", "")))
        if entry_file != requested_file:
            continue
        entry_application = str(entry.get("application") or "").strip()
        if entry_application != str(application or ""):
            continue
        if (
            entry.get("class") == class_name
            and entry.get("function") == function_name
        ):
            return True
    return False


def _assert_application_audience(user: Any, application: Optional[str]) -> None:
    if not application or not isinstance(user, dict):
        return
    if user.get("is_authenticated") is not True:
        return
    audience = str(user.get("application") or "")
    if not audience or not hmac.compare_digest(audience, application):
        raise HTTPException(status_code=403, detail="Session is not authorized for this application")


def _application_bff_identifiers(application: str, modules_root: str) -> Set[str]:
    """Return exact modules delivered to one browser application."""
    validate_application_name(application)
    root = os.path.realpath(modules_root)
    try:
        files = _browser_package_files(application)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Application not found") from None
    return {
        os.path.relpath(path, root).replace(os.sep, "/")
        for path in files
        if path.endswith(".py")
    }


def _enforce_declared_bff_policy(
    user: Any,
    policy: Dict[str, Any],
    *,
    application: Optional[str],
    function_name: str,
) -> None:
    """Enforce standard policy claims before invoking an application hook."""
    if not policy:
        return
    claims = _coerce_policy_user(user)
    comparisons = {
        "issuer": claims.get("issuer", claims.get("iss")),
        "tenant": claims.get("tenant", claims.get("tenant_id", claims.get("tid"))),
        "provider": claims.get("auth_provider"),
        "auth_provider": claims.get("auth_provider"),
        "application": application,
        "operation": function_name,
    }
    for key, actual in comparisons.items():
        expected = policy.get(key)
        if expected is not None and str(actual or "") != str(expected):
            raise HTTPException(status_code=403, detail="BFF policy denied")
    required_role = policy.get("role")
    required_roles = policy.get("roles")
    if required_role is not None:
        required_roles = [required_role]
    if required_roles is not None:
        if isinstance(required_roles, str):
            required_roles = [required_roles]
        actual_roles = set(_normalize_auth_roles(claims.get("roles", claims.get("role"))))
        expected_roles = set(_normalize_auth_roles(required_roles))
        if not expected_roles or not expected_roles.issubset(actual_roles):
            raise HTTPException(status_code=403, detail="BFF policy denied")


def _coerce_policy_user(user: Any) -> Dict[str, Any]:
    if isinstance(user, dict):
        return user
    if user == "noauth":
        return {
            "email": "",
            "password": "",
            "picture": "appcode/profile.png",
            "auth_type": "noauth",
            "is_authenticated": False,
        }
    return {"value": user}


def _clear_auth_session(request: Request) -> None:
    for key in (
        "user",
        "session_id",
        "csrf_token",
        "saml_name_id",
        "saml_session_index",
        "saml_provider_id",
        "saml_request_id",
        "auth_issued_at",
        "login_csrf_token",
    ):
        request.session.pop(key, None)


def _normalize_auth_roles(value: Any) -> List[str]:
    return normalize_roles(value)


_DEFAULT_AUTH_SESSION_CLAIM_KEYS = {
    "id",
    "role",
    "plan",
    "next_billing",
    "theme",
    "sidebar",
    "issuer",
    "subject",
    "tenant",
}


def _auth_session_claim_keys() -> Set[str]:
    configured = {
        key.strip()
        for key in os.getenv("AUTH_SESSION_CLAIM_KEYS", "").split(",")
        if key.strip() and key.strip().casefold() not in _SENSITIVE_USER_CLAIM_KEYS
    }
    return _DEFAULT_AUTH_SESSION_CLAIM_KEYS | configured


def _safe_session_claim_value(value: Any) -> bool:
    try:
        encoded = json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= 1024


def _build_auth_session_user(
    user_info: Any,
    *,
    auth_type: Optional[str] = None,
    auth_provider: Optional[str] = None,
    auth_provider_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the compact, stable identity stored in the signed session cookie."""
    try:
        source = dict(user_info or {})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid authenticated user data") from exc

    email = str(source.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Authenticated user is missing an email address")

    resolved_auth_type = str(auth_type or source.get("auth_type") or "").strip()
    resolved_provider = str(auth_provider or source.get("auth_provider") or "").strip()
    resolved_provider_label = str(
        auth_provider_label or source.get("auth_provider_label") or ""
    ).strip()

    session_user: Dict[str, Any] = {
        "session_version": AUTH_SESSION_SCHEMA_VERSION,
        "email": email,
        "name": str(source.get("name") or "").strip(),
        "picture": str(source.get("picture") or "appcode/profile.png"),
        "auth_type": resolved_auth_type,
        "roles": _normalize_auth_roles(source.get("roles", source.get("role"))),
        "is_authenticated": True,
    }
    reserved_claims = set(session_user) | {
        "password",
        "password_hash",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "saml",
    }
    for claim_name in sorted(_auth_session_claim_keys()):
        if (
            claim_name not in reserved_claims
            and claim_name.casefold() not in _SENSITIVE_USER_CLAIM_KEYS
            and claim_name in source
            and _safe_session_claim_value(source[claim_name])
        ):
            session_user[claim_name] = source[claim_name]
    if resolved_provider:
        session_user["auth_provider"] = resolved_provider
    if resolved_provider_label:
        session_user["auth_provider_label"] = resolved_provider_label
    issuer = str(source.get("issuer") or source.get("iss") or "").strip()
    subject = str(source.get("subject") or source.get("sub") or "").strip()
    tenant = str(source.get("tenant") or source.get("tid") or "").strip()
    if issuer:
        session_user["issuer"] = issuer
    if subject:
        session_user["subject"] = subject
    if tenant:
        session_user["tenant"] = tenant

    saml_source = source.get("saml")
    if isinstance(saml_source, dict):
        saml_identity = {
            "provider_id": str(saml_source.get("provider_id") or resolved_provider),
            "provider_label": str(
                saml_source.get("provider_label") or resolved_provider_label
            ),
            "name_id": str(saml_source.get("name_id") or ""),
        }
        session_user["saml"] = {
            key: value for key, value in saml_identity.items() if value
        }

    return session_user


def _set_authenticated_user(
    request: Request,
    user_info: Any,
    *,
    application: str,
    **identity_overrides: Any,
) -> Dict[str, Any]:
    session_user = _build_auth_session_user(user_info, **identity_overrides)
    session_user["application"] = application
    _clear_auth_session(request)
    request.session["user"] = session_user
    request.session["session_id"] = secrets.token_urlsafe(24)
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    request.session["auth_issued_at"] = int(time.time())
    return session_user


def require_auth(request: Request):
    if (
        ENABLE_GOOGLE_AUTH
        or ENABLE_MICROSOFT_AUTH
        or ENABLE_USER_LOGIN
        or ENABLE_SAML_AUTH
    ):
        user_session = request.session.get("user") or {}
        if not isinstance(user_session, dict):
            _clear_auth_session(request)
            return None

        if user_session.get("session_version") != AUTH_SESSION_SCHEMA_VERSION:
            _clear_auth_session(request)
            return None

        email = user_session.get("email")
        if not isinstance(email, str) or not email.strip():
            _clear_auth_session(request)
            return None

        if user_session.get("is_authenticated") is not True:
            _clear_auth_session(request)
            return None

        session_id = request.session.get("session_id")
        if not isinstance(session_id, str) or not session_id or _session_is_revoked(session_id):
            _clear_auth_session(request)
            return None
        issued_at = request.session.get("auth_issued_at")
        if (
            not isinstance(issued_at, (int, float))
            or issued_at > time.time() + 60
            or time.time() - issued_at > AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS
        ):
            _clear_auth_session(request)
            return None

        return user_session
    else:
        return {
            "email": "",
            "password": "",
            "picture": "appcode/profile.png",
            "auth_type": "noauth",
            "roles": [],
            "is_authenticated": False,
        }


def require_authenticated_user(request: Request):
    user = require_auth(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _request_origin(request: Request) -> str:
    if CANONICAL_ORIGIN:
        return CANONICAL_ORIGIN
    scheme = request.url.scheme
    host = request.headers.get("host", "")
    if TRUST_PROXY_HEADERS:
        scheme = request.headers.get("x-forwarded-proto", scheme).split(",", 1)[0]
        host = request.headers.get("x-forwarded-host", host).split(",", 1)[0]
    return f"{scheme}://{host}".rstrip("/")


def _validate_csrf(request: Request, user: Any) -> None:
    if request.scope.get("pytincture.mcp_user") is not None:
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if not isinstance(user, dict) or user.get("is_authenticated") is not True:
        return
    expected = request.session.get("csrf_token", "")
    supplied = request.headers.get("x-csrf-token", "")
    if not expected or not supplied or not hmac.compare_digest(str(expected), supplied):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != _request_origin(request):
        raise HTTPException(status_code=403, detail="Origin validation failed")


def _validate_preauthentication_request(request: Request, supplied_token: str) -> None:
    """Bind browser login posts to the page and exact initiating origin."""
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site", "").strip().casefold()
    if origin is not None and origin.strip().rstrip("/") != _request_origin(request):
        raise HTTPException(status_code=403, detail="Origin validation failed")
    if fetch_site and fetch_site != "same-origin":
        raise HTTPException(status_code=403, detail="Fetch Metadata validation failed")
    expected = request.session.get("login_csrf_token")
    if expected and (
        not supplied_token
        or not hmac.compare_digest(str(expected), str(supplied_token))
    ):
        raise HTTPException(status_code=403, detail="Login CSRF validation failed")


def _validate_bff_browser_request(request: Request) -> None:
    """Reject drive-by browser mutations while preserving trusted API clients.

    Browsers identify their initiating origin and fetch site. Non-browser
    clients commonly send neither header and remain supported, but supplying
    either header opts the request into strict browser validation.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return

    content_type = request.headers.get("content-type", "").partition(";")[0]
    if content_type.strip().casefold() != "application/json":
        raise HTTPException(
            status_code=415,
            detail="BFF requests require application/json",
        )

    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site")
    if origin is not None:
        normalized_origin = origin.strip().rstrip("/")
        if (
            not normalized_origin
            or normalized_origin.casefold() == "null"
            or normalized_origin != _request_origin(request)
        ):
            raise HTTPException(status_code=403, detail="Origin validation failed")

    if fetch_site is None:
        return
    normalized_fetch_site = fetch_site.strip().casefold()
    if normalized_fetch_site != "same-origin":
        raise HTTPException(status_code=403, detail="Fetch Metadata validation failed")
    if origin is None:
        # A browser identifying itself through Fetch Metadata must also prove
        # the exact origin for a state-changing dispatcher request.
        raise HTTPException(status_code=403, detail="Origin validation failed")


def _release_bff_slot(request: Request) -> None:
    if getattr(request.state, "bff_slot_held", False):
        request.state.bff_slot_held = False
        BFF_ADMISSION_GATE.release()


async def _admit_bff_call(request: Request):
    try:
        await BFF_ADMISSION_GATE.acquire()
    except AdmissionRejected as exc:
        raise HTTPException(
            status_code=503,
            detail="BFF capacity is temporarily exhausted",
            headers={"Retry-After": "1"},
        ) from exc
    request.state.bff_slot_held = True
    request.state.bff_deadline = time.monotonic() + BFF_CALL_TIMEOUT_SECONDS
    try:
        yield
    finally:
        if getattr(request.state, "bff_stream_owns_slot", False):
            return
        deferred = getattr(request.state, "bff_deferred_task", None)
        if deferred is not None and not deferred.done():
            deferred.add_done_callback(lambda _task: _release_bff_slot(request))
        else:
            _release_bff_slot(request)


def _remaining_bff_seconds(request: Request) -> float:
    return max(0.0, request.state.bff_deadline - time.monotonic())


async def _run_bff_thread_stage(request: Request, function: Callable, *args, **kwargs):
    remaining = _remaining_bff_seconds(request)
    if remaining <= 0:
        raise HTTPException(status_code=504, detail="BFF call timed out")
    task = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
    except asyncio.TimeoutError as exc:
        # A Python thread cannot be killed safely. Keep its admission slot until
        # it actually exits so repeated timeouts cannot create unbounded work.
        request.state.bff_deferred_task = task
        raise HTTPException(status_code=504, detail="BFF call timed out") from exc


def _bff_replay_subject(request: Request, user: Any) -> Optional[str]:
    if not isinstance(user, dict) or user.get("is_authenticated") is not True:
        return None
    session_id = request.session.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session_id


def _bff_replay_token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _purge_expired_bff_replay_tokens() -> None:
    if not isinstance(BFF_REPLAY_TOKEN_STORE, dict):
        return
    now = time.time()
    for key, value in tuple(BFF_REPLAY_TOKEN_STORE.items()):
        if not isinstance(value, dict) or float(value.get("expires_at", 0)) <= now:
            BFF_REPLAY_TOKEN_STORE.pop(key, None)


def _store_with_optional_ttl(store, key: str, value: Dict[str, Any], ttl: int) -> None:
    set_with_ttl = getattr(store, "set_with_ttl", None)
    if callable(set_with_ttl):
        set_with_ttl(key, value, ttl)
    else:
        store[key] = value


def _register_bff_replay_client(request: Request, user: Any) -> Optional[Dict[str, Any]]:
    if not ENABLE_BFF_REPLAY_TOKENS:
        return None
    session_id = _bff_replay_subject(request, user)
    if session_id is None:
        return None
    key = secrets.token_bytes(32)
    expires_at = time.time() + AUTH_SESSION_MAX_AGE_SECONDS
    descriptor = json.dumps(
        {
            "session_id": session_id,
            "key": base64.urlsafe_b64encode(key).decode("ascii"),
            "expires_at": expires_at,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    capsule_key = hashlib.sha256(
        SAML_SECRET_KEY.encode("utf-8") + b"pytincture-bff-client-capsule-v1"
    ).digest()
    return {"capsule": _encrypt_opaque_envelope(capsule_key, descriptor), "key": key}


def _bff_replay_client_key(request: Request, session_id: str) -> bytes:
    capsule = request.headers.get("x-pytincture-client", "")
    capsule_key = hashlib.sha256(
        SAML_SECRET_KEY.encode("utf-8") + b"pytincture-bff-client-capsule-v1"
    ).digest()
    try:
        record = json.loads(_decrypt_opaque_envelope(capsule_key, capsule))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Browser state expired") from exc
    if (
        not isinstance(record, dict)
        or record.get("session_id") != session_id
        or float(record.get("expires_at", 0)) <= time.time()
    ):
        raise HTTPException(status_code=409, detail="Browser state expired")
    try:
        return base64.urlsafe_b64decode(str(record["key"]).encode("ascii"))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="Browser state expired") from exc


def _encrypt_opaque_envelope(key: bytes, plaintext: bytes) -> str:
    nonce = secrets.token_bytes(16)
    encrypted = bytearray()
    for offset in range(0, len(plaintext), 32):
        counter = (offset // 32).to_bytes(4, "big")
        stream = hmac.new(key, b"enc" + nonce + counter, hashlib.sha256).digest()
        encrypted.extend(
            value ^ stream[index]
            for index, value in enumerate(plaintext[offset:offset + 32])
        )
    ciphertext = bytes(encrypted)
    tag = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii").rstrip("=")


def _decrypt_opaque_envelope(key: bytes, encoded: str) -> bytes:
    if not encoded:
        raise ValueError("Missing envelope")
    padding = "=" * (-len(encoded) % 4)
    packed = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
    if len(packed) < 33:
        raise ValueError("Invalid envelope")
    nonce, ciphertext, supplied_tag = packed[:16], packed[16:-16], packed[-16:]
    expected_tag = hmac.new(
        key,
        b"tag" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()[:16]
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise ValueError("Invalid envelope")
    plaintext = bytearray()
    for offset in range(0, len(ciphertext), 32):
        counter = (offset // 32).to_bytes(4, "big")
        stream = hmac.new(key, b"enc" + nonce + counter, hashlib.sha256).digest()
        plaintext.extend(
            value ^ stream[index]
            for index, value in enumerate(ciphertext[offset:offset + 32])
        )
    return bytes(plaintext)


def _encrypt_bff_replay_payload(key: bytes, tokens: List[str]) -> str:
    """Return an authenticated opaque envelope for the generated browser stub."""
    plaintext = json.dumps({"v": 1, "items": tokens}, separators=(",", ":")).encode("utf-8")
    return _encrypt_opaque_envelope(key, plaintext)


def _issue_bff_replay_tokens(session_id: str) -> List[str]:
    _purge_expired_bff_replay_tokens()
    expires_at = time.time() + BFF_REPLAY_TOKEN_TTL_SECONDS
    issued = []
    for _ in range(BFF_REPLAY_TOKEN_BATCH_SIZE):
        token = secrets.token_urlsafe(32)
        value = {"session_id": session_id, "expires_at": expires_at}
        key = _bff_replay_token_key(token)
        _store_with_optional_ttl(
            BFF_REPLAY_TOKEN_STORE,
            key,
            value,
            BFF_REPLAY_TOKEN_TTL_SECONDS,
        )
        issued.append(token)
    return issued


def _validate_bff_replay_token(request: Request, user: Any) -> None:
    if request.scope.get("pytincture.mcp_user") is not None:
        return
    if not ENABLE_BFF_REPLAY_TOKENS:
        return
    session_id = _bff_replay_subject(request, user)
    if session_id is None:
        return
    supplied = request.headers.get("x-pytincture-bff-token", "")
    if not supplied:
        raise HTTPException(
            status_code=409,
            detail="BFF request proof invalid or expired",
            headers={"X-Pytincture-Replay": "rejected"},
        )
    key = _bff_replay_token_key(supplied)
    pop_atomic = getattr(BFF_REPLAY_TOKEN_STORE, "pop_atomic", None)
    if callable(pop_atomic):
        token_record = pop_atomic(key, None)
    else:
        token_record = BFF_REPLAY_TOKEN_STORE.pop(key, None)
    if (
        not isinstance(token_record, dict)
        or token_record.get("session_id") != session_id
        or float(token_record.get("expires_at", 0)) <= time.time()
    ):
        raise HTTPException(
            status_code=409,
            detail="BFF request proof invalid or expired",
            headers={"X-Pytincture-Replay": "rejected"},
        )


@app.post(
    "/_pytincture/state",
    operation_id="issueBffReplayTokens",
    include_in_schema=False,
)
async def issue_bff_replay_tokens(
    request: Request,
    user=Depends(require_authenticated_user),
):
    if not ENABLE_BFF_REPLAY_TOKENS:
        raise HTTPException(status_code=404, detail="Not found")
    _validate_csrf(request, user)
    session_id = _bff_replay_subject(request, user)
    if session_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    client_key = _bff_replay_client_key(request, session_id)
    payload = _encrypt_bff_replay_payload(
        client_key,
        _issue_bff_replay_tokens(session_id),
    )
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )

@app.get("/{application}/appcode/appcode.pyt", operation_id="downloadAppcodePackage", responses={200: {"description": "StreamingResponse (ZIP file stream, media_type=\"application/zip\")"}, 401: {"description": "HTTPException (if authentication fails when required)"}})
def download_appcode(request: Request, application: str, user=Depends(require_authenticated_user)):
    try:
        validate_application_name(application)
    except ValueError:
        raise HTTPException(status_code=404, detail="Application not found")
    _assert_application_audience(user, application)
    replay_client = _register_bff_replay_client(request, user)
    if not APPCODE_BUILD_GATE.acquire(timeout=APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS):
        raise HTTPException(
            status_code=503,
            detail="Appcode build capacity is temporarily exhausted",
            headers={"Retry-After": "1"},
        )
    try:
        file_like = create_appcode_pkg_in_memory(
            "",
            "",
            application,
            replay_client=replay_client,
        )
    finally:
        APPCODE_BUILD_GATE.release()
    return StreamingResponse(
        file_like,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=appcode.pyt",
            "Cache-Control": "private, no-store",
            "Vary": "Cookie, Authorization",
        },
    )


_DEFAULT_PUBLIC_ASSET_EXTENSIONS = {
    ".avif", ".bmp", ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js",
    ".m4a", ".mp3", ".mp4", ".ogg", ".otf", ".png", ".svg", ".ttf",
    ".wav", ".webm", ".webmanifest", ".webp", ".woff", ".woff2",
}


def _public_asset_allowed(relative_path: str) -> bool:
    extension = os.path.splitext(relative_path)[1].lower()
    if extension in _DEFAULT_PUBLIC_ASSET_EXTENSIONS:
        return True
    raw_patterns = os.getenv("PYTINCTURE_PUBLIC_ASSET_PATHS", "").strip()
    if not raw_patterns:
        return False
    try:
        patterns = json.loads(raw_patterns)
    except json.JSONDecodeError:
        patterns = [value.strip() for value in raw_patterns.split(",") if value.strip()]
    if not isinstance(patterns, list):
        raise RuntimeError("PYTINCTURE_PUBLIC_ASSET_PATHS must be a list of globs")
    return any(
        isinstance(pattern, str) and fnmatch.fnmatch(relative_path, pattern)
        for pattern in patterns
    )


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _application_widget_wheel_allowed(
    application: str,
    relative_path: str,
    modules_root: str,
) -> bool:
    """Allow only a root-level wheel for the widgetset detected for this app."""
    if "/" in relative_path or not relative_path.lower().endswith(".whl"):
        return False
    wheel_match = re.fullmatch(
        r"(?P<distribution>[A-Za-z0-9_.]+)-[^/]+-[^-]+-[^-]+-[^-]+\.whl",
        relative_path,
    )
    if not wheel_match:
        return False
    widget_spec = get_widgetset(application, modules_root)
    widget_distribution = widget_spec.split("==", 1)[0].strip()
    if not widget_distribution:
        return False
    return _normalized_distribution_name(wheel_match.group("distribution")) == (
        _normalized_distribution_name(widget_distribution)
    )


@app.api_route(
    "/{application}/appcode/{asset_path:path}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def public_app_asset(request: Request, application: str, asset_path: str):
    try:
        validate_application_name(application)
        normalized = normalize_relative_path(asset_path)
    except (UnsafePath, ValueError):
        raise HTTPException(status_code=404, detail="Asset not found")
    if any(part.startswith(".") for part in normalized.split("/")):
        raise HTTPException(status_code=404, detail="Asset not found")
    modules_root = os.path.realpath(get_modules_path())
    asset_allowed = _public_asset_allowed(normalized) or _application_widget_wheel_allowed(
        application,
        normalized,
        modules_root,
    )
    if not asset_allowed:
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        secure_file = read_contained_file(modules_root, normalized)
    except UnsafePath:
        raise HTTPException(status_code=404, detail="Asset not found")
    media_type = mimetypes.guess_type(secure_file.path)[0] or "application/octet-stream"
    return Response(
        content=b"" if request.method == "HEAD" else secure_file.content,
        media_type=media_type,
        headers={
            "Content-Length": str(secure_file.size),
            "X-Pytincture-SHA256": secure_file.digest,
        },
    )

@app.get("/{application}/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="getApplicationClassCall", response_model=Any)
@app.post("/{application}/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="postApplicationClassCall", response_model=Any)
@app.put("/{application}/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="putApplicationClassCall", response_model=Any)
@app.patch("/{application}/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="patchApplicationClassCall", response_model=Any)
@app.delete("/{application}/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="deleteApplicationClassCall", response_model=Any)
@app.get("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="getClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
@app.post("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="postClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
@app.put("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="putClassCall", response_model=Any)
@app.patch("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="patchClassCall", response_model=Any)
@app.delete("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="deleteClassCall", response_model=Any)
async def class_call(
    file_path: str,
    class_name: str,
    function_name: str,
    request: Request,
    application: Optional[str] = None,
    _admission=Depends(_admit_bff_call),
):
    if application is not None:
        try:
            validate_application_name(application)
        except ValueError:
            raise HTTPException(status_code=404, detail="Application not found")

    # Determine if this call is allowed without auth.
    normalized_identifier = _normalize_file_identifier(file_path)
    if not normalized_identifier:
        raise HTTPException(status_code=400, detail="Invalid file path")

    request_identifier_with_ext = normalized_identifier
    if not request_identifier_with_ext.lower().endswith(".py"):
        request_identifier_with_ext += ".py"

    mcp_user = request.scope.get("pytincture.mcp_user")
    if mcp_user is not None:
        user = mcp_user
    elif is_noauth_allowed(
        request_identifier_with_ext,
        class_name,
        function_name,
        application,
    ):
        user = "noauth"
    else:
        # Perform authentication check for calls not whitelisted for no-auth.
        user = await _run_bff_thread_stage(request, require_auth, request)

    if not user:
        raise HTTPException(status_code=401, detail="Call not authorized")
    if not application and isinstance(user, dict) and user.get("is_authenticated") is True:
        application = str(user.get("application") or "") or None
        if application is None:
            raise HTTPException(
                status_code=403,
                detail="Session has no application audience; sign in again",
            )
    _assert_application_audience(user, application)

    modules_root = os.path.abspath(get_modules_path())
    if application:
        application_identifiers = await _run_bff_thread_stage(
            request,
            _application_bff_identifiers,
            application,
            modules_root,
        )
        if request_identifier_with_ext not in application_identifiers:
            raise HTTPException(
                status_code=404,
                detail="BFF operation not exported by this application",
            )
    fs_relative = request_identifier_with_ext.replace("/", os.sep)
    fs_relative = os.path.normpath(fs_relative)

    if fs_relative.startswith("..") or os.path.isabs(fs_relative) or os.path.splitdrive(fs_relative)[0]:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if os.path.basename(fs_relative).startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")

    try:
        module_file_path = resolve_contained_path(
            modules_root,
            request_identifier_with_ext,
        )
    except UnsafePath:
        raise HTTPException(status_code=404, detail=f"File {request_identifier_with_ext} not found in appcode folder")

    operation = await _run_bff_thread_stage(
        request,
        _registered_bff_operation,
        modules_root,
        request_identifier_with_ext,
        class_name,
        function_name,
    )
    if operation is None:
        raise HTTPException(status_code=404, detail="BFF operation not exported")
    module_file_path = str(operation.get("_source_path") or module_file_path)
    source_digest = str(operation.get("_source_digest") or "")
    structured_log(
        logger,
        logging.INFO,
        "bff.start",
        correlation_id=getattr(request.state, "correlation_id", ""),
        module=request_identifier_with_ext,
        class_name=class_name,
        function_name=function_name,
        method=request.method,
    )
    allowed_methods = tuple(operation["http_methods"])
    if request.method not in allowed_methods:
        raise HTTPException(
            status_code=405,
            detail="HTTP method not allowed for this BFF operation",
            headers={"Allow": ", ".join(allowed_methods)},
        )

    _validate_bff_browser_request(request)
    _validate_csrf(request, user)
    await _run_bff_thread_stage(request, _validate_bff_replay_token, request, user)
    policy = operation.get("policy", {})
    policy_hook = await _run_bff_thread_stage(
        request, _configured_bff_policy_hook
    )
    if policy and policy_hook is None:
        raise RuntimeError(
            "A @bff_policy export requires BFF_POLICY_HOOK_PATH or set_bff_policy_hook()"
        )
    _enforce_declared_bff_policy(
        user,
        policy,
        application=application,
        function_name=function_name,
    )
    if policy_hook:
        policy_arguments = {
            "user": _coerce_policy_user(user),
            "policy": policy,
            "application": application,
            "class_name": class_name,
            "function_name": function_name,
            "module_path": request_identifier_with_ext,
            "request": request,
        }
        if inspect.iscoroutinefunction(policy_hook):
            try:
                policy_result = await asyncio.wait_for(
                    policy_hook(**policy_arguments),
                    timeout=_remaining_bff_seconds(request),
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF policy timed out") from exc
        else:
            policy_result = await _run_bff_thread_stage(
                request, lambda: policy_hook(**policy_arguments)
            )
        if inspect.isawaitable(policy_result):
            try:
                await asyncio.wait_for(
                    policy_result, timeout=_remaining_bff_seconds(request)
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF policy timed out") from exc

    def prepare_call():
        module = _load_source_module(
            module_file_path,
            class_name,
            expected_digest=source_digest or None,
        )
        cls = getattr(module, class_name)
        instance = cls(_user=user)
        return getattr(instance, function_name)

    func = await _run_bff_thread_stage(request, prepare_call)

    # 3) Get the function
    function_obj = getattr(func, "__func__", func)
    is_streaming = getattr(function_obj, "_bff_streaming", False)
    streaming_raw = getattr(function_obj, "_bff_streaming_raw", False)
    streaming_media_type = getattr(function_obj, "_bff_streaming_media_type", "text/event-stream")
    is_async_gen_function = inspect.isasyncgenfunction(function_obj)
    is_coroutine_function = inspect.iscoroutinefunction(function_obj)

    # 4) If it's a POST, parse JSON body
    data = {}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            data = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    
    if isinstance(data, str):
        try:
            data = json.loads(str(data))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if callable(func):
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="BFF request body must be an object")
        args = data.get("args", [])
        kwargs = data.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise HTTPException(status_code=400, detail="Invalid BFF arguments")

        # Handle structured args format if present
        if args and isinstance(args[0], dict) and 'value' in args[0]:
            args = [arg['value'] for arg in args]
        # Handle if args and kwargs do not exist in data
        elif "args" not in data and "kwargs" not in data:
            kwargs = data

        def _as_streaming_response(result_obj):
            def log_stream_finish(reason, output_bytes):
                structured_log(
                    logger,
                    logging.INFO,
                    "bff.stream.finish",
                    correlation_id=getattr(request.state, "correlation_id", ""),
                    module=request_identifier_with_ext,
                    class_name=class_name,
                    function_name=function_name,
                    reason=reason,
                    output_bytes=output_bytes,
                )
                _release_bff_slot(request)

            response = as_streaming_response(
                result_obj,
                raw=streaming_raw,
                media_type=streaming_media_type,
                max_seconds=BFF_STREAM_MAX_SECONDS,
                max_bytes=BFF_STREAM_MAX_BYTES,
                max_items=BFF_STREAM_MAX_ITEMS,
                idle_timeout_seconds=BFF_STREAM_IDLE_TIMEOUT_SECONDS,
                on_finish=log_stream_finish,
            )
            request.state.bff_stream_owns_slot = True
            return response

        # Execute the target callable
        if is_async_gen_function:
            result = func(*args, **kwargs)
            if is_streaming:
                return _as_streaming_response(result)
            collected_items = []
            collected_bytes = 0
            async def collect_items():
                nonlocal collected_bytes
                async for item in result:
                    collected_items.append(item)
                    if len(collected_items) > BFF_STREAM_MAX_ITEMS:
                        raise HTTPException(status_code=413, detail="BFF result item limit exceeded")
                    collected_bytes += len(json.dumps(item, default=str).encode("utf-8"))
                    if collected_bytes > BFF_STREAM_MAX_BYTES:
                        raise HTTPException(status_code=413, detail="BFF result byte limit exceeded")
            try:
                await asyncio.wait_for(
                    collect_items(), timeout=_remaining_bff_seconds(request)
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF call timed out") from exc
            return collected_items

        if is_coroutine_function:
            try:
                result = await asyncio.wait_for(
                    func(*args, **kwargs), timeout=_remaining_bff_seconds(request)
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF call timed out") from exc
        else:
            result = await _run_bff_thread_stage(request, func, *args, **kwargs)

        if is_streaming:
            return _as_streaming_response(result)

        return result

    return func


async def _invoke_mcp_bff(spec: MCPToolSpec, arguments: Dict[str, Any]) -> Any:
    """Invoke one exact BFF export using a verified MCP bearer identity."""
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        raise PermissionError("MCP authentication required")
    claims = dict(token.claims or {})
    subject = str(token.subject or token.client_id)
    user = {
        **claims,
        "email": str(claims.get("email") or subject),
        "subject": subject,
        "client_id": token.client_id,
        "roles": claims.get("roles", claims.get("role", [])),
        "scopes": list(token.scopes),
        "issuer": claims.get("iss"),
        "auth_type": "mcp",
        "auth_provider": "mcp",
        "is_authenticated": True,
        "session_version": AUTH_SESSION_SCHEMA_VERSION,
        "application": spec.application,
    }
    body = json.dumps({"kwargs": arguments}).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": f"/{spec.application}/classcall/{spec.module}/{spec.class_name}/{spec.method}",
        "raw_path": b"",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"mcp.internal")],
        "client": ("127.0.0.1", 0),
        "server": ("mcp.internal", 443),
        "app": app,
        "state": {},
        "session": {"user": user, "session_id": f"mcp:{subject}"},
        "pytincture.mcp_user": user,
    }
    request = Request(scope, receive)
    admission = _admit_bff_call(request)
    await admission.__anext__()
    try:
        operation = await _run_bff_thread_stage(
            request,
            _registered_bff_operation,
            get_modules_path(),
            spec.module,
            spec.class_name,
            spec.method,
        )
        if operation is None:
            raise RuntimeError("Configured MCP BFF export is no longer available")
        allowed_methods = tuple(operation["http_methods"])
        method = "POST" if "POST" in allowed_methods else allowed_methods[0]
        if method == "GET" and arguments:
            raise RuntimeError("A GET-only MCP BFF tool cannot declare arguments")
        request.scope["method"] = method
        result = await class_call(
            spec.module,
            spec.class_name,
            spec.method,
            request,
            application=spec.application,
            _admission=None,
        )
        if isinstance(result, StreamingResponse):
            request.state.bff_stream_owns_slot = False
            raise RuntimeError("Streaming BFF methods cannot be exposed as MCP tools")
        return result
    finally:
        await admission.aclose()

@app.post("/logs", operation_id="postLogs", responses={200: {"description": "JSONResponse ({\"status\": \"ok\"})"}, 401: {"description": "HTTPException (if authentication fails)"}})
async def logs_endpoint(request: Request, user=Depends(require_authenticated_user)):
    _validate_csrf(request, user)
    data = await request.json()
    logger.info(
        "Browser log received correlation_id=%s keys=%s",
        getattr(request.state, "correlation_id", ""),
        sorted(data) if isinstance(data, dict) else [],
    )
    return {"status": "ok"}


# ================
# GOOGLE OAUTH2 SETUP
# ================

ENABLE_GOOGLE_AUTH = os.getenv("ENABLE_GOOGLE_AUTH", "false").lower() == "true"
ENABLE_USER_LOGIN = os.getenv("ENABLE_USER_LOGIN", "false").lower() == "true"
ENABLE_SAML_AUTH = os.getenv("ENABLE_SAML_AUTH", "false").lower() == "true"
ENABLE_MICROSOFT_AUTH = os.getenv("ENABLE_MICROSOFT_AUTH", "false").lower() == "true"
ENABLE_DEV_EMAIL_LOGIN = os.getenv("ENABLE_DEV_EMAIL_LOGIN", "false").lower() == "true"
DEV_EMAIL_LOGIN_ONLY = bool(
    ENABLE_DEV_EMAIL_LOGIN
    and ENABLE_USER_LOGIN
    and not (ENABLE_GOOGLE_AUTH or ENABLE_MICROSOFT_AUTH or ENABLE_SAML_AUTH)
)


def _authentication_enabled() -> bool:
    return bool(
        ENABLE_GOOGLE_AUTH
        or ENABLE_MICROSOFT_AUTH
        or ENABLE_USER_LOGIN
        or ENABLE_SAML_AUTH
    )

_configured_saml_secret = os.getenv("SAML_SECRET_KEY", "").strip()
_configured_legacy_secret = os.getenv("SECRET_KEY", "").strip()
SAML_SECRET_KEY = _configured_saml_secret or _configured_legacy_secret
if _authentication_enabled():
    if (
        not SAML_SECRET_KEY
        and ENABLE_DEV_EMAIL_LOGIN
        and DEV_EMAIL_LOGIN_ONLY
    ):
        SAML_SECRET_KEY = secrets.token_urlsafe(32)
        logger.warning(
            "Generated an ephemeral development session key; sessions will reset on restart"
        )
    elif len(SAML_SECRET_KEY) < 32 or len(set(SAML_SECRET_KEY)) < 8:
        raise RuntimeError(
            "Authentication requires SAML_SECRET_KEY with at least 32 random characters; "
            "generate one with `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`"
        )
else:
    # An unauthenticated development service still gets an unpredictable cookie signer.
    SAML_SECRET_KEY = SAML_SECRET_KEY or secrets.token_urlsafe(32)


if ENABLE_GOOGLE_AUTH or ENABLE_MICROSOFT_AUTH:
    try:
        from authlib.integrations.starlette_client import OAuth as _OAuth
        from authlib.integrations.starlette_client import OAuthError as _OAuthError
    except ImportError as exc:
        raise _optional_dependency_error("OAuth", "oauth", exc) from exc
    OAuth = _OAuth
    OAuthError = _OAuthError

if ENABLE_SAML_AUTH:
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth as _OneLogin_Saml2_Auth
        from onelogin.saml2.errors import (
            OneLogin_Saml2_ValidationError as _OneLogin_Saml2_ValidationError,
        )
        from onelogin.saml2.settings import (
            OneLogin_Saml2_Settings as _OneLogin_Saml2_Settings,
        )
    except ImportError as exc:
        raise _optional_dependency_error("SAML", "saml", exc) from exc
    OneLogin_Saml2_Auth = _OneLogin_Saml2_Auth
    OneLogin_Saml2_ValidationError = _OneLogin_Saml2_ValidationError
    OneLogin_Saml2_Settings = _OneLogin_Saml2_Settings

_previous_secret_value = os.getenv("AUTH_SESSION_PREVIOUS_SECRET_KEYS", "").strip()
if _previous_secret_value:
    try:
        AUTH_SESSION_PREVIOUS_SECRET_KEYS = json.loads(_previous_secret_value)
    except json.JSONDecodeError:
        AUTH_SESSION_PREVIOUS_SECRET_KEYS = [
            value.strip() for value in _previous_secret_value.split(",") if value.strip()
        ]
    if not isinstance(AUTH_SESSION_PREVIOUS_SECRET_KEYS, list) or any(
        not isinstance(value, str) or len(value) < 32 or len(set(value)) < 8
        for value in AUTH_SESSION_PREVIOUS_SECRET_KEYS
    ):
        raise RuntimeError("AUTH_SESSION_PREVIOUS_SECRET_KEYS must contain strong keys")
else:
    AUTH_SESSION_PREVIOUS_SECRET_KEYS = []

AUTH_SESSION_SCHEMA_VERSION = 2
AUTH_SESSION_MAX_AGE_SECONDS = int(os.getenv("AUTH_SESSION_MAX_AGE_SECONDS", "28800"))
if AUTH_SESSION_MAX_AGE_SECONDS <= 0:
    raise RuntimeError("AUTH_SESSION_MAX_AGE_SECONDS must be greater than zero")
AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS = int(
    os.getenv("AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS", "86400")
)
if AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS < AUTH_SESSION_MAX_AGE_SECONDS:
    raise RuntimeError(
        "AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS cannot be shorter than the idle lifetime"
    )
AUTH_SESSION_HTTPS_ONLY = os.getenv(
    "AUTH_SESSION_HTTPS_ONLY",
    "false" if DEV_EMAIL_LOGIN_ONLY else "true",
).lower() == "true"
AUTH_SESSION_SAME_SITE = os.getenv("AUTH_SESSION_SAME_SITE", "lax").lower()
if AUTH_SESSION_SAME_SITE not in {"lax", "strict", "none"}:
    raise RuntimeError("AUTH_SESSION_SAME_SITE must be lax, strict, or none")
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(2 * 1024 * 1024)))
if MAX_REQUEST_BODY_BYTES <= 0:
    raise RuntimeError("MAX_REQUEST_BODY_BYTES must be greater than zero")
AUTH_LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("AUTH_LOGIN_RATE_LIMIT_ATTEMPTS", "20"))
AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60"))
AUTH_LOGIN_EMAIL_MAX_CHARS = int(os.getenv("AUTH_LOGIN_EMAIL_MAX_CHARS", "320"))
AUTH_LOGIN_PASSWORD_MAX_CHARS = int(os.getenv("AUTH_LOGIN_PASSWORD_MAX_CHARS", "1024"))
AUTH_PASSWORD_HASH_MAX_CONCURRENCY = int(os.getenv("AUTH_PASSWORD_HASH_MAX_CONCURRENCY", "2"))
AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS = float(
    os.getenv("AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS", "1")
)
AUTH_PASSWORD_HASH_TIMEOUT_SECONDS = float(
    os.getenv("AUTH_PASSWORD_HASH_TIMEOUT_SECONDS", "15")
)
if min(
    AUTH_LOGIN_RATE_LIMIT_ATTEMPTS,
    AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    AUTH_LOGIN_EMAIL_MAX_CHARS,
    AUTH_LOGIN_PASSWORD_MAX_CHARS,
    AUTH_PASSWORD_HASH_MAX_CONCURRENCY,
    AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS,
    AUTH_PASSWORD_HASH_TIMEOUT_SECONDS,
) <= 0:
    raise RuntimeError("authentication resource limits must be greater than zero")
AUTH_LOGIN_RATE_LIMITER = SlidingWindowRateLimiter(
    AUTH_LOGIN_RATE_LIMIT_ATTEMPTS,
    AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
PASSWORD_HASH_GATE = AsyncAdmissionGate(
    AUTH_PASSWORD_HASH_MAX_CONCURRENCY,
    max(1, AUTH_PASSWORD_HASH_MAX_CONCURRENCY * 4),
    AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS,
)
BFF_CALL_TIMEOUT_SECONDS = float(os.getenv("BFF_CALL_TIMEOUT_SECONDS", "30"))
BFF_MAX_CONCURRENCY = int(os.getenv("BFF_MAX_CONCURRENCY", "32"))
BFF_MAX_QUEUE = int(os.getenv("BFF_MAX_QUEUE", "64"))
BFF_QUEUE_TIMEOUT_SECONDS = float(os.getenv("BFF_QUEUE_TIMEOUT_SECONDS", "2"))
BFF_STREAM_MAX_SECONDS = float(os.getenv("BFF_STREAM_MAX_SECONDS", "300"))
BFF_STREAM_MAX_BYTES = int(os.getenv("BFF_STREAM_MAX_BYTES", str(10 * 1024 * 1024)))
BFF_STREAM_MAX_ITEMS = int(os.getenv("BFF_STREAM_MAX_ITEMS", "10000"))
BFF_STREAM_IDLE_TIMEOUT_SECONDS = float(os.getenv("BFF_STREAM_IDLE_TIMEOUT_SECONDS", "30"))
APPCODE_MAX_FILES = int(os.getenv("APPCODE_MAX_FILES", "512"))
APPCODE_MAX_FILE_BYTES = int(os.getenv("APPCODE_MAX_FILE_BYTES", str(4 * 1024 * 1024)))
APPCODE_MAX_TOTAL_BYTES = int(os.getenv("APPCODE_MAX_TOTAL_BYTES", str(32 * 1024 * 1024)))
APPCODE_CACHE_ENTRIES = int(os.getenv("APPCODE_CACHE_ENTRIES", "16"))
APPCODE_BUILD_MAX_CONCURRENCY = int(os.getenv("APPCODE_BUILD_MAX_CONCURRENCY", "2"))
APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS = float(
    os.getenv("APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS", "1")
)
if min(
    BFF_CALL_TIMEOUT_SECONDS,
    BFF_MAX_CONCURRENCY,
    BFF_QUEUE_TIMEOUT_SECONDS,
    BFF_STREAM_MAX_SECONDS,
    BFF_STREAM_MAX_BYTES,
    BFF_STREAM_MAX_ITEMS,
    BFF_STREAM_IDLE_TIMEOUT_SECONDS,
    APPCODE_MAX_FILES,
    APPCODE_MAX_FILE_BYTES,
    APPCODE_MAX_TOTAL_BYTES,
    APPCODE_CACHE_ENTRIES,
    APPCODE_BUILD_MAX_CONCURRENCY,
    APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS,
) <= 0:
    raise RuntimeError("BFF timeout and stream limits must be greater than zero")
if BFF_MAX_QUEUE < 0:
    raise RuntimeError("BFF_MAX_QUEUE cannot be negative")
BFF_ADMISSION_GATE = AsyncAdmissionGate(
    BFF_MAX_CONCURRENCY,
    BFF_MAX_QUEUE,
    BFF_QUEUE_TIMEOUT_SECONDS,
)
APPCODE_ARCHIVE_CACHE = AppcodeArchiveCache(APPCODE_CACHE_ENTRIES)
APPCODE_BUILD_GATE = threading.BoundedSemaphore(APPCODE_BUILD_MAX_CONCURRENCY)
ENABLE_BFF_REPLAY_TOKENS = os.getenv("ENABLE_BFF_REPLAY_TOKENS", "false").lower() == "true"
BFF_REPLAY_TOKEN_BATCH_SIZE = int(os.getenv("BFF_REPLAY_TOKEN_BATCH_SIZE", "12"))
BFF_REPLAY_TOKEN_LOW_WATERMARK = int(os.getenv("BFF_REPLAY_TOKEN_LOW_WATERMARK", "3"))
BFF_REPLAY_TOKEN_TTL_SECONDS = int(os.getenv("BFF_REPLAY_TOKEN_TTL_SECONDS", "300"))
if not 1 <= BFF_REPLAY_TOKEN_BATCH_SIZE <= 100:
    raise RuntimeError("BFF_REPLAY_TOKEN_BATCH_SIZE must be between 1 and 100")
if not 0 <= BFF_REPLAY_TOKEN_LOW_WATERMARK < BFF_REPLAY_TOKEN_BATCH_SIZE:
    raise RuntimeError(
        "BFF_REPLAY_TOKEN_LOW_WATERMARK must be non-negative and below the batch size"
    )
if not 10 <= BFF_REPLAY_TOKEN_TTL_SECONDS <= AUTH_SESSION_MAX_AGE_SECONDS:
    raise RuntimeError(
        "BFF_REPLAY_TOKEN_TTL_SECONDS must be between 10 seconds and the session maximum age"
    )

config_data = {
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
    "MICROSOFT_CLIENT_ID": os.getenv("MICROSOFT_CLIENT_ID", ""),
    "MICROSOFT_CLIENT_SECRET": os.getenv("MICROSOFT_CLIENT_SECRET", ""),
    "MICROSOFT_TENANT_ID": os.getenv("MICROSOFT_TENANT_ID", ""),
    "SECRET_KEY": SAML_SECRET_KEY,
}
config = Config(environ=config_data)

SAML_EMAIL_ATTRIBUTE = os.getenv("SAML_EMAIL_ATTRIBUTE", "email")
SAML_NAME_ATTRIBUTE = os.getenv("SAML_NAME_ATTRIBUTE", "givenName")
SAML_LOGIN_LABEL = os.getenv("SAML_LOGIN_LABEL", "Login with SAML")
SAML_LOGO_URL = os.getenv("SAML_LOGO_URL", "")
SAML_PROVIDERS = os.getenv("SAML_PROVIDERS", "")
SAML_DEFAULT_REDIRECT = os.getenv("SAML_DEFAULT_REDIRECT", "")
SAML_SP_ENTITY_ID = os.getenv("SAML_SP_ENTITY_ID", "")
SAML_SP_ASSERTION_URL = os.getenv("SAML_SP_ASSERTION_CONSUMER_SERVICE_URL", "")
SAML_SP_X509_CERT = os.getenv("SAML_SP_X509_CERT", "")
SAML_SP_PRIVATE_KEY = os.getenv("SAML_SP_PRIVATE_KEY", "")
SAML_IDP_ENTITY_ID = os.getenv("SAML_IDP_ENTITY_ID", "")
SAML_IDP_SSO_URL = os.getenv("SAML_IDP_SSO_URL", "")
SAML_IDP_SLO_URL = os.getenv("SAML_IDP_SLO_URL", "")
SAML_IDP_X509_CERT = os.getenv("SAML_IDP_X509_CERT", "")
SAML_REQUESTED_AUTHN_CONTEXT = os.getenv(
    "SAML_REQUESTED_AUTHN_CONTEXT", "false"
).lower() == "true"
SAML_ALLOWED_ROLES = [
    role.strip().lower()
    for role in os.getenv("SAML_ALLOWED_ROLES", "").split(",")
    if role.strip()
]
SAML_ROLE_ATTRIBUTE_KEYS = [
    key.strip()
    for key in os.getenv("SAML_ROLE_ATTRIBUTE_KEYS", "").split(",")
    if key.strip()
]
if not SAML_ROLE_ATTRIBUTE_KEYS:
    SAML_ROLE_ATTRIBUTE_KEYS = [
        "roles",
        "role",
        "groups",
        "group",
        "http://schemas.auth0.com/roles",
        "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
    ]
SAML_RELAY_STATE_TTL_SECONDS = int(
    os.getenv(
        "SAML_RELAY_STATE_TTL_SECONDS",
        os.getenv("SAML_REQUEST_CACHE_TTL", "600"),
    )
)
if SAML_RELAY_STATE_TTL_SECONDS <= 0:
    raise RuntimeError("SAML_RELAY_STATE_TTL_SECONDS must be greater than zero")
SAML_RESPONSE_MAX_BYTES = int(os.getenv("SAML_RESPONSE_MAX_BYTES", str(512 * 1024)))
SAML_ACS_RATE_LIMIT_ATTEMPTS = int(os.getenv("SAML_ACS_RATE_LIMIT_ATTEMPTS", "60"))
SAML_ACS_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("SAML_ACS_RATE_LIMIT_WINDOW_SECONDS", "60")
)
if SAML_RESPONSE_MAX_BYTES <= 0:
    raise RuntimeError("SAML_RESPONSE_MAX_BYTES must be greater than zero")
if ENABLE_SAML_AUTH and SAML_RESPONSE_MAX_BYTES > MAX_REQUEST_BODY_BYTES:
    raise RuntimeError("SAML_RESPONSE_MAX_BYTES cannot exceed MAX_REQUEST_BODY_BYTES")
if min(SAML_ACS_RATE_LIMIT_ATTEMPTS, SAML_ACS_RATE_LIMIT_WINDOW_SECONDS) <= 0:
    raise RuntimeError("SAML ACS rate-limit values must be greater than zero")
SAML_ACS_RATE_LIMITER = SlidingWindowRateLimiter(
    SAML_ACS_RATE_LIMIT_ATTEMPTS,
    SAML_ACS_RATE_LIMIT_WINDOW_SECONDS,
)


def _split_csv(value: Any) -> List[str]:
    return split_csv(value)


def _provider_value(provider: Optional[Dict[str, Any]], *keys: str, default: Any = "") -> Any:
    return saml_provider_value(provider, *keys, default=default)


def _normalize_saml_provider_id(value: str) -> str:
    return normalize_saml_provider_id(value)


def _normalize_saml_provider(raw_provider: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    return normalize_saml_provider(raw_provider, fallback_id)


def _load_saml_providers() -> List[Dict[str, Any]]:
    return SAMLProviderCatalog(
        SAML_PROVIDERS,
        default_label=SAML_LOGIN_LABEL,
        default_logo_url=SAML_LOGO_URL,
    ).providers


def _get_saml_provider(provider_id: Optional[str] = None) -> Dict[str, Any]:
    catalog = SAMLProviderCatalog(
        SAML_PROVIDERS,
        default_label=SAML_LOGIN_LABEL,
        default_logo_url=SAML_LOGO_URL,
    )
    return catalog.get(provider_id)


def _get_saml_login_buttons() -> List[Dict[str, str]]:
    return SAMLProviderCatalog(
        SAML_PROVIDERS,
        default_label=SAML_LOGIN_LABEL,
        default_logo_url=SAML_LOGO_URL,
    ).login_buttons()


def _get_saml_allowed_roles(provider: Optional[Dict[str, Any]] = None) -> List[str]:
    return saml_allowed_roles(provider, SAML_ALLOWED_ROLES)


def _get_saml_role_attribute_keys(provider: Optional[Dict[str, Any]] = None) -> List[str]:
    return saml_role_attribute_keys(provider, SAML_ROLE_ATTRIBUTE_KEYS)


_SAML_RELAY_STATE_SALT = "pytincture-saml-relay-state-v2"
_SAML_HANDSHAKE_COOKIE_SALT = "pytincture-saml-handshake-cookie-v1"
_SAML_HANDSHAKE_COOKIE = "pytincture_saml_handshake"


def _get_saml_relay_state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        SAML_SECRET_KEY,
        salt=_SAML_RELAY_STATE_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def _sign_saml_relay_state(payload: Dict[str, Any]) -> str:
    return _get_saml_relay_state_serializer().dumps(payload)


def _load_saml_relay_state(token: Optional[str]) -> Dict[str, Any]:
    if not token:
        raise HTTPException(status_code=400, detail="SAML RelayState is required")

    try:
        payload = _get_saml_relay_state_serializer().loads(
            token,
            max_age=SAML_RELAY_STATE_TTL_SECONDS,
        )
    except SignatureExpired as exc:
        raise HTTPException(status_code=400, detail="SAML RelayState has expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=400, detail="Invalid SAML RelayState") from exc

    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise HTTPException(status_code=400, detail="Invalid SAML RelayState")

    transaction_id = payload.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise HTTPException(status_code=400, detail="Invalid SAML RelayState")

    return payload


def _get_saml_handshake_cookie_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        SAML_SECRET_KEY,
        salt=_SAML_HANDSHAKE_COOKIE_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def _saml_handshake_cookie_path(application: str) -> str:
    return f"/{quote(application, safe='')}/auth/saml"


def _set_saml_handshake_cookie(
    response: Response,
    application: str,
    transaction: Dict[str, Any],
) -> None:
    response.set_cookie(
        _SAML_HANDSHAKE_COOKIE,
        _get_saml_handshake_cookie_serializer().dumps(transaction),
        max_age=SAML_RELAY_STATE_TTL_SECONDS,
        path=_saml_handshake_cookie_path(application),
        secure=AUTH_SESSION_HTTPS_ONLY,
        httponly=True,
        # Cross-site POST binding requires SameSite=None in HTTPS deployments.
        # Lax keeps explicit HTTP-only local development usable.
        samesite="none" if AUTH_SESSION_HTTPS_ONLY else "lax",
    )


def _load_saml_handshake_cookie(
    request: Request,
    transaction_id: str,
) -> Dict[str, Any]:
    token = request.cookies.get(_SAML_HANDSHAKE_COOKIE, "")
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired SAML login")
    try:
        transaction = _get_saml_handshake_cookie_serializer().loads(
            token,
            max_age=SAML_RELAY_STATE_TTL_SECONDS,
        )
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired SAML login",
        ) from exc
    if (
        not isinstance(transaction, dict)
        or transaction.get("version") != 1
        or not hmac.compare_digest(
            str(transaction.get("transaction_id", "")),
            transaction_id,
        )
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired SAML login")
    return transaction


def _delete_saml_handshake_cookie(response: Response, application: str) -> None:
    response.delete_cookie(
        _SAML_HANDSHAKE_COOKIE,
        path=_saml_handshake_cookie_path(application),
        secure=AUTH_SESSION_HTTPS_ONLY,
        httponly=True,
        samesite="none" if AUTH_SESSION_HTTPS_ONLY else "lax",
    )


def _saml_replay_proof(
    transaction_id: str,
    response_id: str,
    assertion_id: str,
) -> str:
    message = "\0".join((transaction_id, response_id, assertion_id)).encode("utf-8")
    return hmac.new(
        SAML_SECRET_KEY.encode("utf-8"),
        b"pytincture-saml-replay-v1\0" + message,
        hashlib.sha256,
    ).hexdigest()


def _replace_saml_relay_state(
    saml_auth: OneLogin_Saml2_Auth,
    auth_url: str,
    relay_state: str,
) -> str:
    """Replace the placeholder RelayState and refresh any redirect signature."""
    parsed_url = urlsplit(auth_url)
    parameters = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    if not parameters.get("SAMLRequest"):
        raise RuntimeError("SAML login URL is missing SAMLRequest")

    parameters["RelayState"] = relay_state
    parameters.pop("Signature", None)
    parameters.pop("SigAlg", None)

    security = saml_auth.get_settings().get_security_data()
    if security.get("authnRequestsSigned", False):
        saml_auth.add_request_signature(
            parameters,
            security["signatureAlgorithm"],
        )

    base_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
    return saml_auth.redirect_to(base_url, parameters)


def _normalize_certificate(value: str) -> str:
    """
    Ensure certificate/key values pulled from environment variables are newline-normalized.
    """
    if not value:
        return value
    return value.replace("\\n", "\n").strip()


def _strip_pem_headers(value: str) -> str:
    cleaned = value.replace("-----BEGIN CERTIFICATE-----", "")
    cleaned = cleaned.replace("-----END CERTIFICATE-----", "")
    return cleaned.replace("\n", "").replace("\r", "").replace(" ", "")


def _certificate_fingerprint(value: str) -> Optional[str]:
    """
    Return the SHA1 fingerprint for a PEM or raw base64 certificate.
    """
    if not value:
        return None
    normalized = _normalize_certificate(value)
    pem_pattern = r"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----"
    matches = re.findall(pem_pattern, normalized, flags=re.DOTALL)
    if matches:
        raw_body = matches[0]
    else:
        raw_body = normalized
    body = "".join(raw_body.split())
    try:
        der = base64.b64decode(body)
    except Exception as exc:
        logger.debug("Failed to decode certificate for fingerprint", exc_info=exc)
        return None
    return hashlib.sha1(der).hexdigest()


def _extract_response_certificates(xml_payload: str) -> List[str]:
    """
    Extract embedded ds:X509Certificate values from a decoded SAML response.
    """
    try:
        ns = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
        root = ElementTree.fromstring(xml_payload)
        nodes = root.findall(".//ds:Signature/ds:KeyInfo/ds:X509Data/ds:X509Certificate", ns)
        return [
            (node.text or "").strip()
            for node in nodes
            if (node.text or "").strip()
        ]
    except Exception as exc:
        logger.debug("Failed to parse SAML XML certificates", exc_info=exc)
        return []


def _extract_request_origin(request: Request) -> Dict[str, Any]:
    """
    Resolve protocol, host, and port taking reverse proxy headers into account.
    """
    if CANONICAL_ORIGIN:
        canonical = urlsplit(CANONICAL_ORIGIN)
        port = canonical.port or (443 if canonical.scheme == "https" else 80)
        return {
            "protocol": canonical.scheme,
            "host": canonical.hostname,
            "host_with_port": canonical.netloc,
            "port": port,
            "base_url": CANONICAL_ORIGIN,
        }

    protocol = request.url.scheme
    forwarded_host = None
    if TRUST_PROXY_HEADERS:
        protocol = request.headers.get("x-forwarded-proto") or protocol
        forwarded_host = request.headers.get("x-forwarded-host")
    host_header = forwarded_host or request.headers.get("host")
    hostname = request.url.hostname or "localhost"
    host = hostname

    port = request.url.port
    if port is None:
        port = 443 if protocol == "https" else 80

    if host_header:
        if ":" in host_header:
            potential_host, potential_port = host_header.split(":", 1)
            host = potential_host.strip() or hostname
            try:
                port = int(potential_port)
            except ValueError:
                port = port
        else:
            host = host_header.strip() or hostname

    forwarded_port = request.headers.get("x-forwarded-port") if TRUST_PROXY_HEADERS else None
    if forwarded_port:
        try:
            port = int(forwarded_port)
        except ValueError:
            pass

    default_port = 443 if protocol == "https" else 80
    if host_header:
        host_with_port = host_header
    else:
        host_with_port = host if port == default_port else f"{host}:{port}"

    base_url = f"{protocol}://{host_with_port}"
    return {
        "protocol": protocol,
        "host": host,
        "host_with_port": host_with_port,
        "port": port,
        "base_url": base_url,
    }


def _apply_saml_template(value: str, application: str, origin: Dict[str, Any]) -> str:
    """
    Replace supported placeholders in configuration strings.
    """
    if not value:
        return value
    return (
        value
        .replace("{application}", application)
        .replace("{base_url}", origin["base_url"])
        .replace("{host}", origin["host"])
        .replace("{host_with_port}", origin["host_with_port"])
        .replace("{protocol}", origin["protocol"])
    )


def _debug_session_state(stage: str, request: Request) -> None:
    """
    Emit diagnostic information about the Starlette session + cookies.
    """
    try:
        cookie_value = request.cookies.get("session")
        cookie_present = cookie_value is not None
        cookie_length = len(cookie_value) if cookie_present else 0
        session_keys = list(request.session.keys())
        logger.debug(
            "SAML session stage=%s cookie_present=%s cookie_size=%s keys=%s",
            stage,
            cookie_present,
            cookie_length,
            session_keys,
        )
    except Exception as exc:  # pragma: no cover - diagnostics only
        logger.debug("Unable to inspect SAML session stage=%s", stage, exc_info=exc)


def _build_saml_settings(request: Request, application: str, provider: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Construct the settings dict consumed by python3-saml using runtime request data.
    """
    origin = _extract_request_origin(request)

    sp_entity_id = _provider_value(provider, "sp_entity_id", "spEntityId", default=SAML_SP_ENTITY_ID)
    sp_assertion_url = _provider_value(
        provider,
        "sp_assertion_consumer_service_url",
        "sp_acs_url",
        "acs_url",
        "spAssertionConsumerServiceUrl",
        default=SAML_SP_ASSERTION_URL,
    )
    sp_cert_value = _provider_value(provider, "sp_x509_cert", "sp_cert", "spX509Cert", default=SAML_SP_X509_CERT)
    sp_key_value = _provider_value(provider, "sp_private_key", "spPrivateKey", default=SAML_SP_PRIVATE_KEY)
    idp_entity_value = _provider_value(provider, "idp_entity_id", "idpEntityId", default=SAML_IDP_ENTITY_ID)
    idp_sso_value = _provider_value(provider, "idp_sso_url", "idpSsoUrl", default=SAML_IDP_SSO_URL)
    idp_slo_value = _provider_value(provider, "idp_slo_url", "idpSloUrl", default=SAML_IDP_SLO_URL)
    idp_cert_value = _provider_value(provider, "idp_x509_cert", "idp_cert", "idpX509Cert", default=SAML_IDP_X509_CERT)

    default_entity = f"{origin['base_url']}/{application}/auth/saml/metadata"
    entity_id = _apply_saml_template(sp_entity_id or default_entity, application, origin)

    default_acs = f"{origin['base_url']}/{application}/auth/saml/acs"
    if not sp_assertion_url and entity_id:
        parsed_entity = urlparse(entity_id)
        if parsed_entity.scheme and parsed_entity.netloc:
            default_acs = f"{parsed_entity.scheme}://{parsed_entity.netloc}/{application}/auth/saml/acs"
    acs_url = _apply_saml_template(sp_assertion_url or default_acs, application, origin)

    idp_entity = _apply_saml_template(idp_entity_value, application, origin)
    idp_sso = _apply_saml_template(idp_sso_value, application, origin)
    idp_slo = _apply_saml_template(idp_slo_value, application, origin) if idp_slo_value else ""
    idp_cert = _normalize_certificate(idp_cert_value)

    if not idp_entity or not idp_sso or not idp_cert:
        raise RuntimeError("SAML IdP configuration is incomplete. Ensure each provider has idp_entity_id, idp_sso_url, and idp_x509_cert, or set SAML_IDP_ENTITY_ID, SAML_IDP_SSO_URL, and SAML_IDP_X509_CERT.")

    sp_settings: Dict[str, Any] = {
        "entityId": entity_id,
        "assertionConsumerService": {
            "url": acs_url,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    }

    sp_cert = _normalize_certificate(sp_cert_value)
    sp_key = _normalize_certificate(sp_key_value)
    if sp_cert:
        sp_settings["x509cert"] = sp_cert
    if sp_key:
        sp_settings["privateKey"] = sp_key

    idp_settings: Dict[str, Any] = {
        "entityId": idp_entity,
        "singleSignOnService": {
            "url": idp_sso,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        },
        "x509cert": idp_cert,
    }

    if idp_slo:
        idp_settings["singleLogoutService"] = {
            "url": idp_slo,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }

    debug_enabled = os.getenv("SAML_DEBUG", "false").lower() == "true"
    return {
        "strict": True,
        "debug": debug_enabled,
        "sp": sp_settings,
        "idp": idp_settings,
        "security": {
            "requestedAuthnContext": SAML_REQUESTED_AUTHN_CONTEXT,
        },
    }


def _build_saml_request_data(request: Request, post_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Translate a FastAPI Request into the structure expected by python3-saml.
    """
    origin = _extract_request_origin(request)
    return {
        "https": "on" if origin["protocol"] == "https" else "off",
        "http_host": origin["host_with_port"],
        "server_port": str(origin["port"]),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": post_data or {},
    }


def _init_saml_auth(request: Request, application: str, provider: Optional[Dict[str, Any]] = None, post_data: Optional[Dict[str, Any]] = None) -> OneLogin_Saml2_Auth:
    """
    Convenience wrapper to instantiate a SAML Auth client.
    """
    request_data = _build_saml_request_data(request, post_data=post_data)
    settings = _build_saml_settings(request, application, provider=provider)
    return OneLogin_Saml2_Auth(request_data, old_settings=settings)


def _get_saml_default_redirect(application: str, request: Request, provider: Optional[Dict[str, Any]] = None) -> str:
    """
    Produce the default redirect target using optional templates.
    """
    origin = _extract_request_origin(request)
    default_target = f"/{application}"
    default_redirect = _provider_value(provider, "default_redirect", "defaultRedirect", default=SAML_DEFAULT_REDIRECT)
    configured_target = _apply_saml_template(default_redirect, application, origin) if default_redirect else ""
    return configured_target or default_target


def _get_saml_attribute(attributes: Dict[str, List[str]], attribute_name: str) -> Optional[str]:
    """
    Helper to fetch the first attribute value, returning None if missing.
    """
    if not attribute_name:
        return None

    values = attributes.get(attribute_name)
    if values is None:
        lowered_lookup = {key.lower(): key for key in attributes.keys()}
        matched_key = lowered_lookup.get(attribute_name.lower())
        if matched_key:
            values = attributes.get(matched_key)

    if not values:
        return None
    if isinstance(values, list):
        return values[0]
    return values


def _sanitize_return_to(value: Optional[str]) -> Optional[str]:
    """
    Ensure return_to targets remain on the same origin by allowing only relative URLs.
    """
    if not value:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return None

    path = parsed.path or ""
    if not path.startswith("/") or path.startswith("//"):
        return None

    sanitized = path
    if parsed.query:
        sanitized += f"?{parsed.query}"
    if parsed.fragment:
        sanitized += f"#{parsed.fragment}"
    return sanitized

# Create an OAuth object and register supported providers
if ENABLE_GOOGLE_AUTH or ENABLE_MICROSOFT_AUTH:
    oauth = OAuth(config)
    if ENABLE_GOOGLE_AUTH:
        oauth.register(
            name="google",
            client_id=config.get("GOOGLE_CLIENT_ID"),
            client_secret=config.get("GOOGLE_CLIENT_SECRET"),
            # Use the well-known OIDC discovery document
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if ENABLE_MICROSOFT_AUTH:
        microsoft_tenant_id = config.get("MICROSOFT_TENANT_ID")
        if (
            not microsoft_tenant_id
            or str(microsoft_tenant_id).casefold()
            in {"common", "organizations", "consumers"}
            or re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",
                str(microsoft_tenant_id),
            )
            is None
        ):
            raise RuntimeError(
                "Microsoft authentication requires one explicit MICROSOFT_TENANT_ID"
            )
        oauth.register(
            name="microsoft",
            client_id=config.get("MICROSOFT_CLIENT_ID"),
            client_secret=config.get("MICROSOFT_CLIENT_SECRET"),
            server_metadata_url=f"https://login.microsoftonline.com/{microsoft_tenant_id}/v2.0/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile offline_access"},
        )
else:
    oauth = None

# Add session middleware (needed to store "return_to" and user info)
app.add_middleware(
    RotatingSessionMiddleware,
    secret_key=SAML_SECRET_KEY,
    previous_secret_keys=AUTH_SESSION_PREVIOUS_SECRET_KEYS,
    max_age=AUTH_SESSION_MAX_AGE_SECONDS,
    absolute_max_age=AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS,
    same_site=AUTH_SESSION_SAME_SITE,
    https_only=AUTH_SESSION_HTTPS_ONLY,
)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

# ================
# SAML SSO SETUP
# ================

@app.get(
    "/{application}/auth/saml/login",
    operation_id="initiateSamlAuth",
    response_class=RedirectResponse,
    responses={
        302: {"description": "RedirectResponse (to IdP login)"},
        404: {"description": "HTTPException (if SAML disabled)"},
        500: {"description": "HTTPException (if configuration error)"},
    },
)
async def saml_login(request: Request, application: str):
    return await _saml_login(request, application)


@app.get(
    "/{application}/auth/saml/{provider_id}/login",
    operation_id="initiateSamlProviderAuth",
    response_class=RedirectResponse,
    responses={
        302: {"description": "RedirectResponse (to IdP login)"},
        404: {"description": "HTTPException (if SAML disabled or provider missing)"},
        500: {"description": "HTTPException (if configuration error)"},
    },
)
async def saml_provider_login(request: Request, application: str, provider_id: str):
    return await _saml_login(request, application, provider_id=provider_id)


async def _saml_login(request: Request, application: str, provider_id: Optional[str] = None):
    """
    Redirect the user to the configured SAML Identity Provider.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    provider = _get_saml_provider(provider_id)

    _debug_session_state("saml_login:entry", request)
    return_to = request.query_params.get("return_to")
    safe_return_to = _sanitize_return_to(return_to)
    if safe_return_to:
        request.session["return_to"] = safe_return_to

    try:
        saml_auth = _init_saml_auth(request, application, provider=provider)
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail="SAML configuration error") from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="SAML initialization failed") from exc

    session_return_to = _sanitize_return_to(request.session.pop("return_to", None))
    fallback_return = safe_return_to or session_return_to
    auth_url = saml_auth.login(return_to="pytincture-relay-state")
    request_id = saml_auth.get_last_request_id()
    if not request_id:
        raise HTTPException(status_code=500, detail="SAML login did not generate a request ID")

    transaction_id = secrets.token_urlsafe(32)
    transaction_record = {
        "version": 1,
        "transaction_id": transaction_id,
        "application": application,
        "provider_id": provider["id"],
        "request_id": request_id,
        "return_to": fallback_return,
    }

    relay_token = _sign_saml_relay_state(
        {"version": 2, "transaction_id": transaction_id}
    )
    auth_url = _replace_saml_relay_state(saml_auth, auth_url, relay_token)
    request.session.pop("saml_request_id", None)
    request.session.pop("saml_provider_id", None)
    response = RedirectResponse(url=auth_url)
    _set_saml_handshake_cookie(response, application, transaction_record)
    return response


@app.post(
    "/{application}/auth/saml/acs",
    operation_id="handleSamlAuthCallback",
    response_class=RedirectResponse,
    responses={
        302: {"description": "RedirectResponse (to original path after login)"},
        400: {
            "description": "HTTPException (if SAML response invalid)"},
        401: {"description": "HTTPException (if user not authorized)"},
        429: {"description": "HTTPException (if ACS rate limit is exceeded)"},
        404: {"description": "HTTPException (if SAML disabled)"},
    },
)
async def saml_assertion_consumer(request: Request, application: str):
    return await _saml_assertion_consumer(request, application)


@app.post(
    "/{application}/auth/saml/{provider_id}/acs",
    operation_id="handleSamlProviderAuthCallback",
    response_class=RedirectResponse,
    responses={
        302: {"description": "RedirectResponse (to original path after login)"},
        400: {"description": "HTTPException (if SAML response invalid)"},
        401: {"description": "HTTPException (if user not authorized)"},
        429: {"description": "HTTPException (if ACS rate limit is exceeded)"},
        404: {"description": "HTTPException (if SAML disabled or provider missing)"},
    },
)
async def saml_provider_assertion_consumer(request: Request, application: str, provider_id: str):
    return await _saml_assertion_consumer(request, application, provider_id=provider_id)


def _saml_acs_peer_key(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                peer = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return str(peer)[:128]


async def _saml_assertion_consumer(request: Request, application: str, provider_id: Optional[str] = None):
    """
    Handle the assertion consumer service (ACS) endpoint invoked by the IdP.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    allowed, retry_after = SAML_ACS_RATE_LIMITER.allow(_saml_acs_peer_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many SAML authentication attempts",
            headers={"Retry-After": str(retry_after)},
        )

    _debug_session_state("saml_acs:entry", request)
    form_data = await request.form()
    post_data = dict(form_data.multi_items())
    try:
        validate_saml_response_xml(
            post_data.get("SAMLResponse", ""),
            SAML_RESPONSE_MAX_BYTES,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "SAML response pre-validation failed correlation_id=%s reason=%s",
            getattr(request.state, "correlation_id", ""),
            type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail="Invalid SAML response") from exc
    relay_token = post_data.get("RelayState")
    relay_state = _load_saml_relay_state(relay_token)
    transaction_id = relay_state["transaction_id"]
    transaction = _load_saml_handshake_cookie(request, transaction_id)
    if transaction.get("application") != application:
        raise HTTPException(status_code=400, detail="Invalid or expired SAML login")

    state_provider_id = transaction.get("provider_id")
    if not isinstance(state_provider_id, str) or not state_provider_id:
        raise HTTPException(status_code=400, detail="Invalid or expired SAML login")
    resolved_provider_id = provider_id or state_provider_id
    provider = _get_saml_provider(resolved_provider_id)
    if provider["id"] != state_provider_id:
        raise HTTPException(status_code=400, detail="SAML provider mismatch")
    
    try:
        saml_auth = _init_saml_auth(request, application, provider=provider, post_data=post_data)
        request_id = transaction.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise HTTPException(status_code=400, detail="Invalid or expired SAML login")
        request.session.pop("saml_request_id", None)
        request.session.pop("saml_provider_id", None)
        try:
            saml_auth.process_response(request_id=request_id)
        except OneLogin_Saml2_ValidationError as validation_error:
            logger.warning(
                "SAML response validation failed correlation_id=%s code=%s",
                getattr(request.state, "correlation_id", ""),
                validation_error.code,
            )
            raise
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail="SAML configuration error") from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid SAML response") from exc

    errors = saml_auth.get_errors()
    if errors:
        logger.warning(
            "SAML response rejected correlation_id=%s error_codes=%s",
            getattr(request.state, "correlation_id", ""),
            errors,
        )
        raise HTTPException(status_code=400, detail="Invalid SAML response")

    if not saml_auth.is_authenticated():
        raise HTTPException(status_code=401, detail="SAML authentication failed")

    response_in_response_to = saml_auth.get_last_response_in_response_to()
    response_id = saml_auth.get_last_message_id()
    assertion_id = saml_auth.get_last_assertion_id()
    if (
        response_in_response_to != request_id
        or not isinstance(response_id, str)
        or not response_id
        or not isinstance(assertion_id, str)
        or not assertion_id
    ):
        raise HTTPException(status_code=400, detail="Invalid SAML response correlation")

    replay_proof = _saml_replay_proof(transaction_id, response_id, assertion_id)
    previous_replay_proof = str(request.session.get("saml_replay_proof", ""))
    if previous_replay_proof and hmac.compare_digest(
        previous_replay_proof,
        replay_proof,
    ):
        raise HTTPException(status_code=400, detail="Invalid or replayed SAML login")

    consumed_transaction = transaction

    attributes = saml_auth.get_attributes()
    
    email_candidate_keys = [
        key for key in [
            SAML_EMAIL_ATTRIBUTE,
            "email",
            "mail",
            "emailaddress",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
        ]
        if key
    ]

    email_attr = None
    for candidate_key in email_candidate_keys:
        email_attr = _get_saml_attribute(attributes, candidate_key)
        if email_attr:
            break

    if not email_attr:
        email_attr = saml_auth.get_nameid()

    if not email_attr:
        raise HTTPException(status_code=400, detail="SAML response missing required email attribute")

    name_attr = _get_saml_attribute(attributes, SAML_NAME_ATTRIBUTE) if SAML_NAME_ATTRIBUTE else None

    # Normalize attributes so they are JSON serializable for session storage.
    normalized_attributes: Dict[str, List[str]] = {
        key: list(values) if isinstance(values, (list, tuple)) else [values]
        for key, values in attributes.items()
    }

    allowed_roles = _get_saml_allowed_roles(provider)
    role_attribute_keys = _get_saml_role_attribute_keys(provider)
    role_values: List[str] = []
    normalized_key_map = {key.lower(): key for key in normalized_attributes.keys()}
    for candidate_key in role_attribute_keys:
        matched_key = normalized_key_map.get(candidate_key.lower())
        if matched_key:
            role_values.extend(normalized_attributes.get(matched_key, []))
    session_roles = _normalize_auth_roles(role_values)

    if allowed_roles:
        flattened_roles = set(session_roles)
        has_allowed_role = any(role in flattened_roles for role in allowed_roles)
        if not has_allowed_role:
            raise HTTPException(status_code=401, detail="Not authorized for this application")

    user_info = {
        "email": email_attr,
        "name": name_attr or "",
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "saml",
        "auth_provider": provider["id"],
        "auth_provider_label": provider.get("label") or provider["id"],
        "roles": session_roles,
        "saml": {
            "provider_id": provider["id"],
            "provider_label": provider.get("label") or provider["id"],
            "name_id": saml_auth.get_nameid(),
        },
    }

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = [email.strip().lower() for email in os.getenv("ALLOWED_EMAILS", "").split(",") if email.strip()]
        if email_attr.lower() not in allowed_emails:
            raise HTTPException(status_code=401, detail="Not authorized")

    _set_authenticated_user(request, user_info, application=application)
    request.session["saml_replay_proof"] = replay_proof

    cached_redirect = _sanitize_return_to(consumed_transaction.get("return_to"))
    session_redirect = _sanitize_return_to(request.session.pop("return_to", None))
    if not cached_redirect:
        cached_redirect = session_redirect
    redirect_target = cached_redirect or _get_saml_default_redirect(application, request, provider=provider)
    response = RedirectResponse(url=redirect_target, status_code=302)
    _delete_saml_handshake_cookie(response, application)
    return response


@app.get(
    "/{application}/auth/saml/metadata",
    operation_id="getSamlMetadata",
    responses={
        200: {"description": "Response (SAML metadata XML)"},
        404: {"description": "HTTPException (if SAML disabled)"},
        500: {"description": "HTTPException (if metadata generation fails)"},
    },
)
async def saml_metadata(request: Request, application: str):
    return await _saml_metadata(request, application)


@app.get(
    "/{application}/auth/saml/{provider_id}/metadata",
    operation_id="getSamlProviderMetadata",
    responses={
        200: {"description": "Response (SAML metadata XML)"},
        404: {"description": "HTTPException (if SAML disabled or provider missing)"},
        500: {"description": "HTTPException (if metadata generation fails)"},
    },
)
async def saml_provider_metadata(request: Request, application: str, provider_id: str):
    return await _saml_metadata(request, application, provider_id=provider_id)


async def _saml_metadata(request: Request, application: str, provider_id: Optional[str] = None):
    """
    Provide SP metadata for the configured SAML settings.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    try:
        provider = _get_saml_provider(provider_id)
        settings = OneLogin_Saml2_Settings(settings=_build_saml_settings(request, application, provider=provider), sp_validation_only=True)
        metadata_xml = settings.get_sp_metadata()
        errors = settings.validate_metadata(metadata_xml)
        if errors:
            allowed_errors = {"sp_acs_url_invalid", "sp_entity_id_invalid"}
            remaining_errors = [err for err in errors if err not in allowed_errors]
            if remaining_errors:
                raise HTTPException(status_code=500, detail="SAML metadata validation failed")
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail="SAML configuration error") from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Failed to generate SAML metadata") from exc

    return Response(content=metadata_xml, media_type="application/xml")

@app.get("/{application}/auth/google", operation_id="initiateGoogleAuth", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to Google OAuth URL)"}})
async def auth_google(request: Request, application: str):
    """
    Redirect the user to Google's OAuth2 screen.
    """

    if oauth is None or not ENABLE_GOOGLE_AUTH:
        raise HTTPException(status_code=404, detail="Google authentication not enabled")
    redirect_uri = f"{_request_origin(request)}/{application}/auth/google/callback"

    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/{application}/auth/google/callback", name="auth_google_callback", operation_id="handleGoogleAuthCallback", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to original path after login)"}, 401: {"description": "JSONResponse (if OAuth error or not authorized)"}})
async def auth_google_callback(request: Request, application: str):
    """
    Google redirects here after login. Authlib will exchange code for token.
    We'll store user info in the session, then redirect back to original app path.
    """
    if oauth is None or not ENABLE_GOOGLE_AUTH:
        raise HTTPException(status_code=404, detail="Google authentication not enabled")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        logger.info("Google OAuth callback rejected", exc_info=e)
        return JSONResponse({"error": "Authentication failed"}, status_code=401)
    
    user_info = token.get("userinfo") or {}
    if not user_info:
        try:
            user_info = await oauth.google.parse_id_token(request, token)
        except Exception:
            user_info = user_info or {}
    if user_info.get("email_verified") is not True:
        return JSONResponse({"error": "Email is not verified"}, status_code=401)
    if str(user_info.get("iss") or "") not in {
        "https://accounts.google.com",
        "accounts.google.com",
    } or not str(user_info.get("sub") or ""):
        return JSONResponse({"error": "Invalid identity claims"}, status_code=401)

    # You can optionally grab user info from token["userinfo"]
    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    _set_authenticated_user(
        request,
        user_info,
        application=application,
        auth_type="google",
        auth_provider="google",
        auth_provider_label="Google",
    )

    # See if we stored a "return_to" path earlier; default to "/"
    return_to = _sanitize_return_to(request.session.pop("return_to", None)) or "/"
    return RedirectResponse(url=return_to)

@app.get("/{application}/auth/microsoft", operation_id="initiateMicrosoftAuth", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to Microsoft OAuth URL)"}})
async def auth_microsoft(request: Request, application: str):
    """
    Redirect the user to Microsoft's OAuth2 screen.
    """
    if oauth is None or not ENABLE_MICROSOFT_AUTH:
        raise HTTPException(status_code=404, detail="Microsoft authentication not enabled")

    redirect_uri = f"{_request_origin(request)}/{application}/auth/microsoft/callback"

    return await oauth.microsoft.authorize_redirect(request, redirect_uri)

@app.get("/{application}/auth/microsoft/callback", name="auth_microsoft_callback", operation_id="handleMicrosoftAuthCallback", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to original path after login)"}, 401: {"description": "JSONResponse (if OAuth error or not authorized)"}})
async def auth_microsoft_callback(request: Request, application: str):
    """
    Microsoft redirects here after login. Authlib will exchange code for token.
    We'll store user info in the session, then redirect back to original app path.
    """
    if oauth is None or not ENABLE_MICROSOFT_AUTH:
        raise HTTPException(status_code=404, detail="Microsoft authentication not enabled")

    try:
        token = await oauth.microsoft.authorize_access_token(request)
    except OAuthError as e:
        logger.info("Microsoft OAuth callback rejected", exc_info=e)
        return JSONResponse({"error": "Authentication failed"}, status_code=401)

    user_info = token.get("userinfo") or {}
    if not user_info:
        try:
            user_info = await oauth.microsoft.parse_id_token(request, token)
        except Exception:
            user_info = user_info or {}

    microsoft_tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    expected_issuer = f"https://login.microsoftonline.com/{microsoft_tenant_id}/v2.0"
    if (
        not microsoft_tenant_id
        or str(user_info.get("tid") or "") != microsoft_tenant_id
        or str(user_info.get("iss") or "").rstrip("/") != expected_issuer
        or not str(user_info.get("sub") or user_info.get("oid") or "")
    ):
        return JSONResponse({"error": "Invalid tenant or identity claims"}, status_code=401)
    if not user_info.get("sub") and user_info.get("oid"):
        user_info = {**user_info, "sub": user_info["oid"]}

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    _set_authenticated_user(
        request,
        user_info,
        application=application,
        auth_type="microsoft",
        auth_provider="microsoft",
        auth_provider_label="Microsoft",
    )

    return_to = _sanitize_return_to(request.session.pop("return_to", None)) or "/"
    return RedirectResponse(url=return_to)

@app.post("/{application}/auth/logout", operation_id="logoutUser", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to login page)"}})
def logout(request: Request,  application: str):
    """
    Logs the user out of *your app only*.
    """
    user = require_authenticated_user(request)
    _validate_csrf(request, user)
    revoke_session(str(request.session.get("session_id") or ""))
    _clear_auth_session(request)
    # 2) If stored tokens in session, remove them
    # request.session.pop("token", None)

    # 3) Redirect anywhere in *your* app after local logout
    response = RedirectResponse(url=f"/{application}/login", status_code=302)
    response.delete_cookie("pytincture_csrf")
    return response

# ======================
# LOGIN PAGE
# ======================

@app.get("/{application}/login", response_class=HTMLResponse, operation_id="getLoginPage", responses={200: {"description": "HTMLResponse (login page content)"}})
async def login(request: Request, application: str):
    """
    Serves the login page with options to login via Google and/or Email/Password based on configuration.
    """
    def _resolve_auth_flag(env_name: str, default: bool) -> bool:
        env_value = os.environ.get(env_name)
        if env_value is not None:
            return env_value.lower() == "true"
        return default

    enable_google_auth = _resolve_auth_flag("ENABLE_GOOGLE_AUTH", ENABLE_GOOGLE_AUTH)
    enable_user_login = _resolve_auth_flag("ENABLE_USER_LOGIN", ENABLE_USER_LOGIN)
    enable_saml_auth = _resolve_auth_flag("ENABLE_SAML_AUTH", ENABLE_SAML_AUTH)
    enable_microsoft_auth = _resolve_auth_flag("ENABLE_MICROSOFT_AUTH", ENABLE_MICROSOFT_AUTH)
    login_help_text = os.getenv("LOGIN_HELP_TEXT", "").strip()
    login_csrf_token = secrets.token_urlsafe(32)
    request.session["login_csrf_token"] = login_csrf_token

    saml_login_buttons = _get_saml_login_buttons() if enable_saml_auth else []
    if enable_saml_auth and not enable_google_auth and not enable_user_login and not enable_microsoft_auth and len(saml_login_buttons) == 1:
        return RedirectResponse(url=f"/{application}/{saml_login_buttons[0]['href']}", status_code=302)

    # Start building the HTML content
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login</title>
        <style>
            body { 
                display: flex; 
                justify-content: center; 
                align-items: center; 
                height: 100vh; 
                background-color: #f0f2f5; 
                font-family: Arial, sans-serif;
            }
            .login-container {
                background: white;
                padding: 40px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
                text-align: center;
                width: 25vw;
            }
            .login-button {
                background-color: #4285F4;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
                text-decoration: none;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                margin: 10px 0;
                width: 80%;
                box-sizing: border-box;
            }
            .login-button img {
                width: 20px;
                height: 20px;
                object-fit: contain;
                flex: 0 0 auto;
            }
            .submit-button {
                background-color: #4285F4;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                margin: 10px 0;
                width: 80%;
            }
            .login-button:hover, .submit-button:hover {
                background-color: #357ae8;
            }
            .input-field {
                width: 100%;
                padding: 10px;
                margin: 10px 0;
                border: 1px solid #ccc;
                border-radius: 4px;
                box-sizing: border-box;
            }
            .divider {
                margin: 20px 0;
                border-bottom: 1px solid #ccc;
                position: relative;
            }
            .divider span {
                background: white;
                padding: 0 10px;
                position: absolute;
                top: -10px;
                left: 50%;
                transform: translateX(-50%);
                color: #777;
            }
            .login-help-text {
                margin: 12px 0;
                padding: 10px 12px;
                border-radius: 4px;
                background: #eef4ff;
                color: #294a7a;
                font-size: 14px;
                line-height: 1.4;
                white-space: pre-line;
            }
        </style>
    </head>
    <body>
        <div class="login-container">
            <h2>Welcome</h2>
            <p>Please log in to continue</p>
    """

    if login_help_text:
        html_content += (
            f'<p class="login-help-text">{escape(login_help_text)}</p>'
        )

    social_buttons = []

    if enable_google_auth:
        social_buttons.append(
            '<a href="auth/google" class="login-button">Login with Google</a>'
        )

    if enable_microsoft_auth:
        social_buttons.append(
            '<a href="auth/microsoft" class="login-button">Login with Microsoft</a>'
        )

    if enable_saml_auth:
        for button in saml_login_buttons:
            label = escape(button["label"])
            logo_url = button.get("logo_url") or ""
            logo_html = ""
            if logo_url:
                safe_logo_url = escape(logo_url)
                logo_html = f'<img src="{safe_logo_url}" alt="" aria-hidden="true">'
            social_buttons.append(
                f'<a href="{escape(button["href"])}" class="login-button">{logo_html}<span>{label}</span></a>'
            )

    if social_buttons:
        html_content += "\n".join(social_buttons)

    # Conditionally add Email/Password login form
    if enable_user_login:
        if social_buttons:
            # Add a divider if both login methods are available
            html_content += '''
                <div class="divider"><span>OR</span></div>
            '''
        html_content += f'''
            <form method="post" action="auth/user">
                <input type="hidden" name="login_csrf_token" value="{login_csrf_token}">
                <input type="email" name="email" class="input-field" placeholder="Email" required>
                <input type="password" name="password" class="input-field" placeholder="Password" required>
                <input type="submit" class="submit-button" value="Login with Email"></input>
            </form>
        '''

    # Handle case where no login methods are enabled
    if not (enable_google_auth or enable_user_login or enable_saml_auth or enable_microsoft_auth):
        html_content += '''
            <p>No login methods are currently available. Please contact support.</p>
        '''

    # Close the HTML tags
    html_content += """
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content, status_code=200)

@app.post("/{application}/auth/user", operation_id="handleUserAuth", response_class=RedirectResponse, responses={303: {"description": "RedirectResponse (to original path after login)"}, 401: {"description": "JSONResponse (if not authorized)"}})
async def auth_user_callback(request: Request, application: str):
    """
    User logs in via Email/Password.
    """

    form = await request.form()
    _validate_preauthentication_request(
        request, str(form.get("login_csrf_token") or "")
    )
    request.session.pop("login_csrf_token", None)
    email = str(form.get('email') or "")
    password = str(form.get('password') or "")
    authenticated_claims = await _authenticate_local_user(request, email, password)

    user_info = {
        **authenticated_claims,
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "user",
        "roles": authenticated_claims.get("roles", authenticated_claims.get("role", [])),
    }

    _set_authenticated_user(request, user_info, application=application)

    # See if we stored a "return_to" path earlier; default to "/{application}"
    return_to = _sanitize_return_to(request.session.pop("return_to", None)) or f"/{application}"
    return RedirectResponse(url=return_to, status_code=303)

# Pydantic model for MCP auth input
class MCPAuthInput(BaseModel):
    email: str
    password: str

@app.post("/{application}/auth/mcp", operation_id="mcpAuth", responses={200: {"description": "JSONResponse with status and session cookie"}, 401: {"description": "HTTPException if not authorized"}, 403: {"description": "HTTPException if user login not enabled"}})
async def mcp_auth(request: Request, application: str, auth_input: MCPAuthInput = Body(...)):
    """
    MCP-specific authentication endpoint. Authenticates with email and password via JSON, sets session, and returns status. The response includes Set-Cookie header for session, which can be used in subsequent calls.
    """
    authenticated_claims = await _authenticate_local_user(
        request, auth_input.email, auth_input.password
    )
    user_info = {
        **authenticated_claims,
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "user",
        "roles": authenticated_claims.get("roles", authenticated_claims.get("role", [])),
    }

    session_user = _set_authenticated_user(
        request, user_info, application=application
    )

    return {**session_user, "status": "authenticated"}

# ======================
# The /{application} route
# ======================
@app.get("/{application}", response_class=HTMLResponse, operation_id="getMainApp", responses={200: {"description": "HTMLResponse (modified index.html with widgetset)"}, 302: {"description": "RedirectResponse (to login if not authenticated)"}})
async def main_app_route(response: Response, application: str, request: Request):
    """
    1) Check if user is in session.
    2) If not, store this path in session, redirect to /login.
    3) If yes, serve the index.html with the relevant widgetset replaced.
    """
    try:
        validate_application_name(application)
    except ValueError:
        raise HTTPException(status_code=404, detail="Application not found")

    # Check session
    try:
        user_session = require_auth(request)
    except HTTPException as auth_error:
        if auth_error.status_code != 401:
            raise
        _clear_auth_session(request)
        request.session["return_to"] = f"/{application}"
        return RedirectResponse(url=f"/{application}/login")

    if (
        ENABLE_USER_LOGIN
        or ENABLE_GOOGLE_AUTH
        or ENABLE_MICROSOFT_AUTH
        or ENABLE_SAML_AUTH
    ) and not user_session:
        # Not logged in, so remember where they wanted to go:
        request.session["return_to"] = f"/{application}"
        # Then send them to Login page:
        return RedirectResponse(url=f"/{application}/login")

    try:
        _assert_application_audience(user_session, application)
    except HTTPException:
        request.session["return_to"] = f"/{application}"
        return RedirectResponse(url=f"/{application}/login")

    # Already logged in, proceed normally
    appcode_folder = get_modules_path()
    try:
        secure_entrypoint = read_contained_file(
            appcode_folder, f"{application}.py"
        )
    except UnsafePath:
        raise HTTPException(status_code=404, detail="Application not found")
    widgetset = get_widgetset(application, appcode_folder)
    safe_application = escape(application)
    request_uuid = FRONTEND_INSTANCE_UUID

    # Modify the index.html to include the application name and widgetset
    index_html = open(f"{STATIC_PATH}/index.html").read()
    index_html = index_html.replace("***APPLICATION***", safe_application)
    
    entrypoint_source = decode_python_source(secure_entrypoint.content)

    # Find the proper entrypoint class without importing browser code here.
    app_file_path = secure_entrypoint.path
    try:
        main_window_class = find_main_window_subclass(
            app_file_path,
            source_code=entrypoint_source,
        )
    except EntryPointDiscoveryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if main_window_class:
        # Use the discovered MainWindow subclass name as the entrypoint
        index_html = index_html.replace("***ENTRYPOINT***", main_window_class)
    else:
        # If no MainWindow subclass is found, fallback to using application name
        index_html = index_html.replace("***ENTRYPOINT***", safe_application)
    
    loading_title = application
    favicon_markup = ""
    loading_title = find_app_loading_title(
        app_file_path,
        application,
        source_code=entrypoint_source,
    )
    favicon_markup = build_app_favicon_markup(
        application,
        app_file_path,
        request_uuid=request_uuid,
        source_code=entrypoint_source,
    )
    index_html = index_html.replace("***LOADING_TITLE***", json.dumps(loading_title))
    index_html = index_html.replace("***FAVICON_LINK***", favicon_markup)
    index_html = index_html.replace("***REQUEST_UUID***", request_uuid)

    index_html = index_html.replace("***WIDGETSET***", widgetset)
    return HTMLResponse(
        content=index_html,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )

def find_main_window_subclass(file_path, *, expected_digest=None, source_code=None):
    """
    Scans a Python file for a class that subclasses MainWindow.
    Returns the name of the first such class found, or None if no match.
    """
    if source_code is None:
        secure_source = read_contained_file(
            os.path.dirname(os.fspath(file_path)),
            os.path.basename(os.fspath(file_path)),
        )
        if expected_digest and secure_source.digest != expected_digest:
            raise EntryPointDiscoveryError(
                "Browser entrypoint source changed during discovery"
            )
        source_code = decode_python_source(secure_source.content)
    return _find_main_window(file_path, source_code=source_code)

def _find_app_string_setting(
    file_path, assignment_names, config_keys, *, source_code=None
):
    """
    Read a string setting from app source without importing the application.
    """
    return find_app_string_setting(
        file_path,
        assignment_names,
        config_keys,
        source_code=source_code,
    )


def find_app_loading_title(file_path, default_title, *, source_code=None):
    """
    Read APP_TITLE, APP_LOADING_TITLE, or the matching APP_CONFIG value.
    """
    return _find_app_string_setting(
        file_path,
        assignment_names=("APP_TITLE", "APP_LOADING_TITLE"),
        config_keys=("title", "loading_title"),
        source_code=source_code,
    ) or default_title


def _normalize_app_asset_path(value: Optional[str]) -> Optional[str]:
    return normalize_app_asset_path(value)


def _find_explicit_app_favicon(file_path, *, source_code=None) -> Optional[str]:
    configured = _find_app_string_setting(
        file_path,
        assignment_names=("APP_FAVICON",),
        config_keys=("favicon",),
        source_code=source_code,
    )
    return _normalize_app_asset_path(configured)


def find_app_favicon(file_path, *, source_code=None) -> Optional[str]:
    """
    Resolve an explicit favicon file/folder or a conventional favicon directory.
    """
    return _find_app_favicon_metadata(file_path, source_code=source_code)


_FAVICON_MIME_TYPES = {
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
_FAVICON_MANIFEST_NAMES = {
    "manifest.json",
    "manifest.webmanifest",
    "site.webmanifest",
}


def _is_favicon_asset(filename: str) -> bool:
    lowered = filename.lower()
    extension = os.path.splitext(lowered)[1]
    return (
        extension in _FAVICON_MIME_TYPES
        or lowered in _FAVICON_MANIFEST_NAMES
        or lowered == "browserconfig.xml"
    )


def _get_configured_favicon_root() -> Optional[str]:
    configured = os.getenv("PYTINCTURE_FAVICON_FOLDER", "").strip()
    if not configured:
        return None

    configured = os.path.expanduser(configured)
    if not os.path.isabs(configured):
        configured = os.path.join(get_modules_path(), configured)
    return os.path.realpath(configured)


def _get_configured_favicon_directory(application: str) -> Optional[str]:
    root = _get_configured_favicon_root()
    if not root or not os.path.isdir(root):
        return None

    try:
        validate_application_name(application)
        return resolve_contained_path(root, application, require_file=False)
    except (UnsafePath, ValueError):
        pass

    return root


def _find_favicon_assets_in_directory(directory: str) -> List[str]:
    assets = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name.lower()):
            if entry.is_file(follow_symlinks=False) and _is_favicon_asset(entry.name):
                assets.append(entry.name)
    return assets


def find_app_favicon_assets(file_path, *, source_code=None) -> List[str]:
    """Return the declared favicon file or supported files in its directory."""
    favicon_path = find_app_favicon(file_path, source_code=source_code)
    if not favicon_path:
        return []

    app_root = os.path.dirname(os.fspath(file_path))
    try:
        local_path = resolve_contained_path(
            app_root,
            favicon_path,
            require_file=False,
        )
    except UnsafePath:
        return [favicon_path]

    return [
        f"{favicon_path}/{filename}"
        for filename in _find_favicon_assets_in_directory(local_path)
    ]


def _favicon_size(filename: str) -> Optional[str]:
    match = re.search(r"(?<!\d)(\d{1,4})x(\d{1,4})(?!\d)", filename)
    if not match:
        return None
    return f"{match.group(1)}x{match.group(2)}"


def _build_favicon_tag(
    application: str,
    asset_path: str,
    *,
    asset_route: str = "appcode",
    request_uuid: Optional[str] = None,
) -> Optional[str]:
    favicon_url = (
        f"/{quote(application, safe='')}/{asset_route}/"
        f"{quote(asset_path, safe='/')}"
    )
    if request_uuid:
        favicon_url = f"{favicon_url}?uuid={quote(request_uuid, safe='')}"
    safe_url = escape(favicon_url)
    filename = os.path.basename(asset_path).lower()

    if filename in _FAVICON_MANIFEST_NAMES:
        return f'<link rel="manifest" href="{safe_url}">'
    if filename == "browserconfig.xml":
        return f'<meta name="msapplication-config" content="{safe_url}">'

    extension = os.path.splitext(filename)[1]
    mime_type = _FAVICON_MIME_TYPES.get(extension)
    if not mime_type:
        return None

    if filename.startswith("apple-touch-icon-precomposed"):
        relation = "apple-touch-icon-precomposed"
    elif filename.startswith("apple-touch-icon"):
        relation = "apple-touch-icon"
    elif "mask-icon" in filename or filename == "safari-pinned-tab.svg":
        relation = "mask-icon"
    else:
        relation = "icon"

    attributes = [f'rel="{relation}"', f'href="{safe_url}"', f'type="{mime_type}"']
    size = _favicon_size(filename)
    if size:
        attributes.append(f'sizes="{size}"')
    elif extension == ".svg":
        attributes.append('sizes="any"')
    return f"<link {' '.join(attributes)}>"


def build_app_favicon_markup(
    application: str,
    file_path,
    *,
    request_uuid: Optional[str] = None,
    source_code=None,
) -> str:
    """Generate browser favicon declarations for an application's assets."""
    configured_directory = None
    if _find_explicit_app_favicon(file_path, source_code=source_code) is None:
        configured_directory = _get_configured_favicon_directory(application)

    if configured_directory is not None:
        tags = [
            tag
            for asset_path in _find_favicon_assets_in_directory(configured_directory)
            if (
                tag := _build_favicon_tag(
                    application,
                    asset_path,
                    asset_route="favicon-assets",
                    request_uuid=request_uuid,
                )
            ) is not None
        ]
        if tags:
            return "\n    ".join(tags)

    tags = [
        tag
        for asset_path in find_app_favicon_assets(
            file_path, source_code=source_code
        )
        if (
            tag := _build_favicon_tag(
                application,
                asset_path,
                request_uuid=request_uuid,
            )
        ) is not None
    ]
    return "\n    ".join(tags)


@app.get(
    "/{application}/favicon-assets/{asset_name}",
    include_in_schema=False,
)
async def configured_favicon_asset(
    request: Request, application: str, asset_name: str
):
    """Serve a browser favicon asset from the launcher-configured directory."""
    if asset_name != os.path.basename(asset_name) or not _is_favicon_asset(asset_name):
        raise HTTPException(status_code=404, detail="Favicon asset not found")

    favicon_directory = _get_configured_favicon_directory(application)
    if favicon_directory is None:
        raise HTTPException(status_code=404, detail="Favicon asset not found")

    try:
        secure_file = read_contained_file(favicon_directory, asset_name)
    except UnsafePath:
        raise HTTPException(status_code=404, detail="Favicon asset not found")
    media_type = mimetypes.guess_type(secure_file.path)[0] or "application/octet-stream"
    return Response(
        content=b"" if request.method == "HEAD" else secure_file.content,
        media_type=media_type,
        headers={"Content-Length": str(secure_file.size)},
    )


add_bff_docs_to_app(app, operations=reload_bff_registry(get_modules_path()))
reload_mcp_tools()

# =================
# RUN THE APP
# =================
# Typically:
# uvicorn app:app --host 0.0.0.0 --port 8070
