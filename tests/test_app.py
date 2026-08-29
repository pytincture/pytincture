import os
import base64
import io
import json
import textwrap
import zipfile
import tempfile
import asyncio
import subprocess
import sys
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
    set_user_authenticator,
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


def _csrf_headers(client):
    token = client.cookies.get("pytincture_csrf")
    assert token
    return {"X-CSRF-Token": token}


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
    monkeypatch.delenv("AUTH_USER_CLAIMS", raising=False)
    monkeypatch.delenv("AUTH_SESSION_CLAIM_KEYS", raising=False)
    monkeypatch.delenv("DEFAULT_APP_USERS", raising=False)

    # Import the module and override its globals.
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_BFF_REPLAY_TOKENS", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})
    monkeypatch.setattr(backend_app, "AUTH_SESSION_REVOCATIONS", {})
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_STORE", {})
    set_user_authenticator(None)
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    yield
    set_user_authenticator(None)

@pytest.fixture
def fresh_client(override_env):
    """
    Provide a fresh TestClient instance with cleared cookies.
    """
    client = TestClient(
        app,
        base_url="https://testserver",
        client=("127.0.0.1", 50000),
    )
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
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class ExampleClass:
            __widgetset__ = "dummywidget"
            __version__ = "1.0"
            def __init__(self, _user):
                self._user = _user
            def testfunc(self, *args, **kwargs):
                return {"result": "success", "args": args, "kwargs": kwargs}
    """))
    return dummy_file.parent  # Return the directory containing example.py


def test_user_login_requires_enabled_flag(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    response = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "anything"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_password_hash_verifier_rejects_wrong_password(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app
    from argon2 import PasswordHasher

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv(
        "AUTH_PASSWORD_HASHES",
        json.dumps({"person@example.com": PasswordHasher().hash("correct-password")}),
    )

    wrong = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert wrong.status_code == 401

    correct = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "correct-password"},
        follow_redirects=False,
    )
    assert correct.status_code == 303


def test_password_login_hydrates_safe_default_app_user_claims(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app
    from argon2 import PasswordHasher

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "admin@arul.ai")
    monkeypatch.setenv(
        "AUTH_PASSWORD_HASHES",
        json.dumps({"admin@arul.ai": PasswordHasher().hash("admin")}),
    )
    monkeypatch.setenv(
        "DEFAULT_APP_USERS",
        json.dumps([{
            "id": "1",
            "name": "Admin",
            "email": "ADMIN@ARUL.AI",
            "role": "Viewer",
            "password": "must-not-return",
            "access_token": "must-not-return",
            "plan": "Enterprise",
            "next_billing": "Dec 15, 2023",
            "theme": "Dark",
            "sidebar": "Open",
        }]),
    )

    response = fresh_client.post(
        "/demoapp/auth/mcp",
        json={"email": "admin@arul.ai", "password": "admin"},
    )

    assert response.status_code == 200
    returned_user = response.json()
    assert returned_user["status"] == "authenticated"
    assert returned_user["id"] == "1"
    assert returned_user["name"] == "Admin"
    assert returned_user["role"] == "Viewer"
    assert returned_user["roles"] == ["viewer"]
    assert returned_user["plan"] == "Enterprise"
    assert returned_user["next_billing"] == "Dec 15, 2023"
    assert returned_user["theme"] == "Dark"
    assert returned_user["sidebar"] == "Open"
    assert "password" not in returned_user
    assert "access_token" not in returned_user

    session_user = _decode_session_cookie(
        fresh_client,
        backend_app.SAML_SECRET_KEY,
    )["user"]
    assert session_user["id"] == "1"
    assert session_user["role"] == "Viewer"
    assert "password" not in session_user
    assert "access_token" not in session_user


def test_development_email_login_rejects_remote_peer_spoofing_loopback_host(monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    with TestClient(
        app,
        base_url="https://localhost",
        headers={"X-Forwarded-Host": "localhost"},
        client=("198.51.100.24", 50000),
    ) as client:
        response = client.post(
            "/demoapp/auth/user",
            data={"email": "person@example.com", "password": "ignored"},
            follow_redirects=False,
        )
    assert response.status_code == 401


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
def test_development_email_login_accepts_actual_loopback_peer(monkeypatch, peer):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    with TestClient(
        app,
        base_url="https://public.example.com",
        client=(peer, 50000),
    ) as client:
        response = client.post(
            "/demoapp/auth/user",
            data={"email": "person@example.com", "password": "ignored"},
            follow_redirects=False,
        )
    assert response.status_code == 303


def test_authentication_enabled_requires_strong_startup_secret(tmp_path):
    environment = os.environ.copy()
    environment.update({
        "ENABLE_USER_LOGIN": "true",
        "ENABLE_GOOGLE_AUTH": "false",
        "ENABLE_MICROSOFT_AUTH": "false",
        "ENABLE_SAML_AUTH": "false",
        "PYTHONPATH": str(Path(__file__).parents[1]),
    })
    environment.pop("SAML_SECRET_KEY", None)
    environment.pop("SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Authentication requires SAML_SECRET_KEY" in result.stderr


def test_dependency_routes_reject_missing_authenticated_session(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    assert fresh_client.post("/logs", json={}).status_code == 401
    assert fresh_client.get("/demoapp/appcode/appcode.pyt").status_code == 401


def test_unknown_bff_target_is_rejected_before_module_execution(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    marker = tmp_path / "executed"
    (tmp_path / "danger.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "class Danger:\n"
        "    def run(self): return True\n"
    )
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "user@example.com"})
    response = fresh_client.post("/classcall/danger.py/Danger/run", json={})
    assert response.status_code == 404
    assert not marker.exists()


def test_async_policy_runs_before_constructor(fresh_client, monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    marker = tmp_path / "constructed"
    (tmp_path / "restricted.py").write_text(textwrap.dedent(f"""
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class Restricted:
            def __init__(self):
                Path({str(marker)!r}).write_text("constructed")

            @bff_policy(role="admin")
            def run(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"roles": []})

    async def deny_policy(**kwargs):
        await asyncio.sleep(0)
        raise HTTPException(status_code=403, detail="Forbidden")

    set_bff_policy_hook(deny_policy)
    try:
        response = fresh_client.post("/classcall/restricted.py/Restricted/run", json={})
    finally:
        set_bff_policy_hook(None)
    assert response.status_code == 403
    assert not marker.exists()


