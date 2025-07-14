"""
pyTincture uvicorn launcher
"""

__version__ = "0.9.9"

from multiprocessing import Process, freeze_support
import os
import signal
import shutil
import zipfile

import uvicorn


def main(port, ssl_keyfile=None, ssl_certfile=None):
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


def launch_service(
    modules_folder=os.getcwd(), 
    port=8070, 
    ssl_keyfile=None, 
    ssl_certfile=None, 
    env_vars: dict = {},
    bff_docs_path: str = "/bff-docs",
    bff_docs_title: str = "pyTincture BFF API"
):
    os.environ["MODULES_PATH"] = modules_folder
    
    # Add BFF configuration to environment variables
    os.environ["BFF_DOCS_PATH"] = bff_docs_path.lstrip('/')  # Remove leading slash if present
    os.environ["BFF_DOCS_TITLE"] = bff_docs_title
        
    for akey, value in env_vars.items():
        os.environ[akey] = value

    main_application = Process(target=main, args=(port, ssl_keyfile, ssl_certfile,))
    # launch data and main applications
    main_application.start()
    
    def terminate_all(*args):
        main_application.terminate()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # wait for main application death
    main_application.join()
