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
import nest_asyncio
import base64
import hashlib
from xml.etree import ElementTree
try:
    nest_asyncio.apply()
except ValueError as exc:
    # uvloop event loops cannot be patched; skip when uvloop is active.
    if "uvloop" not in str(exc):
        raise

# FastAPI / Starlette
from fastapi import Depends, FastAPI, Request, Response, HTTPException, Body
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

# Pytincture
from pytincture import get_modules_path
from pytincture.dataclass import get_parsed_output, add_bff_docs_to_app
from importlib.machinery import SourceFileLoader

# Google OAuth via Authlib
from authlib.integrations.starlette_client import OAuth, OAuthError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware
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

def reload_mcp_tools():
    global mcp, mcp_http_app  # Use globals or pass as needed if in a class/module
    
    # Step 1: Remove existing MCP-mounted routes to avoid duplicates
    # Filter out routes starting with "/mcp" to avoid duplicate mounts.
    app.router.routes = [
        route for route in app.router.routes
        if not route.path.startswith("/mcp")
    ]
    
    # Step 2: Recreate FastMCP instance (rescans app for new endpoints/tools)
    mcp = FastMCP.from_fastapi(app=app, name="short")  # Add name="short" to reduce prefixed/suffixed lengths
    print("MCP Tools reloaded successfully.")
    
    # Test tool name lengths
    print("\nTesting MCP Tool Name Lengths:")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        print("Skipping MCP tool length test: event loop already running.")
    else:
        tools = asyncio.run(mcp.get_tools())
        for tool in tools.values():
            name_length = len(tool.name)
            print(f"Tool: {tool.name} | Length: {name_length} chars | Over Limit: {name_length > 64}")
            if name_length > 64:
                print(f"  WARNING: Exceeds 64-char limit! Suggested truncate: {tool.name[:61]}...")
    
    # Step 3: Recreate MCP app using streamable HTTP transport
    mcp_http_app = _build_streamable_mcp_app(mcp, path='/')
    
    # Step 4: Remount the updated MCP app
    app.mount("/mcp", mcp_http_app)

def create_appcode_pkg_in_memory(host, protocol):
    """Generate an appcode package in memory for the browser to pull for the frontend."""
    appcode_folder = get_modules_path()
    in_memory_zip = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(appcode_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, appcode_folder)
                if not (file.endswith('.zip') or file.endswith('.pyt') or file.endswith('.pyc') or file.endswith('.whl') or file.startswith('.')):
                    if "__pycache__" not in root and ".venv" not in root:
                        if file.endswith('.py'):
                            file_contents = get_parsed_output(file_path, host, protocol)
                            if os.path.getsize(file_path) != 0:
                                zipf.writestr(arcname, file_contents)
                            else:
                                zipf.write(file_path, arcname)
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
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
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
    print("CORS middleware disabled: set CORS_ALLOWED_ORIGINS to enable cross-origin requests.")

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

    def __delitem__(self, key):
        """Deletes the item from Redis and the local cache. Raises KeyError if missing."""
        full_key = self._prefix + key
        deleted = self._redis.delete(full_key)
        if deleted == 0:
            raise KeyError(key)

        # Also remove from local cache if present
        if key in self._cache:
            del self._cache[key]

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
else:
    USER_SESSION_DICT = {}

MODULE_PATH = get_modules_path()

try:
    ALLOWED_NOAUTH_CLASSCALLS = json.loads(os.environ.get("ALLOWED_NOAUTH_CLASSCALLS", "[]"))
except json.JSONDecodeError as e:
    raise RuntimeError("Invalid JSON in ALLOWED_NOAUTH_CLASSCALLS environment variable") from e


app.mount("/{application}/frontend", StaticFiles(directory=STATIC_PATH), name="static")
app.mount("/frontend", StaticFiles(directory=STATIC_PATH), name="static_frontend")

BFF_POLICY_HOOK: Optional[Callable[..., None]] = None


def set_bff_policy_hook(hook: Optional[Callable[..., None]]):
    """
    Register (or clear) a global hook that runs before each backend_for_frontend call.
    The hook receives the resolved user session, policy metadata, and request context.
    """
    global BFF_POLICY_HOOK
    BFF_POLICY_HOOK = hook
    return hook


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