def test_state_changing_bff_call_requires_csrf(
    fresh_client, monkeypatch, dummy_module
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "local"},
        follow_redirects=False,
    )
    without_token = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc", json={}
    )
    assert without_token.status_code == 403
    with_token = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={},
        headers=_csrf_headers(fresh_client),
    )
    assert with_token.status_code == 200


def test_bff_replay_token_is_opaque_session_bound_and_single_use(
    fresh_client, monkeypatch, dummy_module
):
    import ast
    import re
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_BFF_REPLAY_TOKENS", True)
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_BATCH_SIZE", 4)
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_LOW_WATERMARK", 1)
    monkeypatch.setenv("BFF_REPLAY_TOKEN_LOW_WATERMARK", "1")
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    backend_app.reload_bff_registry(str(dummy_module))

    login = fresh_client.post(
        "/example/auth/user",
        data={"email": "person@example.com", "password": "local"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    package = fresh_client.get("/example/appcode/appcode.pyt")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        stub = archive.read("example.py").decode("utf-8")
    capsule_match = re.search(r"_pytincture_replay_capsule = (.+)", stub)
    key_match = re.search(r"_pytincture_replay_key = (.+)", stub)
    assert capsule_match and key_match
    capsule = ast.literal_eval(capsule_match.group(1))
    client_key = bytes(ast.literal_eval(key_match.group(1)))

    state_headers = {
        **_csrf_headers(fresh_client),
        "X-Pytincture-Client": capsule,
    }
    state_response = fresh_client.post("/_pytincture/state", headers=state_headers)
    assert state_response.status_code == 200
    assert state_response.headers["content-type"].startswith("application/octet-stream")
    decoded = json.loads(
        backend_app._decrypt_opaque_envelope(client_key, state_response.text)
    )
    assert decoded["v"] == 1
    assert len(decoded["items"]) == 4
    assert all(token not in state_response.text for token in decoded["items"])

    # The capsule is stateless: a stable backend secret can recover the client
    # key and issue a fresh opaque pool after process-local token state is lost.
    backend_app.BFF_REPLAY_TOKEN_STORE.clear()
    after_restart = fresh_client.post("/_pytincture/state", headers=state_headers)
    assert after_restart.status_code == 200

    call_headers = {
        **_csrf_headers(fresh_client),
    }
    # Use a token from the post-restart refill because pre-restart tokens were
    # deliberately invalidated with process-local storage.
    restarted_tokens = json.loads(
        backend_app._decrypt_opaque_envelope(client_key, after_restart.text)
    )["items"]
    call_headers["X-Pytincture-BFF-Token"] = restarted_tokens[0]
    first = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={},
        headers=call_headers,
    )
    copied_curl_replay = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={},
        headers=call_headers,
    )
    assert first.status_code == 200
    assert copied_curl_replay.status_code == 409


