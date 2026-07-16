import os
import base64
import io
import json
import textwrap
import zipfile
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from urllib.parse import parse_qs, urlencode, urlsplit

# Import the app instance and helpers from the module.
from pytincture.backend.app import (
    app,
    ALLOWED_NOAUTH_CLASSCALLS,
    _build_streamable_mcp_app,
    _build_dynamic_module_name,
    _sanitize_return_to,
    set_bff_policy_hook,
)
from fastapi import HTTPException


def _decode_session_cookie(client, secret_key):
    cookie_value = client.cookies.get("session")
    assert cookie_value
    unsigned = TimestampSigner(secret_key).unsign(cookie_value)
    return json.loads(base64.b64decode(unsigned))


def _build_expired_session_cookie(session_data, secret_key, max_age):
    class ExpiredTimestampSigner(TimestampSigner):
        def get_timestamp(self):
            return super().get_timestamp() - max_age - 1

    encoded = base64.b64encode(json.dumps(session_data).encode("utf-8"))
    return ExpiredTimestampSigner(secret_key).sign(encoded).decode("utf-8")


def _tamper_token(token):
    index = len(token) // 2
    replacement = "A" if token[index] != "A" else "B"
    return f"{token[:index]}{replacement}{token[index + 1:]}"


@pytest.fixture(autouse=True)
def override_env(monkeypatch):
    """
    Override environment variables and module-level globals.
    Since app.py reads env vars at import time, update its globals in the module.
    """
    monkeypatch.setenv("MODULES_PATH", "/tmp")
    monkeypatch.setenv("USE_REDIS_INSTANCE", "false")
    monkeypatch.setenv("ALLOWED_NOAUTH_CLASSCALLS", json.dumps([]))
    monkeypatch.delenv("PYTINCTURE_DEFAULT_APPLICATION", raising=False)

    # Import the module and override its globals.
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
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


def test_root_redirects_to_configured_default_application(fresh_client, monkeypatch):
    response = fresh_client.get("/", follow_redirects=False)
    assert response.status_code == 404

    monkeypatch.setenv("PYTINCTURE_DEFAULT_APPLICATION", "demoapp")
    response = fresh_client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/demoapp"


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


def test_main_route_ignores_backend_session_snapshot(fresh_client, monkeypatch):
    """
    Stateless browser sessions must not depend on an email-keyed backend snapshot.
    """
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})

    response = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "stale@example.com", "password": "old-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    backend_app.USER_SESSION_DICT["stale@example.com"] = {
        "email": "stale@example.com",
        "password": "new-password",
        "picture": "demoapp/appcode/profile.png",
    }

    response = fresh_client.get("/demoapp", follow_redirects=False)
    assert response.status_code == 200

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


def test_build_streamable_mcp_app_prefers_http_app():
    class DummyMCP:
        def streamable_http_app(self, path=None):
            return {"transport": "streamable_http_app", "path": path}

        def http_app(self, path=None, transport=None):
            return {"transport": transport, "path": path}

    result = _build_streamable_mcp_app(DummyMCP(), path="/")

    assert result == {"transport": "streamable-http", "path": "/"}


def test_build_streamable_mcp_app_falls_back_to_http_app():
    class DummyMCP:
        def http_app(self, path=None, transport=None):
            return {"transport": transport, "path": path}

    result = _build_streamable_mcp_app(DummyMCP(), path="/")

    assert result == {"transport": "streamable-http", "path": "/"}

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