@app.get("/{application}/appcode/appcode.pyt", operation_id="downloadAppcodePackage", responses={200: {"description": "StreamingResponse (ZIP file stream, media_type=\"application/zip\")"}, 401: {"description": "HTTPException (if authentication fails when required)"}})
def download_appcode(request: Request, user=Depends(require_auth)):
    host = request.headers["host"]
    # Get the protocol from X-Forwarded-Proto header (if set)
    forwarded_proto = request.headers.get("x-forwarded-proto")
    protocol = forwarded_proto or request.url.scheme
    file_like = create_appcode_pkg_in_memory(host, protocol)
    return StreamingResponse(
        file_like,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=appcode.pyt"}
    )

app.mount("/{application}/appcode", StaticFiles(directory=MODULE_PATH), name="static")

@app.get("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="getClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
@app.post("/classcall/{file_path:path}/{class_name}/{function_name}", operation_id="postClassCall", response_model=Any, responses={200: {"description": "Any (dynamic based on called function return, suggest annotating as Union[Dict, List, str, int, float]) or StreamingResponse for streaming methods"}, 401: {"description": "HTTPException (if not authorized)"}, 404: {"description": "HTTPException (if file not found)"}, 500: {"description": "HTTPException (if function call fails)"}})
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
    if request.method == "POST":
        data = await request.json()
    
    if type(data) == str:
        try:
            data = json.loads(str(data))
        except Exception as e:
            print("could not convert data to json")

    # 5) Call the function with *args / **kwargs if needed
    if BFF_POLICY_HOOK:
        policy_metadata = getattr(function_obj, "_bff_policy", {}) or {}
        BFF_POLICY_HOOK(
            user=_coerce_policy_user(user),
            policy=policy_metadata,
            class_name=class_name,
            function_name=function_name,
            module_path=request_identifier_with_ext,
            request=request,
        )

    if callable(func):
        args = data.get("args", [])
        kwargs = data.get("kwargs", {})

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
            for item in iterable:
                yield _serialize_stream_item(item, raw)

        async def _async_iterable(iterable: AsyncIterable, raw: bool = False):
            async for item in iterable:
                yield _serialize_stream_item(item, raw)

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
            async for item in result:
                collected_items.append(item)
            return collected_items

        if is_coroutine_function:
            result = await func(*args, **kwargs)
        else:
            print(args, kwargs)
            result = func(*args, **kwargs)

        if is_streaming:
            return _as_streaming_response(result)

        return result

    return func

@app.post("/logs", operation_id="postLogs", responses={200: {"description": "JSONResponse ({\"status\": \"ok\"})"}, 401: {"description": "HTTPException (if authentication fails)"}})
async def logs_endpoint(request: Request, user=Depends(require_auth)):
    data = await request.json()
    print(data)
    return {"status": "ok"}


# ================
# GOOGLE OAUTH2 SETUP
# ================

ENABLE_GOOGLE_AUTH = os.getenv("ENABLE_GOOGLE_AUTH", "false").lower() == "true"
ENABLE_USER_LOGIN = os.getenv("ENABLE_USER_LOGIN", "false").lower() == "true"
ENABLE_SAML_AUTH = os.getenv("ENABLE_SAML_AUTH", "false").lower() == "true"
ENABLE_MICROSOFT_AUTH = os.getenv("ENABLE_MICROSOFT_AUTH", "false").lower() == "true"

_configured_saml_secret = os.getenv("SAML_SECRET_KEY", "").strip()
if _configured_saml_secret and len(_configured_saml_secret) < 32:
    raise RuntimeError("SAML_SECRET_KEY must contain at least 32 characters")

SAML_SECRET_KEY = (
    _configured_saml_secret
    or os.getenv("SECRET_KEY", "").strip()
    or "verysecretkey"
)
AUTH_SESSION_SCHEMA_VERSION = 1
AUTH_SESSION_MAX_AGE_SECONDS = int(os.getenv("AUTH_SESSION_MAX_AGE_SECONDS", "28800"))
if AUTH_SESSION_MAX_AGE_SECONDS <= 0:
    raise RuntimeError("AUTH_SESSION_MAX_AGE_SECONDS must be greater than zero")
AUTH_SESSION_HTTPS_ONLY = os.getenv(
    "AUTH_SESSION_HTTPS_ONLY",
    "true" if _configured_saml_secret else "false",
).lower() == "true"

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
        print(f"DEBUG: Failed to decode certificate for fingerprint: {exc}")
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
        print(f"DEBUG: Failed to parse SAML XML for embedded certificates: {exc}")
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
        tracked_snapshot = {
            key: request.session.get(key)
            for key in (
                "saml_request_id",
                "return_to",
                "saml_name_id",
                "saml_session_index",
            )
        }
        print(
            f"DEBUG: Session state ({stage}) -> "
            f"cookie_present={cookie_present} size={cookie_length} "
            f"keys={session_keys} tracked_values={tracked_snapshot}"
        )
    except Exception as exc:  # pragma: no cover - diagnostics only
        print(f"DEBUG: Failed to inspect session during {stage}: {exc}")


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
    SessionMiddleware,
    secret_key=SAML_SECRET_KEY,
    max_age=AUTH_SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=AUTH_SESSION_HTTPS_ONLY,
)

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
        print(f"DEBUG: Stored return_to '{safe_return_to}' in session for application '{application}'")
    else:
        if return_to:
            print(f"DEBUG: Ignored unsafe return_to '{return_to}'")
        else:
            print("DEBUG: No return_to param supplied")

    try:
        saml_auth = _init_saml_auth(request, application, provider=provider)
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail=str(config_error)) from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"SAML initialization failed: {exc}") from exc

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
    
    # DEBUG: Log what we received
    print(f"DEBUG: Received form data keys: {list(post_data.keys())}")
    if 'SAMLResponse' in post_data:
        try:
            decoded_response = base64.b64decode(post_data['SAMLResponse']).decode('utf-8')
            print(f"DEBUG: SAML Response (first 500 chars): {decoded_response[:500]}...")
            embedded_certs = _extract_response_certificates(decoded_response)
            if embedded_certs:
                for idx, embedded_cert in enumerate(embedded_certs):
                    fingerprint = _certificate_fingerprint(embedded_cert)
                    preview = embedded_cert[:80]
                    print(
                        f"DEBUG: Embedded certificate #{idx} fingerprint={fingerprint} "
                        f"preview={preview}..."
                    )
                    print(f"DEBUG: Embedded certificate #{idx} full={embedded_cert}")
            else:
                print("DEBUG: No embedded certificates found inside SAML response XML")
        except Exception as e:
            print(f"DEBUG: Could not decode SAML response: {e}")

    try:
        saml_auth = _init_saml_auth(request, application, provider=provider, post_data=post_data)
        
        # DEBUG: Log SAML settings  
        settings = saml_auth.get_settings()
        sp_data = settings.get_sp_data()
        idp_data = settings.get_idp_data()
        
        print(f"DEBUG: SP Entity ID: {sp_data.get('entityId')}")
        print(f"DEBUG: SP ACS URL: {sp_data.get('assertionConsumerService', {}).get('url')}")
        print(f"DEBUG: IdP Entity ID: {idp_data.get('entityId')}")
        print(f"DEBUG: IdP SSO URL: {idp_data.get('singleSignOnService', {}).get('url')}")
        
        # DEBUG: Check certificate
        idp_cert = idp_data.get('x509cert', '')
        print(f"DEBUG: IdP Certificate (first 100 chars): {idp_cert[:100]}...")
        env_fingerprint = _certificate_fingerprint(idp_cert)
        print(f"DEBUG: IdP Certificate fingerprint (env): {env_fingerprint}")
        
        request_id = relay_state["request_id"]
        request.session.pop("saml_request_id", None)
        request.session.pop("saml_provider_id", None)
        
        # Process the SAML response
        try:
            saml_auth.process_response(request_id=request_id)
        except OneLogin_Saml2_ValidationError as validation_error:
            print(
                "DEBUG: OneLogin validation error encountered during process_response "
                f"(code={validation_error.code}): {validation_error.message}"
            )
            import traceback
            traceback.print_exc()
            raise
        
        # DEBUG: Get detailed error information
        errors = saml_auth.get_errors()
        print(f"DEBUG: SAML Errors: {errors}")
        print(f"DEBUG: SAML Last Error Reason: {saml_auth.get_last_error_reason()}")
        print(f"DEBUG: SAML Is Authenticated: {saml_auth.is_authenticated()}")
        
        try:
            print(f"DEBUG: SAML Name ID: {saml_auth.get_nameid()}")
        except:
            print("DEBUG: Could not get Name ID")
        
        # DEBUG: Check what attributes we got
        try:
            attributes = saml_auth.get_attributes()
            print(f"DEBUG: SAML Attributes: {attributes}")
        except:
            print("DEBUG: Could not get attributes")
        
    except RuntimeError as config_error:
        print(f"DEBUG: Runtime error: {config_error}")
        raise HTTPException(status_code=500, detail=str(config_error)) from config_error
    except Exception as exc:  # noqa: BLE001
        print(f"DEBUG: General exception: {exc}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Failed to process SAML response: {exc}") from exc

    errors = saml_auth.get_errors()
    if errors:
        # DEBUG: More detailed error logging
        print(f"DEBUG: Detailed SAML errors before raising exception: {errors}")
        print(f"DEBUG: SAML last error reason: {saml_auth.get_last_error_reason()}")
        
        # Check for specific signature validation errors
        if any('signature' in str(error).lower() for error in errors):
            print("DEBUG: Signature validation error detected")
            settings = saml_auth.get_settings()
            idp_data = settings.get_idp_data()
            cert = idp_data.get('x509cert', '')
            print(f"DEBUG: Certificate being used: {cert[:200]}...")
        
        raise HTTPException(status_code=400, detail=f"SAML response contained errors: {errors}")

    if not saml_auth.is_authenticated():
        print("DEBUG: SAML authentication failed - user not authenticated")
        raise HTTPException(status_code=401, detail="SAML authentication failed")

    attributes = saml_auth.get_attributes()
    print(f"DEBUG: Final attributes received: {attributes}")
    
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

    print(f"DEBUG: Email attribute extracted: {email_attr}")
    
    if not email_attr:
        raise HTTPException(status_code=400, detail="SAML response missing required email attribute")

    name_attr = _get_saml_attribute(attributes, SAML_NAME_ATTRIBUTE) if SAML_NAME_ATTRIBUTE else None
    print(f"DEBUG: Name attribute extracted: {name_attr}")

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
        print(f"DEBUG: Candidate role values from attributes: {flattened_roles}")
        has_allowed_role = any(role in flattened_roles for role in allowed_roles)
        if not has_allowed_role:
            print(
                f"DEBUG: User missing required SAML role. "
                f"Allowed roles={allowed_roles} "
                f"searched_keys={role_attribute_keys}"
            )
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
        print(f"DEBUG: Allowed emails: {allowed_emails}")
        print(f"DEBUG: User email: {email_attr.lower()}")
        if email_attr.lower() not in allowed_emails:
            raise HTTPException(status_code=401, detail="Not authorized")

    _set_authenticated_user(request, user_info)

    cached_redirect = _sanitize_return_to(relay_state.get("return_to"))
    session_redirect = _sanitize_return_to(request.session.pop("return_to", None))
    if cached_redirect:
        print(f"DEBUG: Using return_to from RelayState: {cached_redirect}")
    if not cached_redirect:
        cached_redirect = session_redirect
        if cached_redirect:
            print(f"DEBUG: Using return_to from session: {cached_redirect}")
    redirect_target = cached_redirect or _get_saml_default_redirect(application, request, provider=provider)
    
    print(f"DEBUG: Redirecting to: {redirect_target}")
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
                raise HTTPException(status_code=500, detail=f"SAML metadata validation errors: {remaining_errors}")
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail=str(config_error)) from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to generate SAML metadata: {exc}") from exc

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
        return JSONResponse({"error": str(e)}, status_code=401)
    
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
        return JSONResponse({"error": str(e)}, status_code=401)

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
    # 1) Clear user info from session
    _clear_auth_session(request)
    # 2) If stored tokens in session, remove them
    # request.session.pop("token", None)

    # 3) Redirect anywhere in *your* app after local logout
    return RedirectResponse(url=f"/{application}/login", status_code=302)

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
    email = form.get('email')
    password = form.get('password')

    user_info = {
        "email": email,
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "user",
        "roles": [],
    }

    # You can optionally grab user info from token["userinfo"]
    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

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
    if not ENABLE_USER_LOGIN:
        raise HTTPException(status_code=403, detail="User login not enabled")

    user_info = {
        "email": auth_input.email,
        "picture": f"{application}/appcode/profile.png",
        "auth_type": "user",
        "roles": [],
    }

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            raise HTTPException(status_code=401, detail="Not authorized")

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
        print(f"Error finding MainWindow subclass: {e}")
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
        print(f"Error reading app configuration: {e}")
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
