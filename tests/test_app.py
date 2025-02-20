import os
import io
import json
import textwrap
import zipfile
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Import the app instance and ALLOWED_NOAUTH_CLASSCALLS from the module.
from pytincture.backend.app import app, ALLOWED_NOAUTH_CLASSCALLS

@pytest.fixture(autouse=True)
def override_env(monkeypatch):
    """
    Override environment variables and module-level globals.
    Since app.py reads env vars at import time, update its globals in the module.
    """
    monkeypatch.setenv("MODULES_PATH", "/tmp")
    monkeypatch.setenv("USE_REDIS_INSTANCE", "false")
    monkeypatch.setenv("ALLOWED_NOAUTH_CLASSCALLS", json.dumps([]))

    # Import the module and override its globals.
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    yield

@pytest.fixture
def fresh_client(override_env):
    """
    Provide a fresh TestClient instance with cleared cookies.
    """
    client = TestClient(app)
    client.cookies.clear()
    return client

@pytest.fixture
def dummy_module(tmp_path: Path):
    """
    Create a dummy module file in a temporary directory for testing the
    /classcall endpoint. The file (example.py) defines a class ExampleClass
    with a function testfunc.
    """
    dummy_file = tmp_path / "example.py"
    dummy_file.write_text(textwrap.dedent("""
        class ExampleClass:
            __widgetset__ = "dummywidget"
            __version__ = "1.0"
            def __init__(self, _user):
                self._user = _user
            def testfunc(self, *args, **kwargs):
                return {"result": "success", "args": args, "kwargs": kwargs}
    """))
    return dummy_file.parent  # Return the directory containing example.py

def test_favicon(fresh_client):
    """Test the /favicon.ico route (placeholder)."""
    response = fresh_client.get("/favicon.ico")
    # With the placeholder, expect 200 or 404.
    assert response.status_code in (200, 404)

def test_main_route_with_auth_enabled_no_user_session(fresh_client, monkeypatch):
    """
    With auth enabled but no valid user session,
    the main route should redirect to /{application}/login.
    """
    # Override globals in the backend module.
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})

    # Override require_auth so that it always returns None (simulating no valid session).
    monkeypatch.setattr(backend_app, "require_auth", lambda request: None)

    application_name = "demoapp"
    fresh_client.cookies.clear()  # Ensure no session is present.
    response = fresh_client.get(f"/{application_name}", follow_redirects=False)
    # With authentication enabled and no session, expect a redirect.
    assert response.status_code in (302, 307), f"Expected redirect, got {response.status_code}"
    assert f"/{application_name}/login" in response.headers.get("location", "")

def test_main_route_no_auth_when_disabled(fresh_client, monkeypatch):
    """
    If both ENABLE_GOOGLE_AUTH and ENABLE_USER_LOGIN are disabled,
    the main route should serve the index page (HTTP 200).
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    application_name = "demoapp"
    response = fresh_client.get(f"/{application_name}")
    assert response.status_code == 200

def test_class_call_noauth(dummy_module, monkeypatch, fresh_client):
    """
    Test the /classcall endpoint when the call is allowed without auth.
    We update MODULES_PATH and ALLOWED_NOAUTH_CLASSCALLS accordingly.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    allowed_calls = [{
        "file": "example.py",
        "class": "ExampleClass",
        "function": "testfunc"
    }]
    ALLOWED_NOAUTH_CLASSCALLS.extend(allowed_calls)
    fresh_client.cookies.clear()
    response = fresh_client.get("/classcall/example.py/ExampleClass/testfunc")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response.get("result") == "success"

