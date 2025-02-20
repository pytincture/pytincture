import os
import io
import signal
import zipfile
import json
import pytest
from multiprocessing import freeze_support
from fastapi.testclient import TestClient

# Import the launcher functions from pytincture/__init__.py.
from pytincture import main, launch_service

# --------------------------
# Test for the main() function
# --------------------------
def test_main(monkeypatch):
    calls = []

    def fake_run(app_str, host, port, log_level, access_log, reload, ssl_keyfile, ssl_certfile):
        calls.append({
            "app_str": app_str,
            "host": host,
            "port": port,
            "log_level": log_level,
            "access_log": access_log,
            "reload": reload,
            "ssl_keyfile": ssl_keyfile,
            "ssl_certfile": ssl_certfile,
        })

    # Patch uvicorn.run in the launcher module.
    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(launcher_mod.uvicorn, "run", fake_run)

    test_port = 9000
    test_ssl_keyfile = "key.pem"
    test_ssl_certfile = "cert.pem"

    main(test_port, test_ssl_keyfile, test_ssl_certfile)

    assert len(calls) == 1
    call = calls[0]
    assert call["app_str"] == "pytincture.backend.app:app"
    assert call["host"] == "0.0.0.0"
    assert call["port"] == test_port
    assert call["log_level"] == "debug"
    assert call["access_log"] == "access.log"
    assert call["reload"] is False
    assert call["ssl_keyfile"] == test_ssl_keyfile
    assert call["ssl_certfile"] == test_ssl_certfile

# --------------------------
# Test for the launch_service() function
# --------------------------
def test_launch_service(monkeypatch, tmp_path):
    # Create a FakeProcess class that records calls.
    process_calls = []

    class FakeProcess:
        def __init__(self, target, args):
            self.target = target
            self.args = args
            self.started = False
            self.joined = False
        def start(self):
            self.started = True
            process_calls.append("start")
        def terminate(self):
            process_calls.append("terminate")
        def join(self):
            self.joined = True
            process_calls.append("join")

    # Patch Process in the launcher module.
    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(launcher_mod, "Process", FakeProcess)

    # Create a dummy folder using tmp_path so that the directory exists.
    dummy_folder = tmp_path / "dummy_folder"
    dummy_folder.mkdir()
    test_folder = str(dummy_folder)
    test_port = 8080
    env_vars = {"TEST_VAR": "value"}

    # Before calling launch_service, clear the environment variables if set.
    os.environ.pop("MODULES_PATH", None)
    os.environ.pop("TEST_VAR", None)

    # Call launch_service.
    from pytincture.__init__ import launch_service
    launch_service(modules_folder=test_folder, port=test_port, env_vars=env_vars)

    # Verify that environment variables were set.
    assert os.environ["MODULES_PATH"] == test_folder
    assert os.environ["TEST_VAR"] == "value"

    # Check that our FakeProcess methods were called.
    assert "start" in process_calls
    assert "join" in process_calls