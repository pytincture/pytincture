import os
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
import secrets
import time
import base64
import hashlib
from collections import OrderedDict
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
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

# Pytincture
from pytincture import get_modules_path
from pytincture.dataclass import get_parsed_output, add_bff_docs_to_app
from importlib.machinery import SourceFileLoader

# Google OAuth via Authlib
from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config

from typing import Any, Union, Dict, List, Optional, Iterable, AsyncIterable, Set, Callable

# Pydantic for JSON validation
from pydantic import BaseModel

# SAML Toolkit (OneLogin)
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.errors import OneLogin_Saml2_ValidationError
from urllib.parse import urlparse

# ========================
#  FASTAPI SETUP
# ========================

app = FastAPI(title="pyTincture API")

def reload_mcp_tools():
    global mcp, sse_mcp_app  # Use globals or pass as needed if in a class/module
    
    # Step 1: Remove existing MCP-mounted routes to avoid duplicates
    # Filter out routes starting with "/sse-mcp" (adjust prefix if needed)
    app.router.routes = [
        route for route in app.router.routes
        if not route.path.startswith("/sse-mcp")
    ]
    
    # Step 2: Recreate FastMCP instance (rescans app for new endpoints/tools)
    mcp = FastMCP.from_fastapi(app=app, name="short")  # Add name="short" to reduce prefixed/suffixed lengths
    print("MCP Tools reloaded successfully.")
    
    # Test tool name lengths
    print("\nTesting MCP Tool Name Lengths:")
    tools = asyncio.run(mcp.get_tools())
    for tool in tools.values():
        name_length = len(tool.name)
        print(f"Tool: {tool.name} | Length: {name_length} chars | Over Limit: {name_length > 64}")
        if name_length > 64:
            print(f"  WARNING: Exceeds 64-char limit! Suggested truncate: {tool.name[:61]}...")
    
    # Step 3: Recreate SSE app
    sse_mcp_app = mcp.sse_app(path='/')
    
    # Step 4: Remount the updated SSE app
    app.mount("/sse-mcp", sse_mcp_app)

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