def test_bff_methods_default_to_post(fresh_client, monkeypatch, dummy_module):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "user@example.com"})
    response = fresh_client.get("/classcall/example.py/ExampleClass/testfunc")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_revoked_session_is_rejected(fresh_client, monkeypatch, dummy_module):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "local"},
        follow_redirects=False,
    )
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    backend_app.revoke_session(session_data["session_id"])
    response = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={},
        headers=_csrf_headers(fresh_client),
    )
    assert response.status_code == 401


def test_session_key_rotation_accepts_and_resigns_previous_key():
    from fastapi import FastAPI, Request
    from pytincture.backend.app import RotatingSessionMiddleware

    old_key = "old-key-with-at-least-thirty-two-random-chars"
    new_key = "new-key-with-at-least-thirty-two-random-chars"
    mini_app = FastAPI()

    @mini_app.get("/")
    async def read_session(request: Request):
        return request.session

    mini_app.add_middleware(
        RotatingSessionMiddleware,
        secret_key=new_key,
        previous_secret_keys=[old_key],
        https_only=True,
    )
    encoded = base64.b64encode(json.dumps({"user": "legacy"}).encode())
    old_cookie = TimestampSigner(old_key).sign(encoded).decode()
    with TestClient(mini_app, base_url="https://testserver") as client:
        client.cookies.set("session", old_cookie)
        response = client.get("/")
        assert response.json() == {"user": "legacy"}
        resigned = response.headers["set-cookie"].split("session=", 1)[1].split(";", 1)[0]
    decoded = TimestampSigner(new_key).unsign(resigned)
    assert json.loads(base64.b64decode(decoded)) == {"user": "legacy"}


def test_raw_server_files_are_not_public_assets(fresh_client, monkeypatch, tmp_path):
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "server.py").write_text("SECRET = 'hidden'\n")
    (tmp_path / ".env").write_text("SECRET=hidden\n")
    (tmp_path / "logo.png").write_bytes(b"png")

    assert fresh_client.get("/demoapp/appcode/server.py").status_code == 404
    assert fresh_client.get("/demoapp/appcode/.env").status_code == 404
    assert fresh_client.get("/demoapp/appcode/logo.png").status_code == 200


def test_detected_widget_wheel_is_public_but_unrelated_wheels_are_not(
    fresh_client, monkeypatch, tmp_path
):
    import sys

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.delenv("PYTINCTURE_PUBLIC_ASSET_PATHS", raising=False)
    (tmp_path / "demoapp.py").write_text("import demo_widgets\n")
    matching_version = "demo_widgets-0.1.0-py3-none-any.whl"
    matching_dev = "demo_widgets-99.99.99-py3-none-any.whl"
    unrelated = "server_helpers-1.0.0-py3-none-any.whl"
    (tmp_path / matching_version).write_bytes(b"versioned-wheel")
    (tmp_path / matching_dev).write_bytes(b"development-wheel")
    (tmp_path / unrelated).write_bytes(b"server-only-wheel")
    nested = tmp_path / "private"
    nested.mkdir()
    (nested / matching_version).write_bytes(b"nested-wheel")
    widget_module = type("DemoWidgets", (), {
        "__widgetset__": "demo-widgets",
        "__version__": "0.1.0",
    })
    monkeypatch.setitem(sys.modules, "demo_widgets", widget_module)

    assert fresh_client.get(f"/demoapp/appcode/{matching_version}").status_code == 200
    assert fresh_client.get(f"/demoapp/appcode/{matching_dev}").status_code == 200
    assert fresh_client.head(f"/demoapp/appcode/{matching_version}").status_code == 200
    assert fresh_client.head(f"/demoapp/appcode/{matching_dev}").status_code == 200
    assert fresh_client.get(f"/demoapp/appcode/{unrelated}").status_code == 404
    assert fresh_client.head(f"/demoapp/appcode/{unrelated}").status_code == 404
    assert fresh_client.get(f"/demoapp/appcode/private/{matching_version}").status_code == 404


