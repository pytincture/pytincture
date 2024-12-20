from operator import call
import os
import re
import sys

import uvicorn
import zipfile
from fastapi import FastAPI, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pytincture.dataclass import get_parsed_output
from importlib.machinery import SourceFileLoader
import importlib
from typing import Callable
import json
import io


app = FastAPI()

def create_appcode_pkg_in_memory(host, protocol):
    """ Generate an appcode package in memory for the browser to pull for the frontend."""
    appcode_folder = os.environ["MODULES_PATH"]

    # Create a BytesIO object to act as a file in memory
    in_memory_zip = io.BytesIO()

    # Create a zip file in memory
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(appcode_folder):
            for file in files:
                file_path = os.path.join(root, file)
                # Calculate arcname to be relative to appcode_folder
                arcname = os.path.relpath(file_path, appcode_folder)
                # Exclude the zip file itself, .pyt files, and .pyc files
                if not (file.endswith('.zip') or file.endswith('.pyt') or file.endswith('.pyc') or file.endswith('.whl')):
                    if "__pycache__" not in root:
                        if file.endswith('.py'):
                            # Get the parsed output of the file
                            file_contents = get_parsed_output(file_path, host, protocol)
                            # Write the modified contents to the ZIP archive
                            if not os.path.getsize(file_path) == 0:
                                zipf.writestr(arcname, file_contents)
                            else:
                                zipf.write(file_path, arcname)
                        else:
                            zipf.write(file_path, arcname)

    # Set the cursor to the beginning of the stream
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
    app_file_path = f"{static_path}/{application}.py"
    imports = []
    widgetset = None

    # Check if the application file exists
    if os.path.exists(app_file_path):
        with open(app_file_path, 'r') as app_file:
            # Regex to capture both "import" and "from ... import ..." statements
            import_pattern = re.compile(r'^\s*(import|from)\s+([a-zA-Z0-9_]+)')
            
            # Scan each line of the file
            for line in app_file:
                match = import_pattern.match(line)
                if match:
                    module_name = match.group(2)  # Extract the module name
                    imports.append(module_name)

    # Try importing each module and check for `__widgetset__`
    for module_name in imports:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, '__widgetset__'):
                widgetset = getattr(module, '__widgetset__')  # Use the widgetset from the module
                break  # Stop once we find the widgetset
        except ModuleNotFoundError:
            continue  # Skip if the module is not found

    # If no widgetset is found, return a default one
    return widgetset if widgetset else ""

def create_pytincture_pkg_in_memory():
    """ Generate a pytincture widgetset package in memory """
    pytincture_folder = os.path.join(os.path.dirname(__file__), "../../pytincture")
    file_to_replace = "pytincture/__init__.py"

    # Create a BytesIO object to act as a file in memory
    in_memory_zip = io.BytesIO()

    # Create a zip file in memory
    with zipfile.ZipFile(in_memory_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(pytincture_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.join(pytincture_folder, '..'))
                # Check if this is the file to replace
                if arcname == file_to_replace:
                    zipf.writestr(arcname, '')  # Add an empty file
                else:
                    zipf.write(file_path, arcname)

    # Set the cursor to the beginning of the stream
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


#Static files endpoint
app.mount("/frontend", StaticFiles(directory=STATIC_PATH), name="static")

#Pytincture package endpoint
@app.get("/appcode/pytincture.zip")
def download_pytincture():
    file_like = create_pytincture_pkg_in_memory()
    return StreamingResponse(file_like, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=pytincture.zip"})

#Pytincture package endpoint
@app.get("/appcode/appcode.pyt")
def download_appcode(request: Request):
    protocol = request.url.scheme
    host = request.headers["host"]
    file_like = create_appcode_pkg_in_memory(host, protocol)
    return StreamingResponse(file_like, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=appcode.pyt"})

#Class call endpoint
@app.get("/classcall/{file_name}/{class_name}/{function_name}")
@app.post("/classcall/{file_name}/{class_name}/{function_name}")
async def class_call(file_name: str, class_name: str, function_name: str, request: Request):
    appcode_folder = os.environ["MODULES_PATH"]
    file_path = os.path.join(appcode_folder, file_name)
    
    loader = SourceFileLoader(class_name, file_path)
    spec = loader.load_module()
    
    cls = getattr(spec, class_name)
    instance = cls()
    func = getattr(instance, function_name)
    
    if isinstance(func, Callable):
        data = await request.json()
        data = json.loads(data)
        return func(*data["args"], **data["kwargs"])
    else:
        return func

@app.post("/logs")
async def main(request: Request):
    data = await request.json()
    print(data)

#Static files endpoint
app.mount("/appcode", StaticFiles(directory=MODULE_PATH), name="static")

@app.get("/appdata", response_class=HTMLResponse)
async def main(function_name, data_module):
    pass

#Application endpoint
@app.get("/{application}", response_class=HTMLResponse)
async def main(response: Response, application):
    appcode_folder = os.environ["MODULES_PATH"]
    widgetset = get_widgetset(application, appcode_folder)

    index = open(f"{STATIC_PATH}/index.html").read()
    index = index.replace("***APPLICATION***", application)
    index = index.replace("***WIDGETSET***", widgetset)  # Use the detected widgetset
    return index

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