def test_class_call_policy_hook_receives_mapping_for_noauth(monkeypatch, fresh_client, tmp_path):
    """
    No-auth calls should still provide a mapping-shaped user object to policy hooks.
    """
    modules_dir = tmp_path / "policy_noauth_modules"
    modules_dir.mkdir()
    module_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class PublicRestricted:
            @bff_policy(role="admin")
            def inspect(self):
                return {"ok": True}
    """)
    (modules_dir / "public_restricted.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.extend([{
        "file": "public_restricted.py",
        "class": "PublicRestricted",
        "function": "inspect",
    }])

    seen_user = {}

    def policy_hook(user, policy, **kwargs):
        seen_user.update(user)
        roles = set(user.get("roles", []))
        required_role = policy.get("role")
        if required_role and required_role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")

    set_bff_policy_hook(policy_hook)
    try:
        response = fresh_client.post("/classcall/public_restricted.py/PublicRestricted/inspect", json={"kwargs": {}})
        assert response.status_code == 403
        assert seen_user["auth_type"] == "noauth"
        assert seen_user["is_authenticated"] is False
    finally:
        set_bff_policy_hook(None)


def test_class_call_loads_decorated_module_without_standard_import(monkeypatch, fresh_client, tmp_path):
    """
    Decorated backend classes should load correctly on the first direct classcall import.
    """
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "direct_load_modules"
    modules_dir.mkdir()
    module_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class DirectLoad:
            def ping(self):
                return {"status": "ok"}
    """)
    (modules_dir / "direct_load.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.get("/classcall/direct_load.py/DirectLoad/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_class_call_decorated_constructor_receives_user(monkeypatch, fresh_client, tmp_path):
    """
    Decorated classes that define __init__(_user) should receive the resolved user.
    """
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "constructor_modules"
    modules_dir.mkdir()
    module_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class UserAware:
            def __init__(self, _user):
                self._user = _user

            def whoami(self):
                return {"email": self._user["email"]}
    """)
    (modules_dir / "user_aware.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.get("/classcall/user_aware.py/UserAware/whoami")
    assert response.status_code == 200
    assert response.json()["email"] == "tester@example.com"


def test_dynamic_module_names_are_unique_for_distinct_paths(tmp_path):
    """
    Manually loaded modules should not share sys.modules keys when file paths differ.
    """
    first_path = tmp_path / "pkg_a" / "worker.py"
    second_path = tmp_path / "pkg_b" / "worker.py"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text("class Worker:\n    pass\n")
    second_path.write_text("class Worker:\n    pass\n")

    first_name = _build_dynamic_module_name(str(first_path), "Worker")
    second_name = _build_dynamic_module_name(str(second_path), "Worker")

    assert first_name != second_name


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

def test_frontend_runtime_cache_busts_packaged_app_fetch(fresh_client):
    """
    The packaged app fetch should include a per-launch uuid query parameter.
    """
    response = fresh_client.get("/frontend/pytincture.js")
    assert response.status_code == 200
    assert 'appcode/appcode.pyt?uuid=${encodeURIComponent(makeRequestId())}' in response.text

def test_service_worker_skips_cache_for_uuid_busted_appcode(fresh_client):
    """
    Cache-busted app packages should bypass the service-worker cache.
    """
    response = fresh_client.get("/frontend/sw.js")
    assert response.status_code == 200
    assert 'url.pathname.endsWith("/appcode/appcode.pyt")' in response.text
    assert 'url.searchParams.has("uuid")' in response.text

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


def test_require_auth_does_not_print_debug_output(monkeypatch, capsys):
    """Successful session validation should not write authentication details to stdout."""
    import pytincture.backend.app as backend_app

    user = backend_app._build_auth_session_user({"email": "quiet@example.com"})
    request = type("Request", (), {"session": {"user": user}})()

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    assert backend_app.require_auth(request) == user
    assert capsys.readouterr().out == ""


def test_user_login_stores_only_compact_stateless_claims(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {"sentinel": {"value": True}})

    response = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "do-not-store"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    user = session_data["user"]
    assert user["session_version"] == backend_app.AUTH_SESSION_SCHEMA_VERSION
    assert user["email"] == "person@example.com"
    assert user["auth_type"] == "user"
    assert user["roles"] == []
    assert user["is_authenticated"] is True
    assert "password" not in user
    assert backend_app.USER_SESSION_DICT == {"sentinel": {"value": True}}


def test_stateless_session_survives_logout_in_another_browser_and_replica(
    fresh_client,
    monkeypatch,
    dummy_module,
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))

    first_login = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "first"},
        follow_redirects=False,
    )
    assert first_login.status_code == 303

    with TestClient(app) as second_browser, TestClient(app) as another_replica:
        second_login = second_browser.post(
            "/demoapp/auth/user",
            data={"email": "person@example.com", "password": "second"},
            follow_redirects=False,
        )
        assert second_login.status_code == 303
        second_cookie = second_browser.cookies.get("session")
        assert second_cookie

        backend_app.USER_SESSION_DICT["person@example.com"] = {
            "email": "person@example.com",
            "stale": True,
        }
        fresh_client.get("/demoapp/auth/logout", follow_redirects=False)

        another_replica.cookies.set("session", second_cookie)
        response = another_replica.post(
            "/classcall/example.py/ExampleClass/testfunc",
            json={"kwargs": {"source": "replica"}},
        )

    assert response.status_code == 200
    assert response.json()["result"] == "success"


def test_tampered_and_expired_stateless_sessions_are_rejected(
    fresh_client,
    monkeypatch,
    dummy_module,
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))

    fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "secret"},
        follow_redirects=False,
    )
    valid_cookie = fresh_client.cookies.get("session")
    assert valid_cookie

    fresh_client.cookies.clear()
    fresh_client.cookies.set("session", _tamper_token(valid_cookie))
    tampered_response = fresh_client.get(
        "/classcall/example.py/ExampleClass/testfunc"
    )
    assert tampered_response.status_code == 401

    user = backend_app._build_auth_session_user(
        {"email": "person@example.com", "auth_type": "user"}
    )
    expired_cookie = _build_expired_session_cookie(
        {"user": user},
        backend_app.SAML_SECRET_KEY,
        backend_app.AUTH_SESSION_MAX_AGE_SECONDS,
    )
    fresh_client.cookies.clear()
    fresh_client.cookies.set("session", expired_cookie)
    expired_response = fresh_client.get(
        "/classcall/example.py/ExampleClass/testfunc"
    )
    assert expired_response.status_code == 401


def test_saml_relay_state_is_signed_and_expires(monkeypatch):
    import pytincture.backend.app as backend_app

    payload = {
        "version": 1,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": "ONELOGIN_request",
        "return_to": "/demoapp",
    }
    token = backend_app._sign_saml_relay_state(payload)
    assert backend_app._load_saml_relay_state(token) == payload

    with pytest.raises(HTTPException) as invalid_error:
        backend_app._load_saml_relay_state(_tamper_token(token))
    assert invalid_error.value.status_code == 400
    assert invalid_error.value.detail == "Invalid SAML RelayState"

    monkeypatch.setattr(backend_app, "SAML_RELAY_STATE_TTL_SECONDS", -1)
    with pytest.raises(HTTPException) as expired_error:
        backend_app._load_saml_relay_state(token)
    assert expired_error.value.status_code == 400
    assert expired_error.value.detail == "SAML RelayState has expired"


def test_saml_login_embeds_replica_safe_relay_state(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    class FakeSettings:
        def get_security_data(self):
            return {"authnRequestsSigned": False}

    class FakeSamlAuth:
        request_id = "ONELOGIN_replica_safe_request"

        def login(self, return_to=None):
            return "https://idp.example.com/saml?" + urlencode({
                "SAMLRequest": "request-data",
                "RelayState": return_to,
            })

        def get_last_request_id(self):
            return self.request_id

        def get_settings(self):
            return FakeSettings()

        def redirect_to(self, url, parameters):
            return f"{url}?{urlencode(parameters)}"

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    monkeypatch.setattr(
        backend_app,
        "_init_saml_auth",
        lambda request, application, provider=None, post_data=None: FakeSamlAuth(),
    )

    response = fresh_client.get(
        "/demoapp/auth/saml/login?return_to=/demoapp/work",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    relay_state = backend_app._load_saml_relay_state(query["RelayState"][0])
    assert relay_state == {
        "version": 1,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": FakeSamlAuth.request_id,
        "return_to": "/demoapp/work",
    }


def test_saml_relay_state_replacement_resigns_authn_request():
    import pytincture.backend.app as backend_app

    class FakeSettings:
        def get_security_data(self):
            return {
                "authnRequestsSigned": True,
                "signatureAlgorithm": "rsa-sha256",
            }

    class FakeSamlAuth:
        signed_parameters = None

        def get_settings(self):
            return FakeSettings()

        def add_request_signature(self, parameters, algorithm):
            self.signed_parameters = dict(parameters)
            parameters["Signature"] = "new-signature"
            parameters["SigAlg"] = algorithm

        def redirect_to(self, url, parameters):
            return f"{url}?{urlencode(parameters)}"

    saml_auth = FakeSamlAuth()
    auth_url = "https://idp.example.com/saml?" + urlencode({
        "SAMLRequest": "request-data",
        "RelayState": "placeholder",
        "Signature": "old-signature",
        "SigAlg": "old-algorithm",
    })

    replaced_url = backend_app._replace_saml_relay_state(
        saml_auth,
        auth_url,
        "signed-relay-state",
    )
    query = parse_qs(urlsplit(replaced_url).query)

    assert saml_auth.signed_parameters == {
        "SAMLRequest": "request-data",
        "RelayState": "signed-relay-state",
    }
    assert query["RelayState"] == ["signed-relay-state"]
    assert query["Signature"] == ["new-signature"]
    assert query["SigAlg"] == ["rsa-sha256"]


def test_saml_acs_creates_compact_session_that_authorizes_bff_calls(
    fresh_client,
    monkeypatch,
    dummy_module,
):
    import pytincture.backend.app as backend_app

    class FakeSettings:
        def get_sp_data(self):
            return {
                "entityId": "https://service.example.com/metadata",
                "assertionConsumerService": {"url": "https://service.example.com/acs"},
            }

        def get_idp_data(self):
            return {
                "entityId": "https://idp.example.com/metadata",
                "singleSignOnService": {"url": "https://idp.example.com/sso"},
                "x509cert": "",
            }

    class FakeSamlAuth:
        processed_request_id = None

        def get_settings(self):
            return FakeSettings()

        def process_response(self, request_id=None):
            self.processed_request_id = request_id

        def get_errors(self):
            return []

        def get_last_error_reason(self):
            return None

        def is_authenticated(self):
            return True

        def get_nameid(self):
            return "person@example.com"

        def get_session_index(self):
            return "changing-session-index"

        def get_attributes(self):
            return {
                "email": ["person@example.com"],
                "givenName": ["Person"],
                "roles": ["Admin", "Analyst"],
                "large-claim": ["must-not-enter-the-cookie"],
            }

    fake_saml_auth = FakeSamlAuth()
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    monkeypatch.setattr(backend_app, "SAML_EMAIL_ATTRIBUTE", "email")
    monkeypatch.setattr(backend_app, "SAML_NAME_ATTRIBUTE", "givenName")
    monkeypatch.setattr(backend_app, "SAML_ALLOWED_ROLES", [])
    monkeypatch.setattr(backend_app, "SAML_ROLE_ATTRIBUTE_KEYS", ["roles"])
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    monkeypatch.setattr(
        backend_app,
        "_init_saml_auth",
        lambda request, application, provider=None, post_data=None: fake_saml_auth,
    )

    relay_state = backend_app._sign_saml_relay_state({
        "version": 1,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": "ONELOGIN_original_request",
        "return_to": "/demoapp",
    })
    response = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(b"<Response/>").decode("ascii"),
            "RelayState": relay_state,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert fake_saml_auth.processed_request_id == "ONELOGIN_original_request"
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    user = session_data["user"]
    assert user["email"] == "person@example.com"
    assert user["name"] == "Person"
    assert user["roles"] == ["admin", "analyst"]
    assert user["auth_provider"] == "default"
    assert user["saml"]["name_id"] == "person@example.com"
    assert "attributes" not in user["saml"]
    assert "session_index" not in user["saml"]
    assert "saml_session_index" not in session_data

    backend_app.USER_SESSION_DICT["person@example.com"] = {"stale": True}
    bff_response = fresh_client.get(
        "/classcall/example.py/ExampleClass/testfunc"
    )
    assert bff_response.status_code == 200


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


def test_login_endpoint_includes_microsoft_button_when_enabled(fresh_client, monkeypatch, tmp_path):
    """
    Ensure the login page surfaces the Microsoft option when it is enabled.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", True)

    response = fresh_client.get("/demoapp/login")
    assert response.status_code == 200
    assert "Login with Microsoft" in response.text
    assert 'href="auth/microsoft"' in response.text


def test_microsoft_login_stores_only_compact_stateless_claims(
    fresh_client,
    monkeypatch,
):
    import pytincture.backend.app as backend_app

    class FakeMicrosoftOAuth:
        async def authorize_access_token(self, request):
            return {
                "access_token": "must-not-enter-the-cookie",
                "userinfo": {
                    "email": "person@example.com",
                    "name": "Example Person",
                    "picture": "https://example.com/profile.png",
                    "tenant": "must-not-enter-the-cookie",
                },
            }

    fake_oauth = type("FakeOAuth", (), {"microsoft": FakeMicrosoftOAuth()})()
    monkeypatch.setattr(backend_app, "oauth", fake_oauth)
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {"sentinel": True})
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")

    response = fresh_client.get(
        "/demoapp/auth/microsoft/callback",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    user = session_data["user"]
    assert user == {
        "session_version": backend_app.AUTH_SESSION_SCHEMA_VERSION,
        "email": "person@example.com",
        "name": "Example Person",
        "picture": "https://example.com/profile.png",
        "auth_type": "microsoft",
        "roles": [],
        "is_authenticated": True,
        "auth_provider": "microsoft",
        "auth_provider_label": "Microsoft",
    }
    assert "access_token" not in session_data
    assert backend_app.USER_SESSION_DICT == {"sentinel": True}


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


def test_main_app_route_includes_per_app_favicon(fresh_client, monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text(
        "<html><head>***FAVICON_LINK***</head>"
        "<body>***APPLICATION*** ***ENTRYPOINT*** ***LOADING_TITLE*** "
        "***WIDGETSET***</body></html>"
    )

    dummy_modules = tmp_path / "modules"
    dummy_modules.mkdir()
    (dummy_modules / "demoapp.py").write_text(
        'APP_CONFIG = {"favicon": "assets/demo icon.svg"}\n'
    )

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "get_modules_path", lambda: str(dummy_modules))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.get("/demoapp")

    assert response.status_code == 200
    assert (
        '<link rel="icon" href="/demoapp/appcode/assets/demo%20icon.svg">'
        in response.text
    )


def test_find_app_favicon_rejects_unsafe_asset_paths(tmp_path):
    import pytincture.backend.app as backend_app

    app_file = tmp_path / "unsafe.py"
    app_file.write_text('APP_FAVICON = "../outside.ico"\n')

    assert backend_app.find_app_favicon(app_file) is None


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
    Ensure the login page surfaces the SAML option when multiple auth methods exist.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)

    response = fresh_client.get("/demoapp/login")
    assert response.status_code == 200
    assert "Login with Google" in response.text
    assert "Login with SAML" in response.text