def test_browser_package_excludes_unreachable_server_modules(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("from service import Service\n")
    (tmp_path / "service.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend
        class ServerHelper:
            secret = "helper-must-not-ship"
        @backend_for_frontend
        class Service:
            def secret(self):
                return "must-not-ship"
    """))
    (tmp_path / "server_only.py").write_text("SECRET = 'must-not-ship'\n")

    response = fresh_client.get("/demoapp/appcode/appcode.pyt")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"demoapp.py", "service.py"}
        assert "must-not-ship" not in archive.read("service.py").decode()
        assert "ServerHelper" not in archive.read("service.py").decode()


def test_mcp_has_no_automatic_tools_and_rejects_sensitive_allowlist(monkeypatch):
    import pytincture.backend.app as backend_app

    assert asyncio.run(backend_app.mcp.list_tools()) == []
    monkeypatch.setenv("ENABLE_MCP", "true")
    monkeypatch.setenv("MCP_EXPOSED_OPERATIONS", '["handleUserAuth"]')
    with pytest.raises(RuntimeError, match="session/login/application"):
        backend_app._mcp_operation_ids()


def test_mcp_classcall_cannot_bypass_http_authentication(monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    (tmp_path / "service.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend
        @backend_for_frontend
        class Service:
            def run(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    backend_app.reload_bff_registry(str(tmp_path))
    mcp_server = backend_app.FastMCP.from_fastapi(
        backend_app._FilteredFastAPIApp(app, {"postClassCall"}),
        name="security-test",
    )

    async def invoke_tool():
        tool = await mcp_server.get_tool("postClassCall")
        assert tool is not None
        return await tool.run({
            "file_path": "service.py",
            "class_name": "Service",
            "function_name": "run",
        })

    with pytest.raises(ValueError, match="HTTP error 401"):
        asyncio.run(invoke_tool())


def test_validation_error_does_not_echo_request_body(fresh_client):
    response = fresh_client.post(
        "/demoapp/auth/mcp",
        json={"email": "person@example.com", "secret": "must-not-echo"},
    )
    assert response.status_code == 422
    assert "must-not-echo" not in response.text


def test_request_body_limit_rejects_oversized_payload(fresh_client):
    response = fresh_client.post("/logs", content=b"x" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413

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
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})
    monkeypatch.setenv("ALLOWED_EMAILS", "stale@example.com")

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
    response = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc", json={"kwargs": {}}
    )
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

    response = fresh_client.post("/classcall/direct_load.py/DirectLoad/ping", json={})
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

    response = fresh_client.post("/classcall/user_aware.py/UserAware/whoami", json={})
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
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
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
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
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

    response = fresh_client.post(
        "/classcall/pkg/internal/worker.py/Worker/ping", json={}
    )
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


def test_class_call_timeout_returns_gateway_timeout(monkeypatch, fresh_client, tmp_path):
    import pytincture.backend.app as backend_app

    module_code = textwrap.dedent("""
        import asyncio
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class SlowWidget:
            async def wait(self):
                await asyncio.sleep(0.05)
                return {"status": "late"}
    """)
    (tmp_path / "slow_widget.py").write_text(module_code)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "BFF_CALL_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")

    response = fresh_client.post(
        "/classcall/slow_widget.py/SlowWidget/wait",
        json={},
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["correlation_id"]

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
    (dummy_dir / "demoapp.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Demo:
            def ping(self):
                return "pong"
    """))
    (dummy_dir / "dummy.txt").write_text("dummy content")
    monkeypatch.setenv("MODULES_PATH", str(dummy_dir))
    # Override require_auth to simulate a valid user.
    monkeypatch.setattr(backend_app, "require_auth", lambda req: {"email": "dummy@example.com"})
    application_name = "demoapp"
    hostile_host = "host-with-'quote.example"
    response = fresh_client.get(
        f"/{application_name}/appcode/appcode.pyt",
        headers={
            "Host": hostile_host,
            "X-Forwarded-Host": hostile_host,
            "X-Forwarded-Proto": "protocol-with-'quote",
        },
    )
    assert response.status_code == 200
    # Verify content type and disposition.
    assert response.headers.get("content-type") == "application/zip"
    cd = response.headers.get("content-disposition", "")
    assert "filename=appcode.pyt" in cd
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie, Authorization"
    # Check that the content appears to be a zip archive (starts with PK).
    assert response.content.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        generated_app = archive.read("demoapp.py").decode("utf-8")
    assert hostile_host not in generated_app
    assert "protocol-with-" not in generated_app
    assert "url = '/classcall/demoapp.py/Demo/ping'" in generated_app
    compile(generated_app, "demoapp.py", "exec")

def test_frontend_runtime_cache_busts_packaged_app_fetch(fresh_client):
    """
    The packaged app fetch should include the server instance uuid query parameter.
    """
    response = fresh_client.get("/frontend/pytincture.js")
    assert response.status_code == 200
    assert "installCacheBustingFetch(config.requestUuid)" in response.text
    assert 'withRequestUuid(`${config.application}/appcode/appcode.pyt`, config.requestUuid)' in response.text
    assert "loadScript(`${config.pyodideBaseUrl}pyodide.asm.js`, config.requestUuid)" in response.text
    assert "cache_bust_url(cleaned)" in response.text


