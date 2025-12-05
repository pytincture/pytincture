import os
import io
import json
import textwrap
import zipfile
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Import the app instance and helpers from the module.
from pytincture.backend.app import app, ALLOWED_NOAUTH_CLASSCALLS, _sanitize_return_to, set_bff_policy_hook
from fastapi import HTTPException

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
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
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
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
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
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
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
        "file": "Example.PY",
        "class": "ExampleClass",
        "function": "testfunc"
    }]
    ALLOWED_NOAUTH_CLASSCALLS.extend(allowed_calls)
    fresh_client.cookies.clear()
    response = fresh_client.get("/classcall/example.py/ExampleClass/testfunc")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response.get("result") == "success"


def test_class_call_policy_hook(monkeypatch, fresh_client, tmp_path):
    """
    Custom policy hooks can inspect metadata and user context before allowing a call.
    """
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "policy_modules"
    modules_dir.mkdir()
    module_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class Restricted:
            @bff_policy(role="admin")
            def secret(self):
                return {"ok": True}
    """)
    (modules_dir / "restricted.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))

    current_user = {"email": "user@example.com", "roles": []}

    def fake_require_auth(request):
        return current_user

    monkeypatch.setattr(backend_app, "require_auth", fake_require_auth)

    def policy_hook(user, policy, **kwargs):
        required_role = policy.get("role")
        roles = set(user.get("roles", []))
        if required_role and required_role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")

    set_bff_policy_hook(policy_hook)
    try:
        response = fresh_client.post("/classcall/restricted.py/Restricted/secret", json={"kwargs": {}})
        assert response.status_code == 403

        current_user["roles"] = ["admin"]
        response = fresh_client.post("/classcall/restricted.py/Restricted/secret", json={"kwargs": {}})
        assert response.status_code == 200
        assert response.json()["ok"] is True
    finally:
        set_bff_policy_hook(None)


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


def test_class_call_nested_module_path(monkeypatch, fresh_client, tmp_path):
    """
    Files inside nested directories should be resolvable via /classcall/{folder/...}.
    """
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "nested_modules"
    target_dir = modules_dir / "pkg" / "internal"
    target_dir.mkdir(parents=True)
    module_code = textwrap.dedent("""
        class Worker:
            def __init__(self, _user):
                self._user = _user

            def ping(self, value):
                return {"echo": value}
    """)
    (target_dir / "worker.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.post(
        "/classcall/pkg/internal/worker.py/Worker/ping",
        json={"kwargs": {"value": "hello"}}
    )
    assert response.status_code == 200
    assert response.json()["echo"] == "hello"


def test_class_call_noauth_nested_path(monkeypatch, fresh_client, tmp_path):
    """
    No-auth allowances should work with nested file paths irrespective of case.
    """
    modules_dir = tmp_path / "nested_noauth"
    target_dir = modules_dir / "pkg" / "internal"
    target_dir.mkdir(parents=True)
    module_code = textwrap.dedent("""
        class Worker:
            def __init__(self, _user):
                self._user = _user

            def ping(self):
                return {"status": "ok"}
    """)
    (target_dir / "worker.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.extend([{
        "file": "PKG/Internal/worker.py",
        "class": "Worker",
        "function": "ping"
    }])

    response = fresh_client.get("/classcall/pkg/internal/worker.py/Worker/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_class_call_streaming(monkeypatch, fresh_client, tmp_path):
    """
    Streaming-enabled methods should return a streaming response.
    """
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "stream_modules"
    modules_dir.mkdir()
    module_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class StreamWidget:
            @bff_stream()
            async def ticker(self, count):
                for idx in range(count):
                    yield {"value": idx}
    """)
    (modules_dir / "stream_widget.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {"tester@example.com": {"email": "tester@example.com"}})
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.post(
        "/classcall/stream_widget.py/StreamWidget/ticker",
        json={"kwargs": {"count": 3}}
    )

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")
    chunks = list(response.iter_text())
    combined = "".join(chunks)
    assert '"value": 0' in combined
    assert '"value": 1' in combined

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


def test_sanitize_return_to_allows_relative_paths():
    """
    Relative, same-origin paths should be preserved.
    """
    assert _sanitize_return_to("/demoapp") == "/demoapp"
    assert _sanitize_return_to("/demoapp?next=home") == "/demoapp?next=home"


def test_sanitize_return_to_rejects_external_urls():
    """
    Absolute or protocol-relative URLs must be rejected to avoid open redirects.
    """
    assert _sanitize_return_to("https://evil.com") is None
    assert _sanitize_return_to("//evil.com") is None
    assert _sanitize_return_to("   http://evil.com/path  ") is None
    assert _sanitize_return_to("login") is None


def test_login_endpoint_includes_saml_button_when_enabled(fresh_client, monkeypatch, tmp_path):
    """
    Ensure the login page surfaces the SAML option when enabled.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)

    response = fresh_client.get("/demoapp/login")
    assert response.status_code == 200
    assert "Login with SAML" in response.text


def test_saml_metadata_route_returns_metadata(fresh_client, monkeypatch, tmp_path):
    """
    Verify that the SAML metadata endpoint returns valid XML when configured.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    dummy_modules = tmp_path / "modules"
    dummy_modules.mkdir()
    monkeypatch.setenv("MODULES_PATH", str(dummy_modules))
    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_SP_ENTITY_ID", "https://example.com/{application}/auth/saml/metadata")
    monkeypatch.setattr(backend_app, "SAML_IDP_ENTITY_ID", "https://idp.example.com/metadata")
    monkeypatch.setattr(backend_app, "SAML_IDP_SSO_URL", "https://idp.example.com/sso")
    dummy_cert = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIBszCCAVmgAwIBAgIUO3VsbHlDZXJ0Q29kZXgxEzARBgNVBAMMCkxvY2FsIElE\n"
        "UDEPMA0GA1UECgwGQXBwQ28wHhcNMjQwMTAxMDAwMDAwWhcNMzQwMTAxMDAwMDAw\n"
        "WjATMREwDwYDVQQDDAhVbml0VGVzdDCBnzANBgkqhkiG9w0BAQEFAAOBjQAwgYkC\n"
        "gYEAy1atZ0mFsrl5FTvhYGfEpDj6rVdlHPff0T3hj5VYiC7P+60F2/diFr9GY29s\n"
        "F1tXsEBuFQzL85zzBdNxcQvTxlyvq9Y6lBJ8K8w9Y4mGe/7y6QSyp4i0b36W3YLv\n"
        "oH4p64a1PgVno6Pwx1yk3B9uJJl63/tVspEP1JuxlTCbeu0CAwEAATANBgkqhkiG\n"
        "9w0BAQsFAAOBgQBSAdwLY7z9mVJgE+B76MpxGg7Trz4Y32faVYblaRHmbZt3FvX6\n"
        "6R0tPLfrE38AyFQBtcyqH68v9d5dTU8l2zl4OPcnBHdUMf56XI5clJ8zJqVU6M/p\n"
        "jdJp4bYaXMtOmvw5FXX0HP7h+G5aD3JBt+1w0FSf1V/Iv9ldnYNoG9/HYg==\n"
        "-----END CERTIFICATE-----"
    )
    monkeypatch.setattr(backend_app, "SAML_IDP_X509_CERT", dummy_cert)

    response = fresh_client.get("/demoapp/auth/saml/metadata")
    assert response.status_code == 200
    assert "EntityDescriptor" in response.text