def test_login_endpoint_uses_single_saml_label_and_logo(fresh_client, monkeypatch, tmp_path):
    """
    Single-provider SAML deployments can customize the visible login button.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    monkeypatch.setattr(backend_app, "SAML_LOGIN_LABEL", "Login with Contoso")
    monkeypatch.setattr(backend_app, "SAML_LOGO_URL", "/logos/contoso.svg")

    response = fresh_client.get("/demoapp/login")
    assert response.status_code == 200
    assert "Login with Contoso" in response.text
    assert 'src="/logos/contoso.svg"' in response.text
    assert 'href="auth/saml/login"' in response.text


def test_login_endpoint_lists_multiple_saml_providers(fresh_client, monkeypatch, tmp_path):
    """
    Multiple SAML providers should render as separate choices with labels and logos.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    providers = [
        {"id": "company-a", "label": "Login with Company A", "logo_url": "/logos/a.svg"},
        {"id": "company-b", "label": "Login with Company B", "logo_url": "/logos/b.svg"},
    ]

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", providers)

    response = fresh_client.get("/demoapp/login", follow_redirects=False)
    assert response.status_code == 200
    assert "Login with Company A" in response.text
    assert "Login with Company B" in response.text
    assert 'href="auth/saml/company-a/login"' in response.text
    assert 'href="auth/saml/company-b/login"' in response.text
    assert 'src="/logos/a.svg"' in response.text
    assert 'src="/logos/b.svg"' in response.text


