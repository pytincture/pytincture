"""
pyTincture uvicorn launcher
"""

__version__ = "0.9.27"

from multiprocessing import Process, freeze_support
import os
import signal
import shutil
import zipfile
import asyncio

try:
    import uvloop  # type: ignore
except ImportError:  # pragma: no cover - depends on installation
    uvloop = None

import uvicorn

MODULES_PATH = os.environ.get("MODULES_PATH")


def set_modules_path(path=None):
    """
    Store the active modules path in-process and keep the environment in sync.
    """
    global MODULES_PATH
    MODULES_PATH = path
    if path is None:
        os.environ.pop("MODULES_PATH", None)
    else:
        os.environ["MODULES_PATH"] = path


def get_modules_path():
    """
    Retrieve the active modules path, falling back to the environment or CWD.
    """
    if MODULES_PATH is not None:
        return MODULES_PATH
    return os.environ.get("MODULES_PATH") or os.getcwd()


def main(port, ssl_keyfile=None, ssl_certfile=None, modules_folder=None):
    if modules_folder is not None:
        set_modules_path(os.fspath(modules_folder))

    run_kwargs = dict(
        host="0.0.0.0",
        port=port,
        log_level="debug",
        access_log="access.log",
        reload=False,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )

    if uvloop is not None:
        try:
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            run_kwargs["loop"] = "uvloop"
        except Exception:  # pragma: no cover - uvloop unsupported platform
            run_kwargs["loop"] = "asyncio"

    uvicorn.run(
        "pytincture.backend.app:app",
        **run_kwargs,
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
    modules_folder = os.fspath(modules_folder)
    set_modules_path(modules_folder)
    
    # Add BFF configuration to environment variables
    os.environ["BFF_DOCS_PATH"] = bff_docs_path.lstrip('/')  # Remove leading slash if present
    os.environ["BFF_DOCS_TITLE"] = bff_docs_title
        
    for akey, value in env_vars.items():
        if akey == "MODULES_PATH":
            continue
        os.environ[akey] = value

    main_application = Process(target=main, args=(port, ssl_keyfile, ssl_certfile, modules_folder))
    # launch data and main applications
    main_application.start()
    
    def terminate_all(*args):
        main_application.terminate()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # wait for main application death
    main_application.join()