def require_auth(request: Request):
    if ENABLE_GOOGLE_AUTH or ENABLE_USER_LOGIN or ENABLE_SAML_AUTH:
        user_session = request.session.get("user") or {}
        email = user_session.get("email")
        print(f"DEBUG AUTH: session email={email} keys={list(request.session.keys())}")
        if not email:
            print("DEBUG AUTH: no email found in session")
            return None

        backend_snapshot = USER_SESSION_DICT.get(email)
        if backend_snapshot is None:
            print("DEBUG AUTH: email missing from USER_SESSION_DICT, seeding from session")
            USER_SESSION_DICT[email] = user_session
            return user_session

        if user_session != backend_snapshot:
            print("DEBUG AUTH: session and backend snapshots differ")
            raise HTTPException(status_code=401, detail="Error with authentication")

        print("DEBUG AUTH: session validated via USER_SESSION_DICT")
        return user_session
    else:
        return {
            "email": "",
            "password": "",
            "picture": "appcode/profile.png"
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
    
    loader = SourceFileLoader(class_name, module_file_path)
    spec = importlib.util.spec_from_loader(class_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
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
            user=user,
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

config_data = {
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
    "SECRET_KEY": os.getenv("SECRET_KEY", "verysecretkey"),
}
config = Config(environ=config_data)

SAML_EMAIL_ATTRIBUTE = os.getenv("SAML_EMAIL_ATTRIBUTE", "email")
SAML_NAME_ATTRIBUTE = os.getenv("SAML_NAME_ATTRIBUTE", "givenName")
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
SAML_REQUEST_CACHE_MAX = int(os.getenv("SAML_REQUEST_CACHE_MAX", "512"))
SAML_REQUEST_CACHE_TTL = int(os.getenv("SAML_REQUEST_CACHE_TTL", "600"))

_SAML_REQUEST_STATE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _generate_relay_token() -> str:
    return secrets.token_urlsafe(32)


def _prune_saml_state(now: float) -> None:
    expired: List[str] = []
    for key, payload in _SAML_REQUEST_STATE.items():
        timestamp = payload.get("ts", now)
        if now - timestamp > SAML_REQUEST_CACHE_TTL:
            expired.append(key)
    for key in expired:
        _SAML_REQUEST_STATE.pop(key, None)
    while len(_SAML_REQUEST_STATE) > SAML_REQUEST_CACHE_MAX:
        _SAML_REQUEST_STATE.popitem(last=False)


def _store_saml_state(token: str, payload: Dict[str, Any]) -> None:
    now = time.time()
    payload = dict(payload)
    payload["ts"] = now
    _SAML_REQUEST_STATE[token] = payload
    _SAML_REQUEST_STATE.move_to_end(token)
    _prune_saml_state(now)


def _consume_saml_state(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    payload = _SAML_REQUEST_STATE.pop(token, None)
    if not payload:
        return None
    timestamp = payload.pop("ts", time.time())
    if time.time() - timestamp > SAML_REQUEST_CACHE_TTL:
        return None
    return payload


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


def _build_saml_settings(request: Request, application: str) -> Dict[str, Any]:
    """
    Construct the settings dict consumed by python3-saml using runtime request data.
    """
    origin = _extract_request_origin(request)
    default_entity = f"{origin['base_url']}/{application}/auth/saml/metadata"
    entity_id = _apply_saml_template(SAML_SP_ENTITY_ID or default_entity, application, origin)

    default_acs = f"{origin['base_url']}/{application}/auth/saml/acs"
    if not SAML_SP_ASSERTION_URL and entity_id:
        parsed_entity = urlparse(entity_id)
        if parsed_entity.scheme and parsed_entity.netloc:
            default_acs = f"{parsed_entity.scheme}://{parsed_entity.netloc}/{application}/auth/saml/acs"
    acs_url = _apply_saml_template(SAML_SP_ASSERTION_URL or default_acs, application, origin)

    idp_entity = _apply_saml_template(SAML_IDP_ENTITY_ID, application, origin)
    idp_sso = _apply_saml_template(SAML_IDP_SSO_URL, application, origin)
    idp_slo = _apply_saml_template(SAML_IDP_SLO_URL, application, origin) if SAML_IDP_SLO_URL else ""
    idp_cert = _normalize_certificate(SAML_IDP_X509_CERT)

    if not idp_entity or not idp_sso or not idp_cert:
        raise RuntimeError("SAML IdP configuration is incomplete. Ensure SAML_IDP_ENTITY_ID, SAML_IDP_SSO_URL, and SAML_IDP_X509_CERT are set.")

    sp_settings: Dict[str, Any] = {
        "entityId": entity_id,
        "assertionConsumerService": {
            "url": acs_url,
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    }

    sp_cert = _normalize_certificate(SAML_SP_X509_CERT)
    sp_key = _normalize_certificate(SAML_SP_PRIVATE_KEY)
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


def _init_saml_auth(request: Request, application: str, post_data: Optional[Dict[str, Any]] = None) -> OneLogin_Saml2_Auth:
    """
    Convenience wrapper to instantiate a SAML Auth client.
    """
    request_data = _build_saml_request_data(request, post_data=post_data)
    settings = _build_saml_settings(request, application)
    return OneLogin_Saml2_Auth(request_data, old_settings=settings)


def _get_saml_default_redirect(application: str, request: Request) -> str:
    """
    Produce the default redirect target using optional templates.
    """
    origin = _extract_request_origin(request)
    default_target = f"/{application}"
    configured_target = _apply_saml_template(SAML_DEFAULT_REDIRECT, application, origin) if SAML_DEFAULT_REDIRECT else ""
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

# Create an OAuth object and register the Google provider
if ENABLE_GOOGLE_AUTH:
    oauth = OAuth(config)
    oauth.register(
        name="google",
        client_id=config.get("GOOGLE_CLIENT_ID"),
        client_secret=config.get("GOOGLE_CLIENT_SECRET"),
        # Use the well-known OIDC discovery document
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
else:
    oauth = None

# Add session middleware (needed to store "return_to" and user info)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.get("SECRET_KEY"),
    same_site="lax",   # allow session cookies on top-level navigation while keeping CSRF protection
    https_only=False,  # flip to True once you’re behind HTTPS
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
    """
    Redirect the user to the configured SAML Identity Provider.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

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
        saml_auth = _init_saml_auth(request, application)
    except RuntimeError as config_error:
        raise HTTPException(status_code=500, detail=str(config_error)) from config_error
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"SAML initialization failed: {exc}") from exc

    relay_token = _generate_relay_token()
    auth_url = saml_auth.login(return_to=relay_token)
    request_id = saml_auth.get_last_request_id()
    request.session["saml_request_id"] = request_id
    fallback_return = safe_return_to or request.session.get("return_to")
    _store_saml_state(
        relay_token,
        {
            "request_id": request_id,
            "return_to": fallback_return,
        },
    )
    print(
        f"DEBUG: SAML login generated request ID: {request_id} "
        f"and relay token: {relay_token}"
    )
    _debug_session_state("saml_login:stored_request_id", request)
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
    """
    Handle the assertion consumer service (ACS) endpoint invoked by the IdP.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    _debug_session_state("saml_acs:entry", request)
    form_data = await request.form()
    post_data = dict(form_data.multi_items())
    relay_token = post_data.get("RelayState")
    cached_state = _consume_saml_state(relay_token)
    if cached_state:
        print(
            f"DEBUG: Retrieved cached relay state for token {relay_token}: "
            f"{cached_state}"
        )
    else:
        print(f"DEBUG: No cached relay state found for token {relay_token}")
    
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
        saml_auth = _init_saml_auth(request, application, post_data=post_data)
        
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
        
        request_id = None
        if cached_state and cached_state.get("request_id"):
            request_id = cached_state["request_id"]
            print(f"DEBUG: Using request ID from relay cache: {request_id}")
        else:
            request_id = request.session.pop("saml_request_id", None)
            print(f"DEBUG: Request ID from session (pre-process): {request_id}")
            _debug_session_state("saml_acs:after_pop_request_id", request)
        
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

    if SAML_ALLOWED_ROLES:
        role_values: List[str] = []
        normalized_key_map = {key.lower(): key for key in normalized_attributes.keys()}
        for candidate_key in SAML_ROLE_ATTRIBUTE_KEYS:
            matched_key = normalized_key_map.get(candidate_key.lower())
            if matched_key:
                role_values.extend(normalized_attributes.get(matched_key, []))
        flattened_roles = {str(value).strip().lower() for value in role_values if isinstance(value, str) and value.strip()}
        print(f"DEBUG: Candidate role values from attributes: {flattened_roles}")
        has_allowed_role = any(role in flattened_roles for role in SAML_ALLOWED_ROLES)
        if not has_allowed_role:
            print(
                f"DEBUG: User missing required SAML role. "
                f"Allowed roles={SAML_ALLOWED_ROLES} "
                f"searched_keys={SAML_ROLE_ATTRIBUTE_KEYS}"
            )
            raise HTTPException(status_code=401, detail="Not authorized for this application")

    user_info = {
        "email": email_attr,
        "name": name_attr or "",
        "picture": f"{application}/appcode/profile.png",
        "saml": {
            "name_id": saml_auth.get_nameid(),
            "session_index": saml_auth.get_session_index(),
            "attributes": normalized_attributes,
        },
    }

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = [email.strip().lower() for email in os.getenv("ALLOWED_EMAILS", "").split(",") if email.strip()]
        print(f"DEBUG: Allowed emails: {allowed_emails}")
        print(f"DEBUG: User email: {email_attr.lower()}")
        if email_attr.lower() not in allowed_emails:
            raise HTTPException(status_code=401, detail="Not authorized")

    USER_SESSION_DICT[email_attr] = user_info
    request.session["user"] = user_info
    request.session["saml_name_id"] = saml_auth.get_nameid()
    request.session["saml_session_index"] = saml_auth.get_session_index()

    cached_redirect = None
    if cached_state:
        cached_redirect = _sanitize_return_to(cached_state.get("return_to"))
        if cached_redirect:
            print(f"DEBUG: Using cached return_to from relay cache: {cached_redirect}")
    if not cached_redirect:
        cached_redirect = _sanitize_return_to(request.session.pop("return_to", None))
        if cached_redirect:
            print(f"DEBUG: Using return_to from session: {cached_redirect}")
    redirect_target = cached_redirect or _get_saml_default_redirect(application, request)
    
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
    """
    Provide SP metadata for the configured SAML settings.
    """
    if not ENABLE_SAML_AUTH:
        raise HTTPException(status_code=404, detail="SAML authentication not enabled")

    try:
        settings = OneLogin_Saml2_Settings(settings=_build_saml_settings(request, application), sp_validation_only=True)
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
    
    user_info = token.get("userinfo")

    # You can optionally grab user info from token["userinfo"]
    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    USER_SESSION_DICT[user_info["email"]] = user_info
    request.session["user"] = user_info  # store in session

    # See if we stored a "return_to" path earlier; default to "/"
    return_to = _sanitize_return_to(request.session.pop("return_to", None)) or "/"
    return RedirectResponse(url=return_to)

@app.get("/{application}/auth/logout", operation_id="logoutUser", response_class=RedirectResponse, responses={302: {"description": "RedirectResponse (to login page)"}})
def logout(request: Request,  application: str):
    """
    Logs the user out of *your app only*.
    """
    # 1) Clear user info from session
    request.session.pop("user", None)
    request.session.pop("saml_name_id", None)
    request.session.pop("saml_session_index", None)
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
    # Retrieve configuration flags from environment variables
    ENABLE_GOOGLE_AUTH = os.environ.get("ENABLE_GOOGLE_AUTH", "false").lower() == "true"
    ENABLE_USER_LOGIN = os.environ.get("ENABLE_USER_LOGIN", "false").lower() == "true"

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
                display: inline-block;
                margin: 10px 0;
                width: 80%;
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

    if ENABLE_GOOGLE_AUTH:
        social_buttons.append(
            '<a href="auth/google" class="login-button">Login with Google</a>'
        )

    if ENABLE_SAML_AUTH:
        social_buttons.append(
            '<a href="auth/saml/login" class="login-button">Login with SAML</a>'
        )

    if social_buttons:
        html_content += "\n".join(social_buttons)

    # Conditionally add Email/Password login form
    if ENABLE_USER_LOGIN:
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
    if not (ENABLE_GOOGLE_AUTH or ENABLE_USER_LOGIN or ENABLE_SAML_AUTH):
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
        "password": password,
        "picture": f"{application}/appcode/profile.png"
    }

    # You can optionally grab user info from token["userinfo"]
    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            return JSONResponse({"error": "Not authorized"}, status_code=401)

    USER_SESSION_DICT[user_info["email"]] = user_info
    request.session["user"] = user_info  # store in session

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
        "password": auth_input.password,
        "picture": f"{application}/appcode/profile.png"
    }

    if os.getenv("ALLOWED_EMAILS", "") != "":
        allowed_emails = os.getenv("ALLOWED_EMAILS").split(",")  # Assuming comma-separated
        if user_info.get("email", "").lower() not in [email.strip().lower() for email in allowed_emails]:
            raise HTTPException(status_code=401, detail="Not authorized")

    USER_SESSION_DICT[user_info["email"]] = user_info
    request.session["user"] = user_info  # store in session

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
    user_session = require_auth(request)
    user_entry = user_session.get("email", "") if user_session else "noauth"
    print(f"DEBUG MAIN_APP: user_entry={user_entry} cache_has={user_entry in USER_SESSION_DICT if hasattr(USER_SESSION_DICT, '__contains__') else user_entry in USER_SESSION_DICT}")
    
    if (ENABLE_USER_LOGIN or ENABLE_GOOGLE_AUTH or ENABLE_SAML_AUTH) and not USER_SESSION_DICT.get(user_entry, None):
        # Not logged in, so remember where they wanted to go:
        request.session["return_to"] = f"/{application}"
        print(f"DEBUG MAIN_APP: user missing from session store, redirecting to login. return_to set to /{application}")
        # Then send them to Login page:
        return RedirectResponse(url=f"/{application}/login")
    else:
        request.session["user"] = require_auth(request)
        user_session = request.session.get("user")

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
        loader = SourceFileLoader(module_name, file_path)
        spec = importlib.util.spec_from_loader(module_name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        
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


add_bff_docs_to_app(app)
reload_mcp_tools()

# =================
# RUN THE APP
# =================
# Typically:
# uvicorn app:app --host 0.0.0.0 --port 8070