def test_class_call_with_auth(dummy_module, monkeypatch, fresh_client):
    """
    Test the /classcall endpoint when auth is required.
    With no valid user session and no allowed no-auth call, expect a 401.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    fresh_client.cookies.clear()
    # Override require_auth so that it always returns None.
    monkeypatch.setattr(backend_app, "require_auth", lambda request: None)
    response = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}}
    )
    assert response.status_code == 401

# ---------------------------------------------------------------------
# Additional Tests for Increased Coverage
# ---------------------------------------------------------------------

def test_download_appcode(fresh_client, monkeypatch, tmp_path):
    """
    Test the /{application}/appcode/appcode.pyt endpoint returns a zip package.
    """
    import pytincture.backend.app as backend_app
    # Create a dummy modules folder with a file.
    dummy_dir = tmp_path / "dummy_modules"
    dummy_dir.mkdir()
    (dummy_dir / "dummy.txt").write_text("dummy content")
    monkeypatch.setenv("MODULES_PATH", str(dummy_dir))
    # Override require_auth to simulate a valid user.
    monkeypatch.setattr(backend_app, "require_auth", lambda req: {"email": "dummy@example.com"})
    application_name = "demoapp"
    response = fresh_client.get(f"/{application_name}/appcode/appcode.pyt")
    assert response.status_code == 200
    # Verify content type and disposition.
    assert response.headers.get("content-type") == "application/zip"
    cd = response.headers.get("content-disposition", "")
    assert "filename=appcode.pyt" in cd
    # Check that the content appears to be a zip archive (starts with PK).
    assert response.content.startswith(b"PK")

def test_get_widgetset(tmp_path, monkeypatch):
    """
    Test get_widgetset returns the correct widgetset string.
    """
    from pytincture.backend.app import get_widgetset
    # Create a dummy static directory with an application file.
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    app_file = static_dir / "testapp.py"
    app_file.write_text("import dummywidget\n")
    # Insert a dummy module into sys.modules.
    import sys
    dummy_module = type("DummyWidget", (), {"__widgetset__": "widget_value", "__version__": "1.0"})
    sys.modules["dummywidget"] = dummy_module
    result = get_widgetset("testapp", str(static_dir))
    assert result == "widget_value==1.0"
    del sys.modules["dummywidget"]

def test_create_pytincture_pkg_in_memory(monkeypatch, tmp_path):
    """
    Test that create_pytincture_pkg_in_memory returns a valid zip archive.
    Adjust assertion to check for file names containing 'pytincture/__init__.py' and 'pytincture/module.py'.
    """
    from pytincture.backend.app import create_pytincture_pkg_in_memory
    # Create a dummy pytincture directory structure.
    dummy_dir = tmp_path / "dummy_pytincture"
    dummy_dir.mkdir()
    (dummy_dir / "__init__.py").write_text("dummy init")
    (dummy_dir / "module.py").write_text("print('dummy module')")
    # Monkey-patch __file__ so that the function uses our dummy structure.
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "__file__", str(tmp_path / "dummy_app.py"))
    # Monkey-patch os.walk to yield our dummy structure.
    monkeypatch.setattr("os.walk", lambda p, topdown=True, onerror=None, followlinks=False: [
        (str(dummy_dir), [], ["__init__.py", "module.py"])
    ])
    in_mem_zip = create_pytincture_pkg_in_memory()
    assert isinstance(in_mem_zip, io.BytesIO)
    with zipfile.ZipFile(in_mem_zip, "r") as zf:
        names = zf.namelist()
        # Check that some file path contains 'pytincture/__init__.py'
        assert any("pytincture/__init__.py" in name for name in names)
        for name in names:
            if "pytincture/__init__.py" in name:
                content = zf.read(name).decode("utf-8")
                assert content == "dummy init"
        # Also check that some file path contains 'pytincture/module.py'
        assert any("pytincture/module.py" in name for name in names)


def test_logs_endpoint(fresh_client, monkeypatch):
    """
    Test the /logs endpoint.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "require_auth", lambda req: {"email": "dummy@example.com"})
    response = fresh_client.post("/logs", json={"log": "test log"})
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"

def test_login_endpoint(fresh_client, monkeypatch, tmp_path):
    """
    Test the /{application}/login endpoint returns expected HTML content.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setenv("ENABLE_GOOGLE_AUTH", "true")
    monkeypatch.setenv("ENABLE_USER_LOGIN", "true")
    # Create a dummy frontend directory with an index.html.
    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION*** ***WIDGETSET***</html>")
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    response = fresh_client.get("/demoapp/login")
    assert response.status_code == 200
    html = response.text
    assert "Please log in to continue" in html
    assert "Login with Google" in html
    assert "type=\"email\"" in html
    assert "type=\"password\"" in html

def test_auth_user_callback(fresh_client, monkeypatch):
    """
    Test the /{application}/auth/user endpoint simulating email/password login.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setenv("ALLOWED_EMAILS", "test@example.com")
    response = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "test@example.com", "password": "secret"},
        follow_redirects=False   # Prevent auto-following the redirect
    )
    # Now the response should be the raw RedirectResponse with status 303.
    assert response.status_code == 303
    assert "/demoapp" in response.headers.get("location", "")

def test_main_app_route_logged_in(fresh_client, monkeypatch, tmp_path):
    """
    Test the main app route when a user is logged in.
    """
    import pytincture.backend.app as backend_app
    # Simulate a valid session.
    monkeypatch.setattr(backend_app, "require_auth", lambda req: {"email": "loggedin@example.com"})
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {"loggedin@example.com": {"email": "loggedin@example.com"}})
    # Create a dummy frontend index.html.
    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION*** ***WIDGETSET***</html>")
    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    # Create a dummy MODULES_PATH and app file for get_widgetset.
    dummy_modules = tmp_path / "modules"
    dummy_modules.mkdir()
    (dummy_modules / "demoapp.py").write_text("import dummywidget\n")
    monkeypatch.setenv("MODULES_PATH", str(dummy_modules))
    import sys
    dummy_mod = type("DummyModule", (), {"__widgetset__": "widgetset_val", "__version__": "3.0"})
    sys.modules["dummywidget"] = dummy_mod
    response = fresh_client.get("/demoapp")
    assert response.status_code == 200
    html = response.text
    # Check that placeholders are replaced.
    assert "***APPLICATION***" not in html
    assert "widgetset_val==3.0" in html
    del sys.modules["dummywidget"]
