from operator import call
import os
import re
import sys
import json
import io
import zipfile
import importlib

# FastAPI / Starlette
from fastapi import Depends, FastAPI, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

# Pytincture
from pytincture.dataclass import get_parsed_output
from importlib.machinery import SourceFileLoader

# Google OAuth via Authlib
from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config

# ========================
#  FASTAPI SETUP
# ========================

app = FastAPI()

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
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_PATH = os.path.join(os.path.dirname(__file__), "../frontend/")
MODULE_PATH = os.environ.get("MODULES_PATH")

app.mount("/frontend", StaticFiles(directory=STATIC_PATH), name="static")

def require_auth(request: Request):
    user_session = request.session.get("user")
    if not user_session:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    # Optionally return user info if you need it in the endpoint
    return user_session


# 2) Protect the endpoints using `Depends(require_auth)`

@app.get("/appcode/pytincture.zip")
def download_pytincture(user=Depends(require_auth)):
    file_like = create_pytincture_pkg_in_memory()
    return StreamingResponse(
        file_like,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=pytincture.zip"}
    )

@app.get("/appcode/appcode.pyt")
def download_appcode(request: Request, user=Depends(require_auth)):
    protocol = request.url.scheme
    host = request.headers["host"]
    file_like = create_appcode_pkg_in_memory(host, protocol)
    return StreamingResponse(
        file_like,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=appcode.pyt"}
    )

@app.get("/classcall/{file_name}/{class_name}/{function_name}")
@app.post("/classcall/{file_name}/{class_name}/{function_name}")
async def class_call(
    file_name: str,
    class_name: str,
    function_name: str,
    request: Request,
    user=Depends(require_auth)  # ensure the caller is logged in
):
    # 1) Dynamically load the file_name
    appcode_folder = os.environ["MODULES_PATH"]
    file_path = os.path.join(appcode_folder, file_name)
    
    loader = SourceFileLoader(class_name, file_path)
    spec = loader.load_module()
    
    # 2) Get the class and create an instance
    cls = getattr(spec, class_name)
    instance = cls(_user=user)  # <--- pass user to constructor

    # 3) Get the function
    func = getattr(instance, function_name)

    # 4) If it's a POST, parse JSON body
    data = {}
    if request.method == "POST":
        data = await request.json()
        data = json.loads(data)

    # 5) Call the function with *args / **kwargs if needed
    if callable(func):
        return func(*data.get("args", []), **data.get("kwargs", {}))
    
    return func

@app.post("/logs")
async def logs_endpoint(request: Request, user=Depends(require_auth)):
    data = await request.json()
    print(data)
    return {"status": "ok"}

app.mount("/appcode", StaticFiles(directory=MODULE_PATH), name="static")

@app.get("/appdata", response_class=HTMLResponse)
async def main(function_name, data_module):
    pass

# ================
# GOOGLE OAUTH2 SETUP
# ================


config_data = {
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
    "SECRET_KEY": os.getenv("SECRET_KEY", "verysecretkey"),
}
config = Config(environ=config_data)

# Create an OAuth object and register the Google provider
oauth = OAuth(config)
oauth.register(
    name="google",
    client_id=config.get("GOOGLE_CLIENT_ID"),
    client_secret=config.get("GOOGLE_CLIENT_SECRET"),
    # Use the well-known OIDC discovery document
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Add session middleware (needed to store "return_to" and user info)
app.add_middleware(SessionMiddleware, secret_key=config.get("SECRET_KEY"))

@app.get("/auth/google")
async def auth_google(request: Request):
    """
    Redirect the user to Google's OAuth2 screen.
    """
    redirect_uri = request.url_for("auth_google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    """
    Google redirects here after login. Authlib will exchange code for token.
    We'll store user info in the session, then redirect back to original app path.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    # You can optionally grab user info from token["userinfo"]
    user_info = token.get("userinfo")
    request.session["user"] = user_info  # store in session

    # See if we stored a "return_to" path earlier; default to "/"
    return_to = request.session.pop("return_to", "/")
    return RedirectResponse(url=return_to)

@app.get("/auth/logout")
def logout(request: Request):
    """
    Logs the user out of *your app only*.
    """
    # 1) Clear user info from session
    request.session.pop("user", None)
    # 2) If stored tokens in session, remove them
    # request.session.pop("token", None)

    # 3) Redirect anywhere in *your* app after local logout
    return RedirectResponse(url="/")

# ======================
# The /{application} route
# ======================
@app.get("/{application}", response_class=HTMLResponse)
async def main_app_route(response: Response, application: str, request: Request):
    """
    1) Check if user is in session.
    2) If not, store this path in session, redirect to /auth/google.
    3) If yes, serve the index.html with the relevant widgetset replaced.
    """
    # Check session
    user_session = request.session.get("user")
    if not user_session:
        # Not logged in, so remember where they wanted to go:
        request.session["return_to"] = f"/{application}"
        # Then send them to Google:
        return RedirectResponse(url="/auth/google")

    # Already logged in, proceed normally
    appcode_folder = os.environ["MODULES_PATH"]
    widgetset = get_widgetset(application, appcode_folder)

    index_html = open(f"{STATIC_PATH}/index.html").read()
    index_html = index_html.replace("***APPLICATION***", application)
    index_html = index_html.replace("***WIDGETSET***", widgetset)
    return HTMLResponse(content=index_html)

# =================
# RUN THE APP
# =================
# Typically:
# uvicorn app:app --host 0.0.0.0 --port 8070
