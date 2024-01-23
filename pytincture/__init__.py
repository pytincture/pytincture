"""
pyTincture uvicorn launcher
"""
from multiprocessing import Process, freeze_support
import os
import signal
import shutil
import zipfile

import uvicorn


def create_pytincture_pkg():
    """ Generate a pytincture widgetset package for the browser to pull for the frontend"""
    pytincture_folder = os.path.join(os.path.dirname(__file__), "../pytincture")
    zip_path = os.path.join(os.environ["MODULES_PATH"], "pytincture.zip")

    # File to be replaced with an empty file
    file_to_replace = "pytincture/__init__.py"

    # Create a zip file
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(pytincture_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.join(pytincture_folder, '..'))
                # Check if this is the file to replace
                if arcname == file_to_replace:
                    zipf.writestr(arcname, '')  # Add an empty file
                else:
                    zipf.write(file_path, arcname)

def create_appcode_pkg():
    """ Generate an appcode package for the browser to pull for the frontend."""
    appcode_folder = os.environ["MODULES_PATH"]
    zip_path = os.path.join(appcode_folder, "appcode.pyt")

    # Create a zip file
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(appcode_folder):
            for file in files:
                file_path = os.path.join(root, file)
                # Calculate arcname to be relative to appcode_folder
                arcname = os.path.relpath(file_path, appcode_folder)
                # Exclude the zip file itself
                if not ".zip" in file and not ".pyt" in file and not ".pyc" in file:
                    if not "__pycache__" in file:
                        zipf.write(file_path, arcname)

def main(port, ssl_keyfile=None, ssl_certfile=None):
    create_appcode_pkg()
    create_pytincture_pkg()
    uvicorn.run(
        "pytincture.backend.app:app",
        host="0.0.0.0",
        port=port,
        log_level="debug",
        access_log="access.log",
        reload=False,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile, 
    )

def cleanup():
    print("cleanup process running")

def launch_service(modules_folder=os.getcwd(), port=8070, ssl_keyfile=None, ssl_certfile=None):
    os.environ["MODULES_PATH"] = modules_folder
        
    main_application = Process(target=main, args=(port, ssl_keyfile, ssl_certfile,))
    # launch data and main applications
    main_application.start()
    
    def terminate_all(*args):
        main_application.terminate()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # wait for main application death
    main_application.join()

if __name__ == "__main__":
    freeze_support()
    #launch_service(modules_folder=os.getcwd())