def test_frontend_runtime_cache_busts_only_backend_micropip_installs(fresh_client):
    response = fresh_client.get("/frontend/pytincture.js")

    assert response.status_code == 200
    assert "if (cacheBustingSuspensionDepth > 0)" in response.text
    assert "await withoutCacheBusting(() => pyodide.runPythonAsync" in response.text
    assert "withRequestUuid(lib, activeRequestUuid)" not in response.text
    assert "cacheBust ? withRequestUuid(source, activeRequestUuid) : source" in response.text
    assert "await installWidgetsetSource(pyodide, primarySource);" in response.text
    assert "await installWidgetsetSource(pyodide, source, true);" in response.text


def test_frontend_runtime_resolves_versioned_wheels_and_sends_log_csrf(fresh_client):
    response = fresh_client.get("/frontend/pytincture.js")
    assert response.status_code == 200
    assert "candidateVersions.push(pinnedMatch[1])" in response.text
    assert "candidateVersions.push(config.devWheelVersion)" in response.text
    assert response.text.index("candidateVersions.push(pinnedMatch[1])") < response.text.index(
        "candidateVersions.push(config.devWheelVersion)"
    )
    assert response.text.index("await installWidgetsetSource(pyodide, primarySource)") < response.text.index(
        "const backendSources = await resolveBackendWidgetSources(config)"
    )
    assert "is not available from PyPI; checking backend wheels" in response.text
    assert "Failed to install widgetset from ${primarySource}" not in response.text
    assert response.text.index("if (!(await urlExists(source)))") < response.text.index(
        "await installWidgetsetSource(pyodide, source, true)"
    )
    assert "throw lastInstallError" in response.text
    assert 'name === "pytincture_csrf"' in response.text
    assert 'headers["X-CSRF-Token"] = csrfToken' in response.text

