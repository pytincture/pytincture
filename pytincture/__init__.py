"""
BriskJS uvicorn launcher
"""
import atexit
from multiprocessing import Process, Manager
import os
import signal
import subprocess

import uvicorn


def main(port, mdict, ssl_keyfile=None, ssl_certfile=None):
    uvicorn.run(
        "backend.app:app",
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

def server_run(modules_folder=".", port=8090, ssl_keyfile=None, ssl_certfile=None):
    manager = Manager()
    mdict = manager.dict()

    os.environ["MODULES_PATH"] = os.path.join(os.path.dirname(__file__), modules_folder)
        
    main_application = Process(target=main, args=(port, mdict, ssl_keyfile, ssl_certfile,))
    # launch data and main applications
    main_application.start()
    
    def terminate_all(*args):
        main_application.terminate()

    signal.signal(signal.SIGINT, terminate_all)
    signal.signal(signal.SIGTERM, terminate_all)

    # wait for main application death
    main_application.join()

if __name__ == "__main__":
    server_run(modules_folder="./modules")
