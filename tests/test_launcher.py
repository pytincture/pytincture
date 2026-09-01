import os
import io
import signal
import zipfile
import json
import logging
import pytest
from multiprocessing import freeze_support
from fastapi.testclient import TestClient

# Import the launcher functions from pytincture/__init__.py.
from pytincture import main, launch_service, get_modules_path, set_modules_path

# --------------------------
# Test for the main() function
# --------------------------
def test_main(monkeypatch):
    calls = []
    set_modules_path(None)

    def fake_run(app_str, **kwargs):
        call = {"app_str": app_str}
        call.update(kwargs)
        calls.append(call)

    # Patch uvicorn.run in the launcher module.
    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(launcher_mod.uvicorn, "run", fake_run)

    test_port = 9000
    test_ssl_keyfile = "key.pem"
    test_ssl_certfile = "cert.pem"

    main(test_port, test_ssl_keyfile, test_ssl_certfile)

    assert len(calls) == 1
    call = calls[0]
    assert call["app_str"].title == "pyTincture API"
    assert call["host"] == "0.0.0.0"
    assert call["port"] == test_port
    assert call["log_level"] == "info"
    assert call["access_log"] is False
    assert "log_config" not in call
    assert call["reload"] is False
    assert call["ssl_keyfile"] == test_ssl_keyfile
    assert call["ssl_certfile"] == test_ssl_certfile
    loop_value = call.get("loop", "asyncio")
    assert loop_value in {"asyncio", "uvloop"}
    set_modules_path(None)
    os.environ.pop("MODULES_PATH", None)


def test_optional_uvicorn_access_log_strips_query_strings(monkeypatch):
    calls = []
    monkeypatch.setenv("PYTINCTURE_UVICORN_ACCESS_LOG", "true")

    import pytincture.__init__ as launcher_mod

    monkeypatch.setattr(
        launcher_mod.uvicorn,
        "run",
        lambda app_str, **kwargs: calls.append({"app_str": app_str, **kwargs}),
    )
    main(9000)

    assert calls[0]["access_log"] is True
    assert "pytincture_path_only" in calls[0]["log_config"]["filters"]
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1",
            "GET",
            "/demo/auth/callback?code=secret&state=secret",
            "1.1",
            302,
        ),
        None,
    )
    assert launcher_mod._PathOnlyAccessFilter().filter(record) is True
    assert record.args[2] == "/demo/auth/callback"
    assert "secret" not in record.getMessage()


def test_main_defaults_development_login_to_loopback(monkeypatch):
    calls = []
    monkeypatch.setenv("ENABLE_USER_LOGIN", "true")
    monkeypatch.setenv("ENABLE_DEV_EMAIL_LOGIN", "true")

    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(
        launcher_mod.uvicorn,
        "run",
        lambda app_str, **kwargs: calls.append({"app_str": app_str, **kwargs}),
    )

    main(9000)

    assert calls[0]["host"] == "127.0.0.1"


def test_main_defaults_dynamic_auth_origin_development_to_loopback(monkeypatch):
    calls = []
    monkeypatch.setenv("ENABLE_USER_LOGIN", "true")
    monkeypatch.setenv("PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN", "true")
    monkeypatch.setenv("AUTH_SESSION_HTTPS_ONLY", "false")
    monkeypatch.setenv("SAML_SECRET_KEY", "0123456789abcdef" * 2)

    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(
        launcher_mod.uvicorn,
        "run",
        lambda app_str, **kwargs: calls.append({"app_str": app_str, **kwargs}),
    )

    main(9000)

    assert calls[0]["host"] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "public.example.com"])
def test_main_rejects_routable_dynamic_auth_origin_bind(monkeypatch, host):
    monkeypatch.setenv("ENABLE_USER_LOGIN", "true")
    monkeypatch.setenv("PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN", "true")

    with pytest.raises(RuntimeError, match="literal loopback bind host"):
        main(9000, host=host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "public.example.com"])
def test_main_rejects_routable_development_login_bind(monkeypatch, host):
    monkeypatch.setenv("ENABLE_USER_LOGIN", "true")
    monkeypatch.setenv("ENABLE_DEV_EMAIL_LOGIN", "true")

    with pytest.raises(RuntimeError, match="literal loopback bind host"):
        main(9000, host=host)

