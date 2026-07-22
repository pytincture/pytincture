import os
import ast
import re
from signal import raise_signal
import sys
import json
import inspect
import io
import zipfile
import importlib
import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
import uuid
import fnmatch
import copy
from xml.etree import ElementTree
# FastAPI / Starlette
from fastapi import Depends, FastAPI, Request, Response, HTTPException, Body
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

# Pytincture
from pytincture import get_modules_path
from pytincture.dataclass import get_parsed_output, add_bff_docs_to_app, get_bff_manifest
from importlib.machinery import SourceFileLoader

# Google OAuth via Authlib
from authlib.integrations.starlette_client import OAuth, OAuthError
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.concurrency import run_in_threadpool
from starlette.config import Config

from typing import Any, Union, Dict, List, Optional, Iterable, AsyncIterable, Set, Callable

# Pydantic for JSON validation
from pydantic import BaseModel

# SAML Toolkit (OneLogin)
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.errors import OneLogin_Saml2_ValidationError
from urllib.parse import parse_qsl, quote, urlparse, urlsplit, urlunsplit
from html import escape

# ========================
#  FASTAPI SETUP
# ========================

app = FastAPI(title="pyTincture API")
logger = logging.getLogger("pytincture.security")


class RotatingSessionMiddleware(SessionMiddleware):
    """Starlette sessions that accept old signing keys and re-sign with the current key."""

    def __init__(self, app, secret_key, previous_secret_keys=None, **kwargs):
        super().__init__(app, secret_key=secret_key, **kwargs)
        self.previous_signers = [
            TimestampSigner(str(key)) for key in (previous_secret_keys or []) if str(key)
        ]

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        if self.session_cookie in connection.cookies:
            signed_data = connection.cookies[self.session_cookie].encode("utf-8")
            for signer in (self.signer, *self.previous_signers):
                try:
                    decoded = signer.unsign(signed_data, max_age=self.max_age)
                    scope["session"] = json.loads(base64.b64decode(decoded))
                    initial_session_was_empty = False
                    break
                except (BadSignature, ValueError, json.JSONDecodeError):
                    continue
            else:
                scope["session"] = {}
        else:
            scope["session"] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if scope["session"]:
                    data = base64.b64encode(json.dumps(scope["session"]).encode("utf-8"))
                    signed = self.signer.sign(data).decode("utf-8")
                    max_age = f"Max-Age={self.max_age}; " if self.max_age else ""
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}={signed}; path={self.path}; "
                        f"{max_age}{self.security_flags}",
                    )
                elif not initial_session_was_empty:
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}=null; path={self.path}; "
                        f"expires=Thu, 01 Jan 1970 00:00:00 GMT; {self.security_flags}",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestBodyLimitMiddleware:
    """Reject request bodies that exceed the configured byte limit."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    response = JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Request body too large")
            return message

        try:
            await self.app(scope, limited_receive, send)
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            response = JSONResponse({"detail": "Request body too large"}, status_code=413)
            await response(scope, receive, send)


def _build_streamable_mcp_app(mcp_server, path: str = "/"):
    http_builder = getattr(mcp_server, "http_app", None)
    if callable(http_builder):
        return http_builder(path=path, transport="streamable-http")

    streamable_builder = getattr(mcp_server, "streamable_http_app", None)
    if callable(streamable_builder):
        return streamable_builder(path=path)

    raise AttributeError("FastMCP server does not expose streamable_http_app() or http_app()")


def _build_dynamic_module_name(file_path: str, name_hint: str) -> str:
    """
    Build a stable module name for manually loaded source files.

    The name includes a sanitized hint for readability and a path hash to avoid
    collisions between different files that share a class name or basename.
    """
    absolute_path = os.path.abspath(file_path)
    modules_root = os.path.abspath(get_modules_path() or os.getcwd())

    try:
        relative_path = os.path.relpath(absolute_path, modules_root)
    except ValueError:
        relative_path = os.path.basename(absolute_path)

    if relative_path.startswith(".."):
        relative_path = os.path.basename(absolute_path)

    sanitized_hint = re.sub(r"[^0-9a-zA-Z_]+", "_", name_hint).strip("_") or "module"
    sanitized_path = re.sub(r"[^0-9a-zA-Z_]+", "_", relative_path.replace("\\", "/")).strip("_") or "source"
    path_hash = hashlib.sha1(absolute_path.encode("utf-8")).hexdigest()[:12]
    return f"pytincture_dynamic_{sanitized_hint}_{sanitized_path}_{path_hash}"


def _load_source_module(file_path: str, name_hint: str):
    """
    Load a Python source file using importlib-compatible sys.modules registration.
    """
    module_name = _build_dynamic_module_name(file_path, name_hint)
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

class _FilteredFastAPIApp:
    def __init__(self, source_app: FastAPI, operation_ids: Set[str]):
        self.source_app = source_app
        self.operation_ids = operation_ids
        self.title = source_app.title

    def openapi(self):
        schema = copy.deepcopy(self.source_app.openapi())
        filtered_paths = {}
        for path, path_item in schema.get("paths", {}).items():
            selected = {
                key: value
                for key, value in path_item.items()
                if key not in {"get", "post", "put", "patch", "delete", "options", "head"}
                or value.get("operationId") in self.operation_ids
            }
            if any(key in selected for key in {"get", "post", "put", "patch", "delete"}):
                filtered_paths[path] = selected
        schema["paths"] = filtered_paths
        return schema

    async def __call__(self, scope, receive, send):
        await self.source_app(scope, receive, send)


def _mcp_operation_ids() -> Set[str]:
    if os.getenv("ENABLE_MCP", "false").lower() != "true":
        return set()
    raw = os.getenv("MCP_EXPOSED_OPERATIONS", "[]")
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP_EXPOSED_OPERATIONS must be a JSON list") from exc
    if not isinstance(configured, list) or any(not isinstance(value, str) for value in configured):
        raise RuntimeError("MCP_EXPOSED_OPERATIONS must be a JSON list")
    forbidden = {
        "handleUserAuth", "mcpAuth", "logoutUser", "postLogs",
        "downloadAppcodePackage", "getLoginPage", "getMainApp",
        "issueBffReplayTokens",
        "initiateGoogleAuth", "handleGoogleAuthCallback",
        "initiateMicrosoftAuth", "handleMicrosoftAuthCallback",
        "initiateSamlAuth", "initiateSamlProviderAuth",
        "handleSamlAuthCallback", "handleSamlProviderAuthCallback",
    }
    requested = set(configured)
    disallowed = requested & forbidden
    if disallowed:
        raise RuntimeError(
            "MCP_EXPOSED_OPERATIONS contains session/login/application routes: "
            + ", ".join(sorted(disallowed))
        )
    return requested


def reload_mcp_tools():
    global mcp, mcp_http_app  # Use globals or pass as needed if in a class/module
    
    # Step 1: Remove existing MCP-mounted routes to avoid duplicates
    # Filter out routes starting with "/mcp" to avoid duplicate mounts.
    app.router.routes = [
        route for route in app.router.routes
        if not route.path.startswith("/mcp")
    ]
    
    operation_ids = _mcp_operation_ids()
    if operation_ids:
        mcp_source = _FilteredFastAPIApp(app, operation_ids)
        mcp = FastMCP.from_fastapi(app=mcp_source, name="pytincture")
    else:
        mcp = FastMCP(name="pytincture")
    logger.info("MCP tools reloaded for operations: %s", sorted(operation_ids))
    
    # Step 3: Recreate MCP app using streamable HTTP transport
    mcp_http_app = _build_streamable_mcp_app(mcp, path='/')
    
    # Step 4: Remount the updated MCP app
    app.mount("/mcp", mcp_http_app)

def _local_python_imports(file_path: str, modules_root: str) -> Set[str]:
    """Return local Python files directly imported by a browser module."""
    try:
        with open(file_path, "r", encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read(), filename=file_path)
    except (OSError, SyntaxError):
        return set()
    discovered: Set[str] = set()
    for node in ast.walk(tree):
        candidates: List[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            candidates.append(node.module)
        for module_name in candidates:
            relative = module_name.replace(".", os.sep)
            for candidate in (
                os.path.join(modules_root, f"{relative}.py"),
                os.path.join(modules_root, relative, "__init__.py"),
            ):
                if os.path.isfile(candidate):
                    discovered.add(os.path.abspath(candidate))
    return discovered


def _configured_browser_files(modules_root: str) -> Set[str]:
    raw_patterns = os.getenv("PYTINCTURE_BROWSER_FILES", "").strip()
    if not raw_patterns:
        return set()
    try:
        patterns = json.loads(raw_patterns)
    except json.JSONDecodeError:
        patterns = [value.strip() for value in raw_patterns.split(",") if value.strip()]
    if not isinstance(patterns, list) or any(not isinstance(value, str) for value in patterns):
        raise RuntimeError("PYTINCTURE_BROWSER_FILES must be a JSON list or comma-separated globs")
    selected: Set[str] = set()
    for root, dirs, files in os.walk(modules_root):
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".")
            and directory
            not in {"__pycache__", ".venv", "venv", "node_modules", "build", "dist"}
        ]
        for filename in files:
            absolute = os.path.abspath(os.path.join(root, filename))
            relative = os.path.relpath(absolute, modules_root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                selected.add(absolute)
    return selected


def _browser_package_files(application: str) -> Set[str]:
    modules_root = os.path.abspath(get_modules_path())
    entrypoint = os.path.abspath(os.path.join(modules_root, f"{application}.py"))
    if os.path.commonpath((modules_root, entrypoint)) != modules_root or not os.path.isfile(entrypoint):
        raise HTTPException(status_code=404, detail="Application entrypoint not found")
    selected = {entrypoint}
    pending = [entrypoint]
    while pending:
        for imported in _local_python_imports(pending.pop(), modules_root):
            if imported not in selected:
                selected.add(imported)
                pending.append(imported)
    for python_file in tuple(selected):
        parent = os.path.dirname(python_file)
        while parent != modules_root and os.path.commonpath((modules_root, parent)) == modules_root:
            package_init = os.path.join(parent, "__init__.py")
            if os.path.isfile(package_init):
                selected.add(os.path.abspath(package_init))
            parent = os.path.dirname(parent)
    return selected | _configured_browser_files(modules_root)


def create_appcode_pkg_in_memory(host, protocol, application, replay_client=None):
    """Generate an explicit browser-safe app package in memory."""
    appcode_folder = os.path.abspath(get_modules_path())
    in_memory_zip = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in sorted(_browser_package_files(application)):
            arcname = os.path.relpath(file_path, appcode_folder).replace(os.sep, "/")
            if file_path.endswith('.py'):
                file_contents = get_parsed_output(
                    file_path,
                    host,
                    protocol,
                    replay_client=replay_client,
                )
                zipf.writestr(arcname, file_contents or "")
            else:
                zipf.write(file_path, arcname)
    in_memory_zip.seek(0)
    return in_memory_zip


def _get_default_application() -> Optional[str]:
    configured = os.getenv("PYTINCTURE_DEFAULT_APPLICATION", "").strip().strip("/")
    if not configured:
        return None
    if (
        configured in (".", "..")
        or not all(char.isalnum() or char in "._-" for char in configured)
    ):
        raise RuntimeError(
            "PYTINCTURE_DEFAULT_APPLICATION must be a single application name without a path"
        )
    return configured


@app.get("/", include_in_schema=False)
async def default_application_redirect():
    default_application = _get_default_application()
    if default_application is None:
        raise HTTPException(status_code=404, detail="No default application configured")
    application_path = quote(default_application, safe="")
    return RedirectResponse(url=f"/{application_path}", status_code=302)


@app.get("/favicon.ico", operation_id="getFavicon", responses={200: {"description": "Response (binary content for favicon.ico, or empty if not implemented)"}, 404: {"description": "JSONResponse (if file not found, but currently not handled)"}})
async def favicon():
    """
    Serves the favicon.ico file.
    """
    pass

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    sanitized_errors = [
        {
            key: value
            for key, value in error.items()
            if key in {"loc", "msg", "type"}
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = correlation_id
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
    return response


@app.exception_handler(HTTPException)
async def sanitized_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        correlation_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        logger.error(
            "HTTP failure correlation_id=%s status=%s",
            correlation_id,
            exc.status_code,
            exc_info=exc,
        )
        return JSONResponse(
            {"detail": "Internal server error", "correlation_id": correlation_id},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def sanitized_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", uuid.uuid4().hex)
    logger.exception("Unhandled request failure correlation_id=%s", correlation_id)
    return JSONResponse(
        {"detail": "Internal server error", "correlation_id": correlation_id},
        status_code=500,
    )

def get_widgetset(application, static_path):
    """
    Scan the application file and its imports to find the widgetset.
    """
    import_pattern = re.compile(r'^\s*(import|from)\s+([a-zA-Z0-9_]+)')
    sanitized_application = os.path.basename(application.replace("\\", "/"))
    if sanitized_application in ("", ".", ".."):
        return ""
    app_file_path = os.path.join(static_path, f"{sanitized_application}.py")
    imports = []
    widgetset = None

    if os.path.exists(app_file_path):
        with open(app_file_path, 'r') as app_file:
            for line in app_file:
                match = import_pattern.match(line)
                if match:
                    module_name = match.group(2)
                    imports.append(module_name)

    for module_name in imports:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, '__widgetset__'):
                version = ""
                if hasattr(module, '__version__'):
                    version = "==" + getattr(module, '__version__')
                widgetset = getattr(module, '__widgetset__') + version
                break
        except ModuleNotFoundError:
            continue

    return widgetset if widgetset else ""

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
    )
else:
    logger.info("CORS middleware disabled; set CORS_ALLOWED_ORIGINS to enable it")

from upstash_redis import Redis

import json
from upstash_redis import Redis  # or whatever your actual import is
from markupsafe import escape

class RedisDict:
    """
    A dict-like interface backed by a Redis database (via Upstash), with
    a local in-memory cache to reduce the number of Redis lookups.
    """

    def __init__(self, redis_url: str, redis_token: str, key_prefix: str = ""):
        self._redis = Redis(url=redis_url, token=redis_token)
        self._prefix = key_prefix  # Optional prefix to avoid collisions
        self._cache = {}           # Local in-memory cache: { key: decoded_value }

    def __getitem__(self, key):
        """
        Gets the item from the local cache if present; otherwise fetch from Redis.
        Returns None if the key is missing in Redis.
        """
        if key in self._cache:
            return self._cache[key]

        full_key = self._prefix + key
        value = self._redis.get(full_key)

        if not value:
            # Key doesn't exist in Redis (or empty string?), store None in cache
            self._cache[key] = None
            return None

        # If it looks like JSON, decode it
        if value.startswith("{") and value.endswith("}"):
            value = json.loads(value)

        self._cache[key] = value
        return value

    def __setitem__(self, key, value):
        """Sets the item in Redis and updates the local cache."""
        full_key = self._prefix + key

        # If it's a dict, store as JSON
        if isinstance(value, dict):
            serialized = json.dumps(value)
        else:
            serialized = str(value)  # Ensure it's a string

        # Write to Redis
        self._redis.set(full_key, serialized)
        # Update local cache with the *decoded* form
        self._cache[key] = value

    def set_with_ttl(self, key, value, ttl_seconds: int):
        """Set a value that Redis removes automatically after the TTL."""
        full_key = self._prefix + key
        serialized = json.dumps(value) if isinstance(value, dict) else str(value)
        self._redis.set(full_key, serialized, ex=ttl_seconds)
        self._cache[key] = value

    def __delitem__(self, key):
        """Deletes the item from Redis and the local cache. Raises KeyError if missing."""
        full_key = self._prefix + key
        deleted = self._redis.delete(full_key)
        if deleted == 0:
            raise KeyError(key)

        # Also remove from local cache if present
        if key in self._cache:
            del self._cache[key]

    def pop_atomic(self, key, default=None):
        """Atomically fetch and delete a value, for one-time token consumption."""
        full_key = self._prefix + key
        value = self._redis.getdel(full_key)
        self._cache.pop(key, None)
        if value is None:
            return default
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return json.loads(value)
        return value

    def __contains__(self, key):
        """
        Return True if `key` is in Redis (or in the cache), otherwise False.

        This version uses the local cache first to avoid extra round-trips.
        If we have the key cached as None, that means we already checked
        Redis and it did not exist.
        """
        if key is None:
            return False

        # If we've already cached a value (even if it's None), return based on cache
        if key in self._cache:
            # If cache says None, that means Redis didn't have it
            return self._cache[key] is not None

        # Otherwise, check Redis
        full_key = self._prefix + key
        exists_in_redis = (self._redis.exists(full_key) == 1)

        # If it does not exist, also store None so we won't check again
        if not exists_in_redis:
            self._cache[key] = None

        return exists_in_redis

    def __len__(self):
        """
        Return the number of *cached* keys that are not None.
        This does NOT scan Redis, so it won't show items that never hit the cache
        or that changed in Redis outside this instance.
        
        If you want a fully accurate count from Redis every time,
        you'd need to revert to scanning (expensive) or do something like:
        
            count = 0
            cursor = "0"
            while True:
                cursor, keys = self._redis.scan(cursor=cursor, match=self._prefix + "*", count=100)
                count += len(keys)
                if cursor == "0":
                    break
            return count
        """
        # Count how many cached items are not None
        return sum(1 for val in self._cache.values() if val is not None)

    def __iter__(self):
        """
        Iterate over keys in Redis. 
        NOTE: This does a SCAN, so each iteration can trigger extra calls if repeated.
        If you need a local-only iteration (no new keys from Redis), you'd have to 
        iterate self._cache. But that may skip keys not yet read from Redis.
        """
        cursor = "0"
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=self._prefix + "*", count=100)
            for k in keys:
                # Strip the prefix from the Redis key
                yield k[len(self._prefix):]
            if cursor == "0":
                break

    def keys(self):
        """Return a generator of all keys in Redis matching our prefix."""
        return self.__iter__()

    def items(self):
        """
        Return a generator of (key, value) pairs.

        NOTE: This will still scan Redis for keys. Values will be fetched from cache if present,
        otherwise from Redis. 
        """
        for k in self:
            yield (k, self[k])

    def values(self):
        """
        Return a generator of all values.

        NOTE: Still scans Redis for the keys, then fetches each value 
        (from cache or Redis).
        """
        for k in self:
            yield self[k]

    def get(self, key, default=None):
        """
        Returns the value if present, otherwise `default`.
        Uses the local cache or fetches from Redis.
        """
        val = self.__getitem__(key)
        if val is None:
            return default
        return val
    

# Mount the frontend static files
STATIC_PATH = os.path.join(os.path.dirname(__file__), "../frontend/")
USE_REDIS_INSTANCE = os.environ.get("USE_REDIS_INSTANCE", "false").lower()
if  USE_REDIS_INSTANCE == "true":
    REDIS_UPSTASH_INSTANCE_URL = os.environ.get("REDIS_UPSTASH_INSTANCE_URL", "")
    REDIS_UPSTASH_INSTANCE_TOKEN =  os.environ.get("REDIS_UPSTASH_INSTANCE_TOKEN", "")
    USER_SESSION_DICT = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="session"
    )
    AUTH_SESSION_REVOCATIONS = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="revoked-session:",
    )
    BFF_REPLAY_TOKEN_STORE = RedisDict(
        redis_url=REDIS_UPSTASH_INSTANCE_URL,
        redis_token=REDIS_UPSTASH_INSTANCE_TOKEN,
        key_prefix="bff-replay-token:",
    )
else:
    USER_SESSION_DICT = {}
    AUTH_SESSION_REVOCATIONS = {}
    BFF_REPLAY_TOKEN_STORE = {}

MODULE_PATH = get_modules_path()


def build_bff_registry(modules_root: Optional[str] = None) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    """Build the complete exported BFF registry without importing application code."""
    root_path = os.path.abspath(modules_root or get_modules_path())
    registry: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    if not os.path.isdir(root_path):
        return registry
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [
            directory
            for directory in dirs
            if not directory.startswith(".")
            and directory
            not in {"__pycache__", ".venv", "venv", "node_modules", "build", "dist"}
        ]
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("."):
                continue
            file_path = os.path.join(root, filename)
            relative_path = os.path.relpath(file_path, root_path).replace(os.sep, "/")
            try:
                file_manifest = get_bff_manifest(file_path)
            except (OSError, SyntaxError, ValueError) as exc:
                raise RuntimeError(f"Unable to build BFF manifest for {relative_path}") from exc
            for (class_name, function_name), operation in file_manifest.items():
                registry[(relative_path, class_name, function_name)] = operation
    return registry


BFF_REGISTRY_ROOT = os.path.abspath(MODULE_PATH)
BFF_REGISTRY = build_bff_registry(BFF_REGISTRY_ROOT)


def reload_bff_registry(modules_root: Optional[str] = None):
    """Rebuild exported BFF operations, for example after development-time file changes."""
    global BFF_REGISTRY_ROOT, BFF_REGISTRY
    BFF_REGISTRY_ROOT = os.path.abspath(modules_root or get_modules_path())
    BFF_REGISTRY = build_bff_registry(BFF_REGISTRY_ROOT)
    return BFF_REGISTRY


def _registered_bff_operation(
    modules_root: str, relative_path: str, class_name: str, function_name: str
) -> Optional[Dict[str, Any]]:
    if os.path.abspath(modules_root) != BFF_REGISTRY_ROOT:
        reload_bff_registry(modules_root)
    return BFF_REGISTRY.get((relative_path.replace(os.sep, "/"), class_name, function_name))

try:
    ALLOWED_NOAUTH_CLASSCALLS = json.loads(os.environ.get("ALLOWED_NOAUTH_CLASSCALLS", "[]"))
except json.JSONDecodeError as e:
    raise RuntimeError("Invalid JSON in ALLOWED_NOAUTH_CLASSCALLS environment variable") from e


app.mount("/{application}/frontend", StaticFiles(directory=STATIC_PATH), name="static")
app.mount("/frontend", StaticFiles(directory=STATIC_PATH), name="static_frontend")

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


def set_user_authenticator(authenticator: Optional[Callable[..., Any]]):
    """Register a local email/password authenticator that returns trusted user claims."""
    global USER_AUTHENTICATOR
    USER_AUTHENTICATOR = authenticator
    return authenticator


def revoke_session(session_id: str) -> None:
    """Revoke a signed session; Redis-backed deployments share the revocation."""
    if session_id:
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
    expires_at = AUTH_SESSION_REVOCATIONS.get(session_id)
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
    configured = {
        value.strip().casefold()
        for value in os.getenv("ALLOWED_EMAILS", "").split(",")
        if value.strip()
    }
    return not configured or email.casefold() in configured


def _is_loopback_development_request(request: Request) -> bool:
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    hostname = (request.url.hostname or "").casefold()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0]
    forwarded_hostname = forwarded_host.rsplit(":", 1)[0].strip("[]").casefold()
    return hostname in allowed_hosts and (not forwarded_host or forwarded_hostname in allowed_hosts)


def _verify_configured_password(email: str, password: str) -> bool:
    raw_hashes = os.getenv("AUTH_PASSWORD_HASHES", "").strip()
    if not raw_hashes:
        return False
    try:
        password_hashes = json.loads(raw_hashes)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTH_PASSWORD_HASHES must be a JSON object") from exc
    if not isinstance(password_hashes, dict):
        raise RuntimeError("AUTH_PASSWORD_HASHES must be a JSON object")
    configured_hash = password_hashes.get(email) or password_hashes.get(email.casefold())
    known_user = isinstance(configured_hash, str)
    encoded_hash = configured_hash if known_user else (
        "$argon2id$v=19$m=65536,t=3,p=4$afcNkBX8goR7Ng5icg3p9w$"
        "UZsHTGXyFb9XrYQnpjpUvFRKKrc3WdWdH8oKTuGhX8M"
    )
    try:
        if encoded_hash.startswith("$argon2id$"):
            from argon2 import PasswordHasher
            from argon2.exceptions import VerificationError

            try:
                verified = PasswordHasher().verify(encoded_hash, password)
                return known_user and verified
            except VerificationError:
                return False
        if encoded_hash.startswith(("$2a$", "$2b$", "$2y$")):
            import bcrypt

            verified = bcrypt.checkpw(password.encode("utf-8"), encoded_hash.encode("utf-8"))
            return known_user and verified
    except (ValueError, TypeError):
        return False
    raise RuntimeError("AUTH_PASSWORD_HASHES values must be Argon2id or bcrypt hashes")


async def _authenticate_local_user(
    request: Request, email: str, password: str
) -> Dict[str, Any]:
    if not ENABLE_USER_LOGIN:
        raise HTTPException(status_code=403, detail="User login not enabled")
    normalized_email = str(email or "").strip().casefold()
    if not normalized_email or not isinstance(password, str) or not _allowed_email(normalized_email):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    authenticator = _configured_user_authenticator()
    if authenticator is not None:
        authenticated = authenticator(
            email=normalized_email, password=password, request=request
        )
        if inspect.isawaitable(authenticated):
            authenticated = await authenticated
        if not authenticated:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if authenticated is True:
            return {"email": normalized_email}
        if not isinstance(authenticated, dict):
            raise RuntimeError("User authenticator must return a mapping, True, or False")
        return {**authenticated, "email": normalized_email}

    if _verify_configured_password(normalized_email, password):
        return {"email": normalized_email}

    if (
        ENABLE_DEV_EMAIL_LOGIN
        and os.getenv("ALLOWED_EMAILS", "").strip()
        and _is_loopback_development_request(request)
    ):
        logger.warning("Using loopback-only development email login for %s", normalized_email)
        return {"email": normalized_email}

    raise HTTPException(status_code=401, detail="Invalid email or password")


def _normalize_file_identifier(value: str) -> str:
    normalized = (value or "").replace("\\", "/")
    normalized = normalized.strip("/")
    normalized = normalized.lstrip("./")
    return normalized


def _file_aliases(value: str) -> Set[str]:
    normalized = _normalize_file_identifier(value)
    if not normalized:
        return set()

    aliases = {normalized}
    basename = os.path.basename(normalized)
    aliases.add(basename)

    def add_variants(name: str):
        if not name:
            return
        if name.lower().endswith(".py"):
            aliases.add(name[:-3])
        else:
            aliases.add(f"{name}.py")

    add_variants(normalized)
    add_variants(basename)

    return {alias for alias in aliases if alias}


def is_noauth_allowed(file_name: str, class_name: str, function_name: str) -> bool:
    """
    Check if the given file, class, and function is allowed to be called without auth.
    Matching is case-insensitive and supports relative paths or basenames with/without `.py`.
    """
    requested_aliases = _file_aliases(file_name)
    requested_aliases_casefold = {alias.casefold() for alias in requested_aliases}

    for entry in ALLOWED_NOAUTH_CLASSCALLS:
        entry_file = entry.get("file", "")
        entry_aliases = _file_aliases(entry_file)
        if not entry_aliases:
            continue

        entry_aliases_casefold = {alias.casefold() for alias in entry_aliases}
        if ((requested_aliases & entry_aliases) or
                (requested_aliases_casefold & entry_aliases_casefold)):
            if (entry.get("class") == class_name and
                    entry.get("function") == function_name):
                return True
    return False


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
    ):
        request.session.pop(key, None)


def _normalize_auth_roles(value: Any) -> List[str]:
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = []

    roles = {
        str(role).strip().lower()
        for role in candidates
        if str(role).strip()
    }
    return sorted(roles)


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
        "roles": _normalize_auth_roles(source.get("roles")),
        "is_authenticated": True,
    }
    if resolved_provider:
        session_user["auth_provider"] = resolved_provider
    if resolved_provider_label:
        session_user["auth_provider_label"] = resolved_provider_label

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
    **identity_overrides: Any,
) -> Dict[str, Any]:
    session_user = _build_auth_session_user(user_info, **identity_overrides)
    _clear_auth_session(request)
    request.session["user"] = session_user
    request.session["session_id"] = secrets.token_urlsafe(24)
    request.session["csrf_token"] = secrets.token_urlsafe(32)
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
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0]
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "")).split(",", 1)[0]
    return f"{scheme}://{host}".rstrip("/")


def _validate_csrf(request: Request, user: Any) -> None:
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
    host = request.headers["host"]
    # Get the protocol from X-Forwarded-Proto header (if set)
    forwarded_proto = request.headers.get("x-forwarded-proto")
    protocol = forwarded_proto or request.url.scheme
    replay_client = _register_bff_replay_client(request, user)
    file_like = create_appcode_pkg_in_memory(
        host,
        protocol,
        application,
        replay_client=replay_client,
    )
    return StreamingResponse(
        file_like,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=appcode.pyt"}
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
async def public_app_asset(application: str, asset_path: str):
    normalized = asset_path.replace("\\", "/").strip("/")
    if not normalized or any(part in {"", ".", ".."} or part.startswith(".") for part in normalized.split("/")):
        raise HTTPException(status_code=404, detail="Asset not found")
    modules_root = os.path.realpath(get_modules_path())
    absolute_path = os.path.realpath(os.path.join(modules_root, *normalized.split("/")))
    try:
        within_root = os.path.commonpath((modules_root, absolute_path)) == modules_root
    except ValueError:
        within_root = False
    asset_allowed = _public_asset_allowed(normalized) or _application_widget_wheel_allowed(
        application,
        normalized,
        modules_root,
    )
    if not within_root or not os.path.isfile(absolute_path) or not asset_allowed:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(absolute_path)

@app.get("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="getClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
@app.post("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="postClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
@app.put("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="putClassCall", response_model=Any)
@app.patch("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="patchClassCall", response_model=Any)
@app.delete("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="deleteClassCall", response_model=Any)
async def class_call(
    file_path: str,
    class_name: str,
    function_name: str,
    request: Request
):
    
    # Determine if this call is allowed without auth.
    normalized_identifier = _normalize_file_identifier(file_path)
    if not normalized_identifier:
        raise HTTPException(status_code=400, detail="Invalid file path")

    request_identifier_with_ext = normalized_identifier
    if not request_identifier_with_ext.lower().endswith(".py"):
        request_identifier_with_ext += ".py"

    if is_noauth_allowed(request_identifier_with_ext, class_name, function_name):
        user = "noauth"
    else:
        # Perform authentication check for calls not whitelisted for no-auth.
        user = require_auth(request)

    if not user:
        raise HTTPException(status_code=401, detail="Call not authorized")

    modules_root = os.path.abspath(get_modules_path())
    fs_relative = request_identifier_with_ext.replace("/", os.sep)
    fs_relative = os.path.normpath(fs_relative)

    if fs_relative.startswith("..") or os.path.isabs(fs_relative) or os.path.splitdrive(fs_relative)[0]:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if os.path.basename(fs_relative).startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")

    module_file_path = os.path.abspath(os.path.join(modules_root, fs_relative))

    try:
        common_root = os.path.commonpath([module_file_path, modules_root])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid module path")

    if common_root != modules_root:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not os.path.isfile(module_file_path):
        raise HTTPException(status_code=404, detail=f"File {request_identifier_with_ext} not found in appcode folder")

    operation = _registered_bff_operation(
        modules_root,
        request_identifier_with_ext,
        class_name,
        function_name,
    )
    if operation is None:
        raise HTTPException(status_code=404, detail="BFF operation not exported")
    allowed_methods = tuple(operation["http_methods"])
    if request.method not in allowed_methods:
        raise HTTPException(
            status_code=405,
            detail="HTTP method not allowed for this BFF operation",
            headers={"Allow": ", ".join(allowed_methods)},
        )

    _validate_csrf(request, user)
    _validate_bff_replay_token(request, user)
    policy_hook = _configured_bff_policy_hook()
    if policy_hook:
        policy_result = policy_hook(
            user=_coerce_policy_user(user),
            policy=operation.get("policy", {}),
            class_name=class_name,
            function_name=function_name,
            module_path=request_identifier_with_ext,
            request=request,
        )
        if inspect.isawaitable(policy_result):
            await policy_result

    module = _load_source_module(module_file_path, class_name)
    cls = getattr(module, class_name)
    instance = cls(_user=user)

    # 3) Get the function
    func = getattr(instance, function_name)
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

        def _serialize_stream_item(item, raw: bool = False):
            # Ensure each streamed chunk is JSON encoded and newline-delimited unless raw passthrough is requested.
            if isinstance(item, (bytes, bytearray)):
                data_bytes = bytes(item)
                if not raw and not data_bytes.endswith(b"\n"):
                    data_bytes += b"\n"
                return data_bytes
            if isinstance(item, str):
                data_text = item
            else:
                data_text = json.dumps(item)
            if not raw and not data_text.endswith("\n"):
                data_text += "\n"
            return data_text

        def _sync_iterable(iterable: Iterable, raw: bool = False):
            started = time.monotonic()
            output_bytes = 0
            for item in iterable:
                if time.monotonic() - started > BFF_STREAM_MAX_SECONDS:
                    return
                serialized = _serialize_stream_item(item, raw)
                output_bytes += len(serialized.encode("utf-8") if isinstance(serialized, str) else serialized)
                if output_bytes > BFF_STREAM_MAX_BYTES:
                    return
                yield serialized

        async def _async_iterable(iterable: AsyncIterable, raw: bool = False):
            started = time.monotonic()
            output_bytes = 0
            iterator = iterable.__aiter__()
            while True:
                remaining = BFF_STREAM_MAX_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    return
                try:
                    item = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    return
                serialized = _serialize_stream_item(item, raw)
                output_bytes += len(serialized.encode("utf-8") if isinstance(serialized, str) else serialized)
                if output_bytes > BFF_STREAM_MAX_BYTES:
                    return
                yield serialized

        def _as_streaming_response(result_obj):
            if isinstance(result_obj, StreamingResponse):
                return result_obj

            if inspect.isasyncgen(result_obj) or hasattr(result_obj, "__aiter__"):
                async_iter = result_obj if inspect.isasyncgen(result_obj) else result_obj
                return StreamingResponse(_async_iterable(async_iter, streaming_raw), media_type=streaming_media_type)

            if isinstance(result_obj, (str, bytes, bytearray)):
                return StreamingResponse(_sync_iterable([result_obj], streaming_raw), media_type=streaming_media_type)

            if isinstance(result_obj, dict):
                return StreamingResponse(_sync_iterable([result_obj], streaming_raw), media_type=streaming_media_type)

            if inspect.isgenerator(result_obj) or isinstance(result_obj, Iterable):
                iterable_obj = result_obj if inspect.isgenerator(result_obj) else result_obj
                return StreamingResponse(_sync_iterable(iterable_obj, streaming_raw), media_type=streaming_media_type)

            # Fallback: stream single value
            return StreamingResponse(_sync_iterable([result_obj], streaming_raw), media_type=streaming_media_type)

        # Execute the target callable
        if is_async_gen_function:
            result = func(*args, **kwargs)
            if is_streaming:
                return _as_streaming_response(result)
            collected_items = []
            async def collect_items():
                async for item in result:
                    collected_items.append(item)
            try:
                await asyncio.wait_for(collect_items(), timeout=BFF_CALL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF call timed out") from exc
            return collected_items

        if is_coroutine_function:
            try:
                result = await asyncio.wait_for(
                    func(*args, **kwargs), timeout=BFF_CALL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF call timed out") from exc
        else:
            try:
                result = await asyncio.wait_for(
                    run_in_threadpool(func, *args, **kwargs),
                    timeout=BFF_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=504, detail="BFF call timed out") from exc

        if is_streaming:
            return _as_streaming_response(result)

        return result

    return func

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

_previous_secret_value = os.getenv("AUTH_SESSION_PREVIOUS_SECRET_KEYS", "").strip()
if _previous_secret_value:
    try:
        AUTH_SESSION_PREVIOUS_SECRET_KEYS = json.loads(_previous_secret_value)
    except json.JSONDecodeError:
        AUTH_SESSION_PREVIOUS_SECRET_KEYS = [
            value.strip() for value in _previous_secret_value.split(",") if value.strip()
        ]
    if not isinstance(AUTH_SESSION_PREVIOUS_SECRET_KEYS, list) or any(
        not isinstance(value, str) or len(value) < 32
        for value in AUTH_SESSION_PREVIOUS_SECRET_KEYS
    ):
        raise RuntimeError("AUTH_SESSION_PREVIOUS_SECRET_KEYS must contain strong keys")
else:
    AUTH_SESSION_PREVIOUS_SECRET_KEYS = []

AUTH_SESSION_SCHEMA_VERSION = 2
AUTH_SESSION_MAX_AGE_SECONDS = int(os.getenv("AUTH_SESSION_MAX_AGE_SECONDS", "28800"))
if AUTH_SESSION_MAX_AGE_SECONDS <= 0:
    raise RuntimeError("AUTH_SESSION_MAX_AGE_SECONDS must be greater than zero")
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
BFF_CALL_TIMEOUT_SECONDS = float(os.getenv("BFF_CALL_TIMEOUT_SECONDS", "30"))
BFF_STREAM_MAX_SECONDS = float(os.getenv("BFF_STREAM_MAX_SECONDS", "300"))
BFF_STREAM_MAX_BYTES = int(os.getenv("BFF_STREAM_MAX_BYTES", str(10 * 1024 * 1024)))
if BFF_CALL_TIMEOUT_SECONDS <= 0 or BFF_STREAM_MAX_SECONDS <= 0 or BFF_STREAM_MAX_BYTES <= 0:
    raise RuntimeError("BFF timeout and stream limits must be greater than zero")
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


def _split_csv(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _provider_value(provider: Optional[Dict[str, Any]], *keys: str, default: Any = "") -> Any:
    if provider:
        for key in keys:
            value = provider.get(key)
            if value not in (None, ""):
                return value
    return default


def _normalize_saml_provider_id(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_-]+", "-", value.strip()).strip("-")
    return normalized.lower()


def _normalize_saml_provider(raw_provider: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    provider = dict(raw_provider)
    provider_id = _normalize_saml_provider_id(str(provider.get("id") or fallback_id))
    if not provider_id:
        raise RuntimeError("SAML provider id cannot be empty")
    provider["id"] = provider_id
    provider["label"] = str(provider.get("label") or provider.get("name") or f"Login with {provider_id}")
    logo_url = provider.get("logo_url") or provider.get("logo") or provider.get("logoUrl") or ""
    provider["logo_url"] = str(logo_url)
    return provider


def _load_saml_providers() -> List[Dict[str, Any]]:
    configured = SAML_PROVIDERS
    if isinstance(configured, str):
        configured = configured.strip()
        if configured:
            try:
                configured = json.loads(configured)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Invalid JSON in SAML_PROVIDERS environment variable") from exc
        else:
            configured = None

    providers: List[Dict[str, Any]] = []
    if isinstance(configured, dict):
        for provider_id, provider_data in configured.items():
            if not isinstance(provider_data, dict):
                raise RuntimeError("Each SAML_PROVIDERS entry must be an object")
            providers.append(_normalize_saml_provider(provider_data, str(provider_id)))
    elif isinstance(configured, list):
        for index, provider_data in enumerate(configured):
            if not isinstance(provider_data, dict):
                raise RuntimeError("Each SAML_PROVIDERS entry must be an object")
            providers.append(_normalize_saml_provider(provider_data, f"provider-{index + 1}"))
    elif configured is not None:
        raise RuntimeError("SAML_PROVIDERS must be a JSON object or array")

    if providers:
        seen_ids: Set[str] = set()
        for provider in providers:
            provider_id = provider["id"]
            if provider_id in seen_ids:
                raise RuntimeError(f"Duplicate SAML provider id: {provider_id}")
            seen_ids.add(provider_id)
        return providers

    return [{
        "id": "default",
        "label": SAML_LOGIN_LABEL or "Login with SAML",
        "logo_url": SAML_LOGO_URL,
    }]


def _get_saml_provider(provider_id: Optional[str] = None) -> Dict[str, Any]:
    providers = _load_saml_providers()
    if provider_id is None:
        if len(providers) == 1:
            return providers[0]
        raise HTTPException(status_code=400, detail="SAML provider id is required")

    normalized_id = _normalize_saml_provider_id(provider_id)
    for provider in providers:
        if provider["id"] == normalized_id:
            return provider
    raise HTTPException(status_code=404, detail=f"SAML provider '{provider_id}' not found")


def _get_saml_login_buttons() -> List[Dict[str, str]]:
    providers = _load_saml_providers()
    use_provider_routes = len(providers) > 1 or providers[0]["id"] != "default"
    buttons = []
    for provider in providers:
        href = "auth/saml/login"
        if use_provider_routes:
            href = f"auth/saml/{provider['id']}/login"
        buttons.append({
            "href": href,
            "label": provider.get("label") or "Login with SAML",
            "logo_url": provider.get("logo_url") or "",
        })
    return buttons


def _get_saml_allowed_roles(provider: Optional[Dict[str, Any]] = None) -> List[str]:
    provider_roles = _provider_value(provider, "allowed_roles", "allowedRoles", default=None)
    roles = _split_csv(provider_roles) if provider_roles is not None else SAML_ALLOWED_ROLES
    return [role.lower() for role in roles]


def _get_saml_role_attribute_keys(provider: Optional[Dict[str, Any]] = None) -> List[str]:
    provider_keys = _provider_value(provider, "role_attribute_keys", "roleAttributeKeys", default=None)
    return _split_csv(provider_keys) if provider_keys is not None else SAML_ROLE_ATTRIBUTE_KEYS


_SAML_RELAY_STATE_SALT = "pytincture-saml-relay-state-v1"


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

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise HTTPException(status_code=400, detail="Invalid SAML RelayState")

    for key in ("application", "provider_id", "request_id"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise HTTPException(status_code=400, detail="Invalid SAML RelayState")

    return_to = payload.get("return_to")
    if return_to is not None and _sanitize_return_to(return_to) is None:
        raise HTTPException(status_code=400, detail="Invalid SAML RelayState return path")

    return payload


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
    protocol = request.headers.get("x-forwarded-proto") or request.url.scheme
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

    forwarded_port = request.headers.get("x-forwarded-port")
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
        oauth.register(
            name="microsoft",
            client_id=config.get("MICROSOFT_CLIENT_ID"),
            client_secret=config.get("MICROSOFT_CLIENT_SECRET"),
            server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
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

    relay_token = _sign_saml_relay_state(
        {
            "version": 1,
            "application": application,
            "provider_id": provider["id"],
            "request_id": request_id,
            "return_to": fallback_return,
        }
    )
    auth_url = _replace_saml_relay_state(saml_auth, auth_url, relay_token)
    request.session.pop("saml_request_id", None)
    request.session.pop("saml_provider_id", None)
    return RedirectResponse(url=auth_url)


@app.post(
    "/{application}/auth/saml/acs",
    operation_id="handleSamlAuthCallback",
    response_class=RedirectResponse,
    responses={
        302: {"description": "RedirectResponse (to original path after login)"},
        400: {
            "description": "HTTPException (if SAML response invalid)"},
        401: {"description": "HTTPException (if user not authorized)"},
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
        404: {"description": "HTTPException (if SAML disabled or provider missing)"},
    },
)
async def saml_provider_assertion_consumer(request: Request, application: str, provider_id: str):
    return await _saml_assertion_consumer(request, application, provider_id=provider_id)


async def _saml_assertion_consumer(request: Request, application: str, provider_id: Optional[str] = None):
    """
    Handle the assertion consumer service (ACS) endpoint invoked by the IdP.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    _debug_session_state("saml_acs:entry", request)
    form_data = await request.form()
    post_data = dict(form_data.multi_items())
    relay_token = post_data.get("RelayState")
    relay_state = _load_saml_relay_state(relay_token)
    if relay_state["application"] != application:
        raise HTTPException(status_code=400, detail="SAML application mismatch")

    state_provider_id = relay_state["provider_id"]
    resolved_provider_id = provider_id or state_provider_id
    provider = _get_saml_provider(resolved_provider_id)
    if provider["id"] != state_provider_id:
        raise HTTPException(status_code=400, detail="SAML provider mismatch")
    
    try:
        saml_auth = _init_saml_auth(request, application, provider=provider, post_data=post_data)
        request_id = relay_state["request_id"]
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

    _set_authenticated_user(request, user_info)

    cached_redirect = _sanitize_return_to(relay_state.get("return_to"))
    session_redirect = _sanitize_return_to(request.session.pop("return_to", None))
    if not cached_redirect:
        cached_redirect = session_redirect
    redirect_target = cached_redirect or _get_saml_default_redirect(application, request, provider=provider)
    return RedirectResponse(url=redirect_target, status_code=302)


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

    forwarded_proto = request.headers.get("x-forwarded-proto")
    host = request.headers["host"]
    protocol = forwarded_proto or request.url.scheme
    redirect_uri = f"{protocol}://{host}/{application}/auth/google/callback"

    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/{application}/auth/google/callback", name="auth_google_callback", operation_id="handleGoogleAuthCallback", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to original path after login)"}, 401: {"description": "JSONResponse (if OAuth error or not authorized)"}})