def test_service_worker_skips_cache_for_all_uuid_busted_files(fresh_client):
    """
    Every UUID-bearing file should bypass the service-worker cache.
    """
    response = fresh_client.get("/frontend/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert 'url.searchParams.has("uuid")' in response.text
    assert "url.origin === self.location.origin" in response.text
    assert "new Request(withRequestUuid(url), event.request)" in response.text
    assert 'fetch(bustedRequest, { cache: "no-store" })' in response.text


def test_health_and_readiness_endpoints(fresh_client):
    from pytincture import __version__

    health = fresh_client.get("/healthz", headers={"X-Request-ID": "health-check-1"})
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": __version__}
    assert health.headers["X-Request-ID"] == "health-check-1"

    readiness = fresh_client.get("/readyz")
    assert readiness.status_code == 200
    assert readiness.json()["status"] == "ready"
    assert all(readiness.json()["checks"].values())


def test_readiness_fails_when_modules_path_is_unavailable(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(
        backend_app,
        "get_modules_path",
        lambda: str(tmp_path / "missing-modules"),
    )
    response = fresh_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not-ready"
    assert response.json()["checks"]["modules_path"] is False


def test_readiness_fails_when_shared_state_is_unavailable(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    class UnavailableStore:
        def ping(self):
            return False

    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "true")
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", UnavailableStore())
    monkeypatch.setattr(backend_app, "AUTH_SESSION_REVOCATIONS", UnavailableStore())
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_STORE", UnavailableStore())
    response = fresh_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["session_store"] is False

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
    request = type("Request", (), {
        "session": {"user": user, "session_id": "test-session"}
    })()

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    assert backend_app.require_auth(request) == user
    assert capsys.readouterr().out == ""


def test_user_login_stores_only_compact_stateless_claims(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
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
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))

    first_login = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "first"},
        follow_redirects=False,
    )
    assert first_login.status_code == 303

    with TestClient(
        app, base_url="https://testserver", client=("127.0.0.1", 50001)
    ) as second_browser, TestClient(
        app, base_url="https://testserver", client=("127.0.0.1", 50002)
    ) as another_replica:
        second_login = second_browser.post(
            "/demoapp/auth/user",
            data={"email": "person@example.com", "password": "second"},
            follow_redirects=False,
        )
        assert second_login.status_code == 303
        second_cookie = second_browser.cookies.get("session")
        second_csrf_cookie = second_browser.cookies.get("pytincture_csrf")
        assert second_cookie
        assert second_csrf_cookie

        backend_app.USER_SESSION_DICT["person@example.com"] = {
            "email": "person@example.com",
            "stale": True,
        }
        fresh_client.get("/demoapp/auth/logout", follow_redirects=False)

        another_replica.cookies.set("session", second_cookie)
        another_replica.cookies.set("pytincture_csrf", second_csrf_cookie)
        response = another_replica.post(
            "/classcall/example.py/ExampleClass/testfunc",
            json={"kwargs": {"source": "replica"}},
            headers=_csrf_headers(another_replica),
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
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
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
        "version": 2,
        "transaction_id": "opaque-transaction-id",
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
    assert relay_state["version"] == 2
    transaction = backend_app._get_saml_handshake_cookie_serializer().loads(
        fresh_client.cookies[backend_app._SAML_HANDSHAKE_COOKIE],
        max_age=backend_app.SAML_RELAY_STATE_TTL_SECONDS,
    )
    assert transaction == {
        "version": 1,
        "transaction_id": relay_state["transaction_id"],
        "application": "demoapp",
        "provider_id": "default",
        "request_id": FakeSamlAuth.request_id,
        "return_to": "/demoapp/work",
    }
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=none" in set_cookie


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

        def get_last_response_in_response_to(self):
            return "ONELOGIN_original_request"

        def get_last_message_id(self):
            return "ONELOGIN_response"

        def get_last_assertion_id(self):
            return "ONELOGIN_assertion"

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

    transaction_id = "opaque-transaction"
    transaction = {
        "version": 1,
        "transaction_id": transaction_id,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": "ONELOGIN_original_request",
        "return_to": "/demoapp",
    }
    handshake_cookie = (
        backend_app._get_saml_handshake_cookie_serializer().dumps(transaction)
    )
    fresh_client.cookies.set(
        backend_app._SAML_HANDSHAKE_COOKIE,
        handshake_cookie,
        path="/demoapp/auth/saml",
    )
    relay_state = backend_app._sign_saml_relay_state({
        "version": 2,
        "transaction_id": transaction_id,
    })
    callback_data = {
        "SAMLResponse": base64.b64encode(b"<Response/>").decode("ascii"),
        "RelayState": relay_state,
    }
    response = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data=callback_data,
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
    assert len(session_data["saml_replay_proof"]) == 64
    assert "ONELOGIN_response" not in fresh_client.cookies["session"]
    assert "ONELOGIN_assertion" not in fresh_client.cookies["session"]
    assert any(
        backend_app._SAML_HANDSHAKE_COOKIE in value and "Max-Age=0" in value
        for value in response.headers.get_list("set-cookie")
    )
    # Even a raw client that restores the consumed handshake cookie is rejected
    # by the replay proof carried in the signed browser session.
    fresh_client.cookies.set(
        backend_app._SAML_HANDSHAKE_COOKIE,
        handshake_cookie,
        path="/demoapp/auth/saml",
    )

    replay = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data=callback_data,
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert replay.json() == {"detail": "Invalid or replayed SAML login"}

    backend_app.USER_SESSION_DICT["person@example.com"] = {"stale": True}
    bff_response = fresh_client.post(
        "/classcall/example.py/ExampleClass/testfunc",
        json={},
        headers=_csrf_headers(fresh_client),
    )
    assert bff_response.status_code == 200


def test_saml_acs_rejects_relay_state_copied_to_another_browser(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    transaction_id = "victim-transaction"
    relay_state = backend_app._sign_saml_relay_state(
        {"version": 2, "transaction_id": transaction_id}
    )
    # A second browser may have its own valid handshake cookie, but it must not
    # be able to submit the victim browser's copied RelayState.
    fresh_client.cookies.set(
        backend_app._SAML_HANDSHAKE_COOKIE,
        backend_app._get_saml_handshake_cookie_serializer().dumps(
            {
                "version": 1,
                "transaction_id": "attacker-transaction",
                "application": "demoapp",
                "provider_id": "default",
                "request_id": "attacker-request",
                "return_to": "/demoapp",
            }
        ),
        path="/demoapp/auth/saml",
    )
    response = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(b"<Response/>").decode("ascii"),
            "RelayState": relay_state,
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or expired SAML login"}


def test_saml_acs_requires_exact_in_response_to(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    class FakeSamlAuth:
        def process_response(self, request_id=None):
            assert request_id == "request-one"

        def get_errors(self):
            return []

        def is_authenticated(self):
            return True

        def get_last_response_in_response_to(self):
            return "different-request"

        def get_last_message_id(self):
            return "response-one"

        def get_last_assertion_id(self):
            return "assertion-one"

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    monkeypatch.setattr(
        backend_app,
        "_init_saml_auth",
        lambda request, application, provider=None, post_data=None: FakeSamlAuth(),
    )
    transaction_id = "correlation-transaction"
    record = {
        "version": 1,
        "transaction_id": transaction_id,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": "request-one",
        "return_to": "/demoapp",
    }
    fresh_client.cookies.set(
        backend_app._SAML_HANDSHAKE_COOKIE,
        backend_app._get_saml_handshake_cookie_serializer().dumps(record),
        path="/demoapp/auth/saml",
    )
    relay_state = backend_app._sign_saml_relay_state(
        {"version": 2, "transaction_id": transaction_id}
    )
    response = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(b"<Response/>").decode("ascii"),
            "RelayState": relay_state,
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid SAML response correlation"}


def test_saml_acs_rejects_disallowed_transforms_before_toolkit_and_rate_limits(
    fresh_client,
    monkeypatch,
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(backend_app, "SAML_PROVIDERS", "")
    monkeypatch.setattr(
        backend_app,
        "SAML_ACS_RATE_LIMITER",
        SlidingWindowRateLimiter(1, 60),
    )
    toolkit_called = False

    def fail_if_toolkit_is_called(*args, **kwargs):
        nonlocal toolkit_called
        toolkit_called = True
        raise AssertionError("unsafe XML reached the SAML toolkit")

    monkeypatch.setattr(backend_app, "_init_saml_auth", fail_if_toolkit_is_called)
    relay_state = backend_app._sign_saml_relay_state({
        "version": 1,
        "application": "demoapp",
        "provider_id": "default",
        "request_id": "ONELOGIN_original_request",
        "return_to": "/demoapp",
    })
    guarded_response = (
        Path(__file__).parent / "fixtures" / "saml" / "disallowed-xslt-transform.xml"
    ).read_bytes()

    rejected = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(guarded_response).decode("ascii"),
            "RelayState": relay_state,
        },
    )
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "Invalid SAML response"}
    assert toolkit_called is False

    throttled = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(b"<Response/>").decode("ascii"),
            "RelayState": relay_state,
        },
    )
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"] == "60"
    assert toolkit_called is False