# --------------------------
# Test for the launch_service() function
# --------------------------
def test_launch_service(monkeypatch, tmp_path):
    # Create a FakeProcess class that records calls.
    process_calls = []
    set_modules_path(None)

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
    favicon_folder = dummy_folder / "branding" / "favicon"
    favicon_folder.mkdir(parents=True)
    test_folder = str(dummy_folder)
    test_port = 8080
    env_vars = {"TEST_VAR": "value"}

    # Before calling launch_service, clear the environment variables if set.
    os.environ.pop("MODULES_PATH", None)
    os.environ.pop("TEST_VAR", None)
    os.environ.pop("PYTINCTURE_DEFAULT_APPLICATION", None)
    os.environ.pop("PYTINCTURE_FAVICON_FOLDER", None)
    os.environ.pop("ENABLE_USER_LOGIN", None)
    os.environ.pop("ENABLE_DEV_EMAIL_LOGIN", None)

    # Call launch_service.
    from pytincture.__init__ import launch_service
    launch_service(
        modules_folder=test_folder,
        port=test_port,
        env_vars=env_vars,
        default_application="demoapp",
        favicon_folder="branding/favicon",
    )

    # Verify that the module path was stored and env vars applied.
    assert get_modules_path() == test_folder
    assert os.environ["MODULES_PATH"] == test_folder
    assert os.environ["TEST_VAR"] == "value"
    assert os.environ["PYTINCTURE_DEFAULT_APPLICATION"] == "demoapp"
    assert os.environ["PYTINCTURE_FAVICON_FOLDER"] == str(favicon_folder)

    # Check that our FakeProcess methods were called.
    assert "start" in process_calls
    assert "join" in process_calls
    set_modules_path(None)
    os.environ.pop("MODULES_PATH", None)
    os.environ.pop("TEST_VAR", None)
    os.environ.pop("PYTINCTURE_DEFAULT_APPLICATION", None)
    os.environ.pop("PYTINCTURE_FAVICON_FOLDER", None)


def test_launch_service_uses_safe_implicit_bind_for_development_login(
    monkeypatch, tmp_path
):
    process_args = []

    class FakeProcess:
        def __init__(self, target, args):
            process_args.append(args)

        def start(self):
            pass

        def terminate(self):
            pass

        def join(self):
            pass

    import pytincture as launcher_mod
    monkeypatch.setattr(launcher_mod, "Process", FakeProcess)

    try:
        launcher_mod.launch_service(
            modules_folder=str(tmp_path),
            env_vars={
                "ENABLE_USER_LOGIN": "true",
                "ENABLE_DEV_EMAIL_LOGIN": "true",
            },
        )
    finally:
        os.environ.pop("ENABLE_USER_LOGIN", None)
        os.environ.pop("ENABLE_DEV_EMAIL_LOGIN", None)

    assert process_args[0][4] == "127.0.0.1"


def test_launch_service_rejects_explicit_routable_development_bind(
    monkeypatch, tmp_path
):
    import pytincture as launcher_mod

    monkeypatch.setattr(
        launcher_mod,
        "Process",
        lambda *args, **kwargs: pytest.fail("process must not be created"),
    )

    try:
        with pytest.raises(RuntimeError, match="literal loopback bind host"):
            launcher_mod.launch_service(
                modules_folder=str(tmp_path),
                host="0.0.0.0",
                env_vars={
                    "ENABLE_USER_LOGIN": "true",
                    "ENABLE_DEV_EMAIL_LOGIN": "true",
                },
            )
    finally:
        os.environ.pop("ENABLE_USER_LOGIN", None)
        os.environ.pop("ENABLE_DEV_EMAIL_LOGIN", None)


def test_launch_service_validates_typed_configuration_before_process(monkeypatch, tmp_path):
    import pytincture as launcher_mod

    monkeypatch.setattr(
        launcher_mod,
        "Process",
        lambda *args, **kwargs: pytest.fail("process must not be created"),
    )
    try:
        with pytest.raises(ValueError, match="absolute session lifetime"):
            launcher_mod.launch_service(
                modules_folder=str(tmp_path),
                env_vars={
                    "AUTH_SESSION_MAX_AGE_SECONDS": "120",
                    "AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS": "60",
                },
            )
    finally:
        os.environ.pop("AUTH_SESSION_MAX_AGE_SECONDS", None)
        os.environ.pop("AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS", None)


def test_launch_service_ignores_env_var_override(monkeypatch, tmp_path):
    process_calls = []
    set_modules_path(None)

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

    import pytincture.__init__ as launcher_mod
    monkeypatch.setattr(launcher_mod, "Process", FakeProcess)

    modules_folder = tmp_path / "modules"
    modules_folder.mkdir()

    env_vars = {
        "MODULES_PATH": "should_be_ignored",
        "TEST_VAR": "value",
    }

    os.environ.pop("MODULES_PATH", None)
    os.environ.pop("TEST_VAR", None)

    from pytincture.__init__ import launch_service
    launch_service(modules_folder=str(modules_folder), port=8070, env_vars=env_vars)

    assert get_modules_path() == str(modules_folder)
    assert os.environ["MODULES_PATH"] == str(modules_folder)
    assert os.environ["TEST_VAR"] == "value"
    assert "start" in process_calls
    assert "join" in process_calls

    set_modules_path(None)
    os.environ.pop("MODULES_PATH", None)
    os.environ.pop("TEST_VAR", None)
