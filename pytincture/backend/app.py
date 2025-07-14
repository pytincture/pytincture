from operator import call
import os
import re
from signal import raise_signal
import sys
import json
import inspect
import io
import zipfile
import importlib

# FastAPI / Starlette
from fastapi import Depends, FastAPI, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

# Pytincture
from pytincture.dataclass import get_parsed_output, add_bff_docs_to_app
from importlib.machinery import SourceFileLoader

# Google OAuth via Authlib
from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config

# ========================
#  FASTAPI SETUP
# ========================

app = FastAPI(title="pyTincture API")

add_bff_docs_to_app(app)

def reload_mcp_tools():
    global mcp, sse_mcp_app  # Use globals or pass as needed if in a class/module
    
    # Step 1: Remove existing MCP-mounted routes to avoid duplicates
    # Filter out routes starting with "/sse-mcp" (adjust prefix if needed)
    app.router.routes = [
        route for route in app.router.routes
        if not route.path.startswith("/sse-mcp")
    ]
    
    # Step 2: Recreate FastMCP instance (rescans app for new endpoints/tools)
    mcp = FastMCP.from_fastapi(app=app)  # Optionally pass route_maps=[...] for custom mapping
    print("MCP Tools reloaded successfully.")
    
    # Step 3: Recreate SSE app
    sse_mcp_app = mcp.sse_app(path='/')
    
    # Step 4: Remount the updated SSE app
    app.mount("/sse-mcp", sse_mcp_app)

reload_mcp_tools()

def create_appcode_pkg_in_memory(host, protocol):
    """Generate an appcode package in memory for the browser to pull for the frontend."""
    appcode_folder = os.environ["MODULES_PATH"]
    in_memory_zip = io.BytesIO()
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(appcode_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, appcode_folder)
                if not (file.endswith('.zip') or file.endswith('.pyt') or file.endswith('.pyc') or file.endswith('.whl') or file.startswith('.')):
                    if "__pycache__" not in root:
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


@app.get("/favicon.ico")
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
    app_file_path = f"{static_path}/{application}.py"
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

origins = (
    "https://pypi.org",
    "http://0.0.0.0:8070",
    "http://localhost:8070",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from upstash_redis import Redis

import json
from upstash_redis import Redis  # or whatever your actual import is

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

MODULE_PATH = os.environ.get("MODULES_PATH")

try:
    ALLOWED_NOAUTH_CLASSCALLS = json.loads(os.environ.get("ALLOWED_NOAUTH_CLASSCALLS", "[]"))
except json.JSONDecodeError as e:
    raise RuntimeError("Invalid JSON in ALLOWED_NOAUTH_CLASSCALLS environment variable") from e


app.mount("/{application}/frontend", StaticFiles(directory=STATIC_PATH), name="static")


def is_noauth_allowed(file_name: str, class_name: str, function_name: str) -> bool:
    """
    Check if the given file, class, and function is allowed to be called without auth.
    """
    for entry in ALLOWED_NOAUTH_CLASSCALLS:
        if (entry.get("file") == file_name and
            entry.get("class") == class_name and
            entry.get("function") == function_name):
            return True
    return False

def require_auth(request: Request):
    if ENABLE_GOOGLE_AUTH or ENABLE_USER_LOGIN:
        user_session = request.session.get("user") or {}
        if user_session.get("email", None) in USER_SESSION_DICT:
            if user_session != USER_SESSION_DICT[user_session["email"]]:
                raise HTTPException(status_code=401, detail="Error with authentication")
            return user_session
        else:
            return None
    else:
        return {
            "email": "",
            "password": "",
            "picture": "appcode/profile.png"
        }

@app.get("/{application}/appcode/appcode.pyt")
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

@app.get("/classcall/{file_name}/{class_name}/{function_name}")
@app.post("/classcall/{file_name}/{class_name}/{function_name}")
async def class_call(
    file_name: str,
    class_name: str,
    function_name: str,
    request: Request
):
    
    # Determine if this call is allowed without auth.
    if is_noauth_allowed(file_name, class_name, function_name):
        user = "noauth"
    else:
        # Perform authentication check for calls not whitelisted for no-auth.
        user = require_auth(request)

    if not user:
        raise HTTPException(status_code=401, detail="Call not authorized")

    # 1) Dynamically load the file_name
    appcode_folder = os.environ["MODULES_PATH"]
    if not file_name.endswith(".py"):
        file_name += ".py"
    if not os.path.exists(os.path.join(appcode_folder, file_name)):
        raise HTTPException(status_code=404, detail=f"File {file_name} not found in appcode folder")
    
    # 2) Dynamically load the class_name
    file_path = os.path.join(appcode_folder, file_name)
    
    loader = SourceFileLoader(class_name, file_path)
    spec = importlib.util.spec_from_loader(class_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    cls = getattr(module, class_name)
    instance = cls(_user=user)

    # 3) Get the function
    func = getattr(instance, function_name)

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
    if callable(func):
        args = data.get("args", [])
        kwargs = data.get("kwargs", {})
        
        # Handle structured args format if present
        if args and isinstance(args[0], dict) and 'value' in args[0]:
            args = [arg['value'] for arg in args]
        
        # Check if the function is async
        if inspect.iscoroutinefunction(func):
            # Handle async function
            result = await func(*args, **kwargs)
        else:
            # Handle sync function
            result = func(*args, **kwargs)
        
        return result
    
    return func

@app.post("/logs")
async def logs_endpoint(request: Request, user=Depends(require_auth)):
    data = await request.json()
    print(data)
    return {"status": "ok"}


# ================
# GOOGLE OAUTH2 SETUP
# ================

ENABLE_GOOGLE_AUTH = os.getenv("ENABLE_GOOGLE_AUTH", "false").lower() == "true"
ENABLE_USER_LOGIN = os.getenv("ENABLE_USER_LOGIN", "false").lower() == "true"

config_data = {
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
    "SECRET_KEY": os.getenv("SECRET_KEY", "verysecretkey"),
}
config = Config(environ=config_data)

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
app.add_middleware(SessionMiddleware, secret_key=config.get("SECRET_KEY"))

@app.get("/{application}/auth/google")
async def auth_google(request: Request, application: str):
    """
    Redirect the user to Google's OAuth2 screen.
    """

    forwarded_proto = request.headers.get("x-forwarded-proto")
    host = request.headers["host"]
    protocol = forwarded_proto or request.url.scheme
    redirect_uri = f"{protocol}://{host}/{application}/auth/google/callback"

    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/{application}/auth/google/callback", name="auth_google_callback")
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
    return_to = request.session.get("return_to", "/")
    return RedirectResponse(url=return_to)

@app.get("/{application}/auth/logout")
def logout(request: Request,  application: str):
    """
    Logs the user out of *your app only*.
    """
    # 1) Clear user info from session
    request.session.pop("user", None)
    # 2) If stored tokens in session, remove them
    # request.session.pop("token", None)

    # 3) Redirect anywhere in *your* app after local logout
    return RedirectResponse(url=f"/{application}/login", status_code=302)

# ======================
# LOGIN PAGE
# ======================

@app.get("/{application}/login", response_class=HTMLResponse)
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

    # Conditionally add Google login button
    if ENABLE_GOOGLE_AUTH:
        html_content += f'''
            <a href="auth/google" class="login-button">Login with Google</a>
        '''

    # Conditionally add Email/Password login form
    if ENABLE_USER_LOGIN:
        if ENABLE_GOOGLE_AUTH:
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
    if not ENABLE_GOOGLE_AUTH and not ENABLE_USER_LOGIN:
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

@app.post("/{application}/auth/user")
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

    # See if we stored a "return_to" path earlier; default to "/"
    return_to = request.session.get("return_to", f"/{application}")
    return RedirectResponse(url=return_to, status_code=303)

# ======================
# The /{application} route
# ======================
@app.get("/{application}", response_class=HTMLResponse)
async def main_app_route(response: Response, application: str, request: Request):
    """
    1) Check if user is in session.
    2) If not, store this path in session, redirect to /login.
    3) If yes, serve the index.html with the relevant widgetset replaced.
    """
    # Check session
    user_session = require_auth(request)
    user_entry = user_session.get("email", "") if user_session else "noauth"
    
    if (ENABLE_USER_LOGIN or ENABLE_GOOGLE_AUTH) and not USER_SESSION_DICT.get(user_entry, None):
        # Not logged in, so remember where they wanted to go:
        request.session["return_to"] = f"/{application}"
        # Then send them to Login page:
        return RedirectResponse(url=f"/{application}/login")
    else:
        request.session["user"] = require_auth(request)
        user_session = request.session.get("user")

    # Already logged in, proceed normally
    appcode_folder = os.environ["MODULES_PATH"]
    widgetset = get_widgetset(application, appcode_folder)

    # Modify the index.html to include the application name and widgetset
    index_html = open(f"{STATIC_PATH}/index.html").read()
    index_html = index_html.replace("***APPLICATION***", application)
    
    # Find the proper entrypoint class (MainWindow subclass)
    app_file_path = f"{appcode_folder}/{application}.py"
    if os.path.exists(app_file_path):
        main_window_class = find_main_window_subclass(app_file_path)
        if main_window_class:
            # Use the discovered MainWindow subclass name as the entrypoint
            index_html = index_html.replace("***ENTRYPOINT***", main_window_class)
        else:
            # If no MainWindow subclass is found, fallback to using application name
            index_html = index_html.replace("***ENTRYPOINT***", application)
    else:
        # If file doesn't exist, just use the application name as-is
        index_html = index_html.replace("***ENTRYPOINT***", application)
    
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


# =================
# RUN THE APP
# =================
# Typically:
# uvicorn app:app --host 0.0.0.0 --port 8070