def test_login_endpoint_redirects_directly_to_saml_when_only_option(fresh_client, monkeypatch, tmp_path):
    """
    When SAML is the only configured login method, skip the chooser page.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.delenv("ENABLE_GOOGLE_AUTH", raising=False)
    monkeypatch.delenv("ENABLE_USER_LOGIN", raising=False)
    monkeypatch.delenv("ENABLE_SAML_AUTH", raising=False)
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)

    response = fresh_client.get("/demoapp/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers.get("location") == "/demoapp/auth/saml/login"


def test_login_endpoint_does_not_redirect_when_multiple_saml_only(fresh_client, monkeypatch, tmp_path):
    """
    If SAML has multiple providers, the chooser must remain visible even when it is the only auth type.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", [
        {"id": "company-a", "label": "Company A"},
        {"id": "company-b", "label": "Company B"},
    ])

    response = fresh_client.get("/demoapp/login", follow_redirects=False)
    assert response.status_code == 200
    assert "Company A" in response.text
    assert "Company B" in response.text


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


def test_saml_provider_metadata_route_uses_provider_config(fresh_client, monkeypatch, tmp_path):
    """
    Provider metadata should use the selected provider's IdP config with shared SP URLs by default.
    """
    import pytincture.backend.app as backend_app

    dummy_frontend = tmp_path / "frontend"
    dummy_frontend.mkdir()
    (dummy_frontend / "index.html").write_text("<html>***APPLICATION***</html>")

    dummy_modules = tmp_path / "modules"
    dummy_modules.mkdir()
    monkeypatch.setenv("MODULES_PATH", str(dummy_modules))
    monkeypatch.setattr(backend_app, "STATIC_PATH", str(dummy_frontend))

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

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", [{
        "id": "company-a",
        "label": "Company A",
        "idp_entity_id": "https://idp-a.example.com/metadata",
        "idp_sso_url": "https://idp-a.example.com/sso",
        "idp_x509_cert": dummy_cert,
    }])

    response = fresh_client.get("/demoapp/auth/saml/company-a/metadata")
    assert response.status_code == 200
    assert "EntityDescriptor" in response.text
    assert "http://testserver/demoapp/auth/saml/metadata" in response.text
    assert "http://testserver/demoapp/auth/saml/acs" in response.text