async def auth_google_callback(request: Request, application: str):
    """
    Google redirects here after login. Authlib will exchange code for token.
    We'll store user info in the session, then redirect back to original app path.
    """
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

    # You can optionally grab user info from token["userinfo"]
    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    _set_authenticated_user(
        request,
        user_info,
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

    forwarded_proto = request.headers.get("x-forwarded-proto")
    host = request.headers["host"]
    protocol = forwarded_proto or request.url.scheme
    redirect_uri = f"{protocol}://{host}/{application}/auth/microsoft/callback"

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

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    _set_authenticated_user(
        request,
        user_info,
        auth_type="microsoft",
        auth_provider="microsoft",
        auth_provider_label="Microsoft",
    )

    return_to = _sanitize_return_to(request.session.pop("return_to", None)) or "/"
    return RedirectResponse(url=return_to)

@app.get("/{application}/auth/logout", operation_id="logoutUser", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to login page)"}})
def logout(request: Request,  application: str):
    """
    Logs the user out of *your app only*.
    """
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
        </style>
    </head>
    <body>
        <div class="login-container">
            <h2>Welcome</h2>
            <p>Please log in to continue</p>
    """

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
        html_content += '''
            <form method="post" action="auth/user">
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
    email = str(form.get('email') or "")
    password = str(form.get('password') or "")
    authenticated_claims = await _authenticate_local_user(request, email, password)

    user_info = {
        **authenticated_claims,
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "user",
        "roles": authenticated_claims.get("roles", []),
    }

    _set_authenticated_user(request, user_info)

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
        "roles": authenticated_claims.get("roles", []),
    }

    _set_authenticated_user(request, user_info)

    return {"status": "authenticated"}

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

    # Already logged in, proceed normally
    appcode_folder = get_modules_path()
    widgetset = get_widgetset(application, appcode_folder)
    safe_application = escape(application)

    # Modify the index.html to include the application name and widgetset
    index_html = open(f"{STATIC_PATH}/index.html").read()
    index_html = index_html.replace("***APPLICATION***", safe_application)
    
    # Find the proper entrypoint class (MainWindow subclass)
    app_file_path = f"{appcode_folder}/{application}.py"
    if os.path.exists(app_file_path):
        main_window_class = find_main_window_subclass(app_file_path)
        if main_window_class:
            # Use the discovered MainWindow subclass name as the entrypoint
            index_html = index_html.replace("***ENTRYPOINT***", main_window_class)
        else:
            # If no MainWindow subclass is found, fallback to using application name
            index_html = index_html.replace("***ENTRYPOINT***", safe_application)
    else:
        # If file doesn't exist, just use the application name as-is
        index_html = index_html.replace("***ENTRYPOINT***", safe_application)
    
    loading_title = application
    favicon_markup = ""
    if os.path.exists(app_file_path):
        loading_title = find_app_loading_title(app_file_path, application)
        favicon_markup = build_app_favicon_markup(application, app_file_path)
    index_html = index_html.replace("***LOADING_TITLE***", escape(loading_title))
    index_html = index_html.replace("***FAVICON_LINK***", favicon_markup)

    index_html = index_html.replace("***WIDGETSET***", widgetset)
    return HTMLResponse(content=index_html)

def find_main_window_subclass(file_path):
    """
    Scans a Python file for a class that subclasses MainWindow.
    Returns the name of the first such class found, or None if no match.
    """
    try:
        # Load the module
        module_name = os.path.basename(file_path).replace('.py', '')
        module = _load_source_module(file_path, module_name)
        
        # Inspect all classes in the module
        for name, obj in inspect.getmembers(module):
            # Check if it's a class and if it's a subclass of MainWindow
            if inspect.isclass(obj) and hasattr(obj, '__bases__'):
                for base in obj.__bases__:
                    if base.__name__ == 'MainWindow':
                        return name  # Return the name of the MainWindow subclass
        
        return None  # No MainWindow subclass found
    except Exception as e:
        logger.warning("Unable to find MainWindow subclass", exc_info=e)
        return None

def _find_app_string_setting(file_path, assignment_names, config_keys):
    """
    Read a string setting from app source without importing the application.
    """
    try:
        with open(file_path, "r") as f:
            source = f.read()
        tree = ast.parse(source)
    except Exception as e:
        logger.warning("Unable to read app configuration", exc_info=e)
        return None

    def extract_string(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id in assignment_names:
                        value = extract_string(node.value)
                        if value:
                            return value
                    if target.id == "APP_CONFIG" and isinstance(node.value, ast.Dict):
                        for key, value_node in zip(node.value.keys, node.value.values):
                            key_str = extract_string(key)
                            if key_str in config_keys:
                                value = extract_string(value_node)
                                if value:
                                    return value
    return None


def find_app_loading_title(file_path, default_title):
    """
    Read APP_TITLE, APP_LOADING_TITLE, or the matching APP_CONFIG value.
    """
    return _find_app_string_setting(
        file_path,
        assignment_names=("APP_TITLE", "APP_LOADING_TITLE"),
        config_keys=("title", "loading_title"),
    ) or default_title


def _normalize_app_asset_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    candidate = value.strip()
    if candidate.startswith("/appcode/"):
        candidate = candidate[len("/appcode/"):]
    elif candidate.startswith("appcode/"):
        candidate = candidate[len("appcode/"):]
    elif candidate.startswith("/"):
        return None

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    if "\\" in candidate:
        return None

    segments = candidate.split("/")
    if any(not segment or segment in (".", "..") for segment in segments):
        return None
    return "/".join(segments)


def _find_explicit_app_favicon(file_path) -> Optional[str]:
    configured = _find_app_string_setting(
        file_path,
        assignment_names=("APP_FAVICON",),
        config_keys=("favicon",),
    )
    return _normalize_app_asset_path(configured)


def find_app_favicon(file_path) -> Optional[str]:
    """
    Resolve an explicit favicon file/folder or a conventional favicon directory.
    """
    configured = _find_explicit_app_favicon(file_path)
    if configured:
        return configured

    app_root = os.path.dirname(os.fspath(file_path))
    application = os.path.splitext(os.path.basename(os.fspath(file_path)))[0]
    for candidate in (f"favicon/{application}", "favicon"):
        candidate_path = os.path.join(app_root, *candidate.split("/"))
        if os.path.isdir(candidate_path):
            return candidate
    return None


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

    if (
        application not in ("", ".", "..")
        and all(char.isalnum() or char in "._-" for char in application)
    ):
        application_folder = os.path.realpath(os.path.join(root, application))
        try:
            is_within_root = os.path.commonpath((root, application_folder)) == root
        except ValueError:
            is_within_root = False
        if is_within_root and os.path.isdir(application_folder):
            return application_folder

    return root


def _find_favicon_assets_in_directory(directory: str) -> List[str]:
    assets = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name.lower()):
            if entry.is_file(follow_symlinks=False) and _is_favicon_asset(entry.name):
                assets.append(entry.name)
    return assets


def find_app_favicon_assets(file_path) -> List[str]:
    """Return the declared favicon file or supported files in its directory."""
    favicon_path = find_app_favicon(file_path)
    if not favicon_path:
        return []

    app_root = os.path.dirname(os.fspath(file_path))
    local_path = os.path.join(app_root, *favicon_path.split("/"))
    if not os.path.isdir(local_path):
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
) -> Optional[str]:
    favicon_url = (
        f"/{quote(application, safe='')}/{asset_route}/"
        f"{quote(asset_path, safe='/')}"
    )
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


def build_app_favicon_markup(application: str, file_path) -> str:
    """Generate browser favicon declarations for an application's assets."""
    configured_directory = None
    if _find_explicit_app_favicon(file_path) is None:
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
                )
            ) is not None
        ]
        if tags:
            return "\n    ".join(tags)

    tags = [
        tag
        for asset_path in find_app_favicon_assets(file_path)
        if (tag := _build_favicon_tag(application, asset_path)) is not None
    ]
    return "\n    ".join(tags)


@app.get(
    "/{application}/favicon-assets/{asset_name}",
    include_in_schema=False,
)
async def configured_favicon_asset(application: str, asset_name: str):
    """Serve a browser favicon asset from the launcher-configured directory."""
    if asset_name != os.path.basename(asset_name) or not _is_favicon_asset(asset_name):
        raise HTTPException(status_code=404, detail="Favicon asset not found")

    favicon_directory = _get_configured_favicon_directory(application)
    if favicon_directory is None:
        raise HTTPException(status_code=404, detail="Favicon asset not found")

    asset_path = os.path.realpath(os.path.join(favicon_directory, asset_name))
    try:
        is_within_directory = (
            os.path.commonpath((favicon_directory, asset_path)) == favicon_directory
        )
    except ValueError:
        is_within_directory = False

    if not is_within_directory or not os.path.isfile(asset_path):
        raise HTTPException(status_code=404, detail="Favicon asset not found")
    return FileResponse(asset_path)


add_bff_docs_to_app(app)
reload_mcp_tools()

# =================
# RUN THE APP
# =================
# Typically:
# uvicorn app:app --host 0.0.0.0 --port 8070