def test_login_endpoint(fresh_client, monkeypatch, tmp_path):
    """
    Test the /{application}/login endpoint returns expected HTML content.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
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


def test_login_endpoint_renders_escaped_help_text(fresh_client, monkeypatch):
    """Optional login guidance is visible without allowing HTML injection."""
    monkeypatch.setenv(
        "LOGIN_HELP_TEXT",
        "Demo: user@example.com / demo-password <script>alert('x')</script>",
    )

    response = fresh_client.get("/demoapp/login")

    assert response.status_code == 200
    assert 'class="login-help-text"' in response.text
    assert "Demo: user@example.com / demo-password" in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;/script&gt;" in response.text
    assert "<script>alert('x')</script>" not in response.text


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
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
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


def test_main_app_frontend_files_share_one_instance_uuid(fresh_client, monkeypatch, tmp_path):
    import re
    import pytincture.backend.app as backend_app

    (tmp_path / "demoapp.py").write_text("# cache-busted frontend\n")
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    first_response = fresh_client.get("/demoapp")
    second_response = fresh_client.get("/demoapp")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    uuid_values = re.findall(
        r'(?:[?&]uuid=|requestUuid:\s*")([a-f0-9]{32})',
        first_response.text + second_response.text,
    )
    assert len(uuid_values) >= 12
    assert set(uuid_values) == {backend_app.FRONTEND_INSTANCE_UUID}
    assert "***REQUEST_UUID***" not in first_response.text
    assert "***REQUEST_UUID***" not in second_response.text
    assert first_response.headers["cache-control"] == "no-store, max-age=0"
    assert first_response.headers["pragma"] == "no-cache"


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
    assert '<link rel="icon" href="/demoapp/appcode/assets/demo%20icon.svg?uuid=' in response.text
    assert 'type="image/svg+xml" sizes="any">' in response.text


def test_favicon_folder_declares_available_browser_assets(tmp_path):
    import pytincture.backend.app as backend_app

    app_file = tmp_path / "demoapp.py"
    app_file.write_text("# favicon folder uses convention-based discovery\n")
    favicon_folder = tmp_path / "favicon"
    favicon_folder.mkdir()
    for filename in (
        "android-chrome-192x192.png",
        "apple-touch-icon.png",
        "favicon-16x16.ico",
        "favicon-32x32.png",
        "favicon.ico",
        "safari-pinned-tab.svg",
        "site.webmanifest",
    ):
        (favicon_folder / filename).write_bytes(b"icon")
    (favicon_folder / "notes.txt").write_text("not a browser favicon asset")

    markup = backend_app.build_app_favicon_markup("demoapp", app_file)

    assert 'href="/demoapp/appcode/favicon/favicon.ico"' in markup
    assert 'href="/demoapp/appcode/favicon/favicon-16x16.ico"' in markup
    assert 'sizes="16x16"' in markup
    assert 'href="/demoapp/appcode/favicon/favicon-32x32.png"' in markup
    assert 'sizes="32x32"' in markup
    assert 'rel="apple-touch-icon"' in markup
    assert 'rel="mask-icon"' in markup
    assert 'rel="manifest"' in markup
    assert "notes.txt" not in markup


def test_favicon_folder_prefers_application_specific_directory(tmp_path):
    import pytincture.backend.app as backend_app

    app_file = tmp_path / "demoapp.py"
    app_file.write_text("# app-specific favicon folder\n")
    (tmp_path / "favicon").mkdir()
    app_favicon_folder = tmp_path / "favicon" / "demoapp"
    app_favicon_folder.mkdir()
    (app_favicon_folder / "favicon.ico").write_bytes(b"icon")

    assert backend_app.find_app_favicon(app_file) == "favicon/demoapp"


def test_launcher_favicon_folder_supports_external_path(monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    modules_folder = tmp_path / "modules"
    modules_folder.mkdir()
    app_file = modules_folder / "demoapp.py"
    app_file.write_text("# use launcher favicon folder\n")

    favicon_folder = tmp_path / "shared-branding"
    favicon_folder.mkdir()
    (favicon_folder / "favicon-32x32.png").write_bytes(b"configured-icon")
    monkeypatch.setenv("PYTINCTURE_FAVICON_FOLDER", str(favicon_folder))

    markup = backend_app.build_app_favicon_markup("demoapp", app_file)

    assert 'href="/demoapp/favicon-assets/favicon-32x32.png"' in markup
    assert 'sizes="32x32"' in markup


def test_launcher_favicon_folder_serves_per_app_assets(fresh_client, monkeypatch, tmp_path):
    favicon_root = tmp_path / "favicons"
    app_favicon_folder = favicon_root / "demoapp"
    app_favicon_folder.mkdir(parents=True)
    (favicon_root / "favicon.ico").write_bytes(b"shared-icon")
    (app_favicon_folder / "favicon.ico").write_bytes(b"demo-icon")
    monkeypatch.setenv("PYTINCTURE_FAVICON_FOLDER", str(favicon_root))

    response = fresh_client.get("/demoapp/favicon-assets/favicon.ico")

    assert response.status_code == 200
    assert response.content == b"demo-icon"


def test_app_favicon_setting_overrides_launcher_folder(monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    app_file = tmp_path / "demoapp.py"
    app_file.write_text('APP_FAVICON = "branding/app-icon.svg"\n')
    favicon_folder = tmp_path / "launcher-favicons"
    favicon_folder.mkdir()
    (favicon_folder / "favicon.ico").write_bytes(b"launcher-icon")
    monkeypatch.setenv("PYTINCTURE_FAVICON_FOLDER", str(favicon_folder))

    markup = backend_app.build_app_favicon_markup("demoapp", app_file)

    assert 'href="/demoapp/appcode/branding/app-icon.svg"' in markup
    assert "favicon-assets" not in markup


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


def test_saml_requested_authn_context_is_disabled_by_default():
    import pytincture.backend.app as backend_app
    from fastapi import Request
    from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    provider = {
        "idp_entity_id": "https://idp.example.com/metadata",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_x509_cert": "dummy-certificate",
    }

    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("service.example.com", 443),
            "path": "/demoapp/auth/saml/login",
            "query_string": b"",
            "headers": [(b"host", b"service.example.com")],
        }
    )
    settings = backend_app._build_saml_settings(request, "demoapp", provider=provider)
    saml_settings = OneLogin_Saml2_Settings(settings=settings)
    request_xml = OneLogin_Saml2_Authn_Request(saml_settings).get_xml()

    assert settings["security"]["requestedAuthnContext"] is False
    assert "RequestedAuthnContext" not in request_xml


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

    response = fresh_client.get(
        "/demoapp/auth/saml/company-a/metadata",
        headers={"host": "service.example.com"},
    )
    assert response.status_code == 200
    assert "EntityDescriptor" in response.text
    assert "https://service.example.com/demoapp/auth/saml/metadata" in response.text
    assert "https://service.example.com/demoapp/auth/saml/acs" in response.text
