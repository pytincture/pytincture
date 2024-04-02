from operator import call
import os

import uvicorn
import zipfile
from fastapi import FastAPI, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pytincture.dataclass import get_parsed_output
from importlib.machinery import SourceFileLoader
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
                if not (file.endswith('.zip') or file.endswith('.pyt') or file.endswith('.pyc')):
                    if "__pycache__" not in root:
                        if file.endswith('.py'):
                            # Get the parsed output of the file
                            file_contents = get_parsed_output(file_path, host, protocol)
                            # Write the modified contents to the ZIP archive
                            zipf.writestr(arcname, file_contents)
                        else:
                            zipf.write(file_path, arcname)

    # Set the cursor to the beginning of the stream
    in_memory_zip.seek(0)
    
    return in_memory_zip

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

#Static files endpoint
app.mount("/appcode", StaticFiles(directory=MODULE_PATH), name="static")

@app.get("/appdata", response_class=HTMLResponse)
async def main(function_name, data_module):
    pass

#Application endpoint
@app.get("/{application}", response_class=HTMLResponse)
async def main(response: Response, application):
    index = open(f"{STATIC_PATH}/index.html").read().replace("***APPLICATION***", application)
    return index

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
