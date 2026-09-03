import os
import base64
import hashlib
import io
import json
import re
import textwrap
import zipfile
import tempfile
import asyncio
import subprocess
import sys
import threading
import time
import httpx
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi.responses import RedirectResponse
from itsdangerous import TimestampSigner
from urllib.parse import parse_qs, urlencode, urlsplit
from pytincture import __version__
from pytincture.backend.replay import LocalReplayStore

# Import the app instance and helpers from the module.
from pytincture.backend.app import (
    app,
    ALLOWED_NOAUTH_CLASSCALLS,
    _CSRF_COOKIE,
    _SESSION_COOKIE,
    _build_dynamic_module_name,
    _sanitize_return_to,
    set_bff_policy_hook,
    set_user_authenticator,
)
from fastapi import HTTPException, Request


def _decode_session_cookie(client, secret_key):
    cookie_value = client.cookies.get(_SESSION_COOKIE)
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
    token = client.cookies.get(_CSRF_COOKIE)
    assert token
    return {"X-CSRF-Token": token}


def _login_csrf_token(client, application="demoapp"):
    page = client.get(f"/{application}/login")
    assert page.status_code == 200
    match = re.search(
        r'name="login_csrf_token" value="([^"]+)"', page.text
    )
    assert match
    return match.group(1)


def _password_login(
    client,
    *,
    application="demoapp",
    email="person@example.com",
    password="password",
    **request_kwargs,
):
    data = dict(request_kwargs.pop("data", {}))
    data.update(
        {
            "email": email,
            "password": password,
            "login_csrf_token": _login_csrf_token(client, application),
        }
    )
    return client.post(
        f"/{application}/auth/user",
        data=data,
        **request_kwargs,
    )


def _mcp_password_login(
    client,
    *,
    application="demoapp",
    email="person@example.com",
    password="password",
    **request_kwargs,
):
    initiation = client.get(f"/{application}/auth/mcp")
    assert initiation.status_code == 200
    payload = dict(request_kwargs.pop("json", {}))
    payload.update(
        {
            "email": email,
            "password": password,
            "login_csrf_token": initiation.json()["login_csrf_token"],
        }
    )
    return client.post(
        f"/{application}/auth/mcp",
        json=payload,
        **request_kwargs,
    )


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
    monkeypatch.setattr(backend_app, "APPLICATION_ADMISSION", {})
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {})
    monkeypatch.setattr(backend_app, "AUTH_SESSION_REVOCATIONS", {})
    monkeypatch.setattr(
        backend_app,
        "BFF_REPLAY_TOKEN_STORE",
        LocalReplayStore(10_000, 512),
    )
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
        base_url="https://127.0.0.1",
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
    (tmp_path / "demoapp.py").write_text(
        "from example import ExampleClass\n",
        encoding="utf-8",
    )
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

    wrong = _password_login(
        fresh_client,
        email="person@example.com",
        password="wrong-password",
        follow_redirects=False,
    )
    assert wrong.status_code == 401

    correct = _password_login(
        fresh_client,
        email="person@example.com",
        password="correct-password",
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

    response = _mcp_password_login(
        fresh_client,
        email="admin@arul.ai",
        password="admin",
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
        response = _password_login(
            client,
            email="person@example.com",
            password="ignored",
            follow_redirects=False,
        )
    assert response.status_code == 401


def test_password_login_rejects_oversized_fields(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "AUTH_LOGIN_EMAIL_MAX_CHARS", 8)
    response = _password_login(
        fresh_client,
        email="long-address@example.com",
        password="x",
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("allowed_emails", "email"),
    [
        ("unknown@example.com", "unknown@example.com"),
        ("different@example.com", "blocked@example.com"),
    ],
)
def test_unknown_and_disallowed_logins_share_admission_and_dummy_hash_work(
    fresh_client, monkeypatch, allowed_emails, email
):
    import pytincture.backend.app as backend_app

    events = []

    class RecordingLimiter:
        def allow(self, _key):
            events.append("rate")
            return True, 0

    def recording_dummy_hash(*_args, **_kwargs):
        events.append("hash")
        return False

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", False)
    monkeypatch.setattr(backend_app, "AUTH_LOGIN_RATE_LIMITER", RecordingLimiter())
    monkeypatch.setattr(backend_app, "verify_password", recording_dummy_hash)
    monkeypatch.setenv("ALLOWED_EMAILS", allowed_emails)

    response = _password_login(
        fresh_client,
        email=email,
        password="incorrect",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert events == ["rate", "rate", "hash"]


def test_password_login_rate_limit_recovers_after_window(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    now = [0.0]
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setattr(
        backend_app,
        "AUTH_LOGIN_RATE_LIMITER",
        SlidingWindowRateLimiter(1, 10, clock=lambda: now[0]),
    )

    first = _password_login(
        fresh_client,
        email="person@example.com",
        password="development",
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = _password_login(
        fresh_client,
        email="person@example.com",
        password="development",
        follow_redirects=False,
    )
    assert second.status_code == 429
    assert second.headers["retry-after"] == "10"

    now[0] = 11
    recovered = _password_login(
        fresh_client,
        email="person@example.com",
        password="development",
        follow_redirects=False,
    )
    assert recovered.status_code == 303


def test_password_hash_saturation_rejects_then_recovers(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app
    from pytincture.backend.limits import AsyncAdmissionGate
    from pytincture.backend.saml import SlidingWindowRateLimiter

    gate = AsyncAdmissionGate(1, 0, 0.001)
    asyncio.run(gate.acquire())
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setattr(backend_app, "PASSWORD_HASH_GATE", gate)
    monkeypatch.setattr(
        backend_app,
        "AUTH_LOGIN_RATE_LIMITER",
        SlidingWindowRateLimiter(10, 60),
    )

    saturated = _password_login(
        fresh_client,
        email="person@example.com",
        password="development",
        follow_redirects=False,
    )
    assert saturated.status_code == 503
    gate.release()
    recovered = _password_login(
        fresh_client,
        email="person@example.com",
        password="development",
        follow_redirects=False,
    )
    assert recovered.status_code == 303


@pytest.mark.parametrize(
    ("peer", "base_url"),
    [("127.0.0.1", "https://127.0.0.1")],
)
def test_development_email_login_accepts_literal_loopback_peer_and_host(
    monkeypatch, peer, base_url
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    with TestClient(
        app,
        base_url=base_url,
        client=(peer, 50000),
    ) as client:
        response = _password_login(
            client,
            email="person@example.com",
            password="ignored",
            follow_redirects=False,
        )
    assert response.status_code == 303


def test_development_email_login_accepts_ipv6_literal_loopback_request():
    import pytincture.backend.app as backend_app

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/demoapp/auth/user",
            "raw_path": b"/demoapp/auth/user",
            "query_string": b"",
            "headers": [(b"host", b"[::1]:8070"), (b"origin", b"https://[::1]:8070")],
            "client": ("::1", 50000),
            "server": ("::1", 8070),
        }
    )

    assert backend_app._is_loopback_development_request(request) is True


def test_development_email_login_rejects_public_host_through_loopback_proxy(monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    with TestClient(
        app,
        base_url="https://public.example.com",
        client=("127.0.0.1", 50000),
    ) as client:
        response = _password_login(
            client,
            headers={"Origin": "https://public.example.com"},
            email="person@example.com",
            password="ignored",
            follow_redirects=False,
        )

    assert response.status_code == 401


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
    assert "production authentication requires a strong session_secret" in result.stderr


def test_legacy_backend_requires_fixed_production_auth_origin(tmp_path):
    environment = os.environ.copy()
    environment.update({
        "MODULES_PATH": str(tmp_path),
        "ENABLE_USER_LOGIN": "true",
        "ENABLE_DEV_EMAIL_LOGIN": "false",
        "ENABLE_GOOGLE_AUTH": "false",
        "ENABLE_MICROSOFT_AUTH": "false",
        "ENABLE_SAML_AUTH": "false",
        "PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN": "false",
        "PYTINCTURE_TRUST_PROXY_HEADERS": "false",
        "SAML_SECRET_KEY": "0123456789abcdef" * 2,
        "PYTHONPATH": str(Path(__file__).parents[1]),
    })
    environment.pop("PYTINCTURE_ALLOWED_HOSTS", None)
    environment.pop("PYTINCTURE_CANONICAL_ORIGIN", None)

    missing_hosts = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert missing_hosts.returncode != 0
    assert "production authentication requires exact allowed_hosts" in missing_hosts.stderr

    environment["PYTINCTURE_ALLOWED_HOSTS"] = "service.example"
    environment["PYTINCTURE_CANONICAL_ORIGIN"] = "https://service.example"
    configured = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert configured.returncode == 0, configured.stderr


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
    response = fresh_client.post("/danger/classcall/danger.py/Danger/run", json={"args": [], "kwargs": {}})
    assert response.status_code == 404
    assert not marker.exists()


@pytest.mark.parametrize("async_execution_mode", ["event-loop", "worker-thread"])
def test_async_policy_runs_before_constructor(
    fresh_client,
    monkeypatch,
    tmp_path,
    async_execution_mode,
):
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
    monkeypatch.setattr(
        backend_app,
        "BFF_ASYNC_EXECUTION_MODE",
        async_execution_mode,
    )

    async def deny_policy(**kwargs):
        await asyncio.sleep(0)
        return False

    set_bff_policy_hook(deny_policy)
    try:
        response = fresh_client.post(
            "/restricted/classcall/restricted.py/Restricted/run", json={"args": [], "kwargs": {}}
        )
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
    login_response = _password_login(
        fresh_client,
        email="person@example.com",
        password="local",
        follow_redirects=False,
    )
    assert login_response.headers["cache-control"] == "private, no-store, max-age=0"
    without_token = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc", json={"args": [], "kwargs": {}}
    )
    assert without_token.status_code == 403
    with_token = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers=_csrf_headers(fresh_client),
    )
    assert with_token.status_code == 200
    assert with_token.headers["cache-control"] == "private, no-store, max-age=0"
    assert set(with_token.headers["vary"].split(", ")) == {
        "Cookie",
        "Authorization",
    }


def test_bff_cannot_override_private_cache_policy(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "cache_service.py").write_text(textwrap.dedent("""
        from fastapi import Response
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class CacheService:
            def read(self):
                return Response(
                    content=b"private account data",
                    media_type="text/plain",
                    headers={
                        "Cache-Control": "no-cache, public, max-age=3600",
                        "Vary": "Accept-Encoding",
                    },
                )
    """), encoding="utf-8")
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "require_auth", lambda _request: "noauth")
    backend_app.reload_bff_registry(str(tmp_path))

    response = fresh_client.post(
        "/cache_service/classcall/cache_service.py/CacheService/read",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 200
    assert response.text == "private account data"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert set(response.headers["vary"].split(", ")) == {
        "Accept-Encoding",
        "Cookie",
        "Authorization",
    }


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

    login = _password_login(
        fresh_client,
        application="example",
        email="person@example.com",
        password="local",
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
        "/example/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers=call_headers,
    )
    copied_curl_replay = fresh_client.post(
        "/example/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers=call_headers,
    )
    assert first.status_code == 200
    assert copied_curl_replay.status_code == 409


def _prepare_bff_replay_refill(fresh_client, monkeypatch, dummy_module):
    import ast
    import re
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_BFF_REPLAY_TOKENS", True)
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_BATCH_SIZE", 4)
    monkeypatch.setattr(
        backend_app,
        "AUTH_LOGIN_RATE_LIMITER",
        SlidingWindowRateLimiter(20, 60),
    )
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    backend_app.reload_bff_registry(str(dummy_module))
    login = _password_login(
        fresh_client,
        application="example",
        email="person@example.com",
        password="local",
        follow_redirects=False,
    )
    assert login.status_code == 303
    package = fresh_client.get("/example/appcode/appcode.pyt")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        stub = archive.read("example.py").decode("utf-8")
    capsule_match = re.search(r"_pytincture_replay_capsule = (.+)", stub)
    assert capsule_match
    return {
        **_csrf_headers(fresh_client),
        "X-Pytincture-Client": ast.literal_eval(capsule_match.group(1)),
    }


@pytest.mark.parametrize(
    "limiter_name",
    (
        "BFF_REPLAY_SESSION_ISSUE_LIMITER",
        "BFF_REPLAY_PEER_ISSUE_LIMITER",
        "BFF_REPLAY_WORKER_ISSUE_LIMITER",
    ),
)
def test_bff_replay_refills_have_session_peer_and_worker_quotas(
    fresh_client, monkeypatch, dummy_module, limiter_name
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    headers = _prepare_bff_replay_refill(fresh_client, monkeypatch, dummy_module)
    monkeypatch.setattr(
        backend_app,
        limiter_name,
        SlidingWindowRateLimiter(1, 60, max_keys=10),
    )

    assert fresh_client.post("/_pytincture/state", headers=headers).status_code == 200
    rejected = fresh_client.post("/_pytincture/state", headers=headers)
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "60"
    assert rejected.json() == {"detail": "BFF request-proof issuance rate exceeded"}


def test_bff_replay_refill_session_denial_does_not_consume_broader_quotas(
    fresh_client, monkeypatch, dummy_module
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    headers = _prepare_bff_replay_refill(fresh_client, monkeypatch, dummy_module)
    session_id = _decode_session_cookie(
        fresh_client,
        backend_app.SAML_SECRET_KEY,
    )["session_id"]
    session_limiter = SlidingWindowRateLimiter(1, 60, max_keys=10)
    peer_limiter = SlidingWindowRateLimiter(1, 60, max_keys=10)
    worker_limiter = SlidingWindowRateLimiter(1, 60, max_keys=10)
    assert session_limiter.allow(session_id) == (True, 0)
    monkeypatch.setattr(
        backend_app,
        "BFF_REPLAY_SESSION_ISSUE_LIMITER",
        session_limiter,
    )
    monkeypatch.setattr(
        backend_app,
        "BFF_REPLAY_PEER_ISSUE_LIMITER",
        peer_limiter,
    )
    monkeypatch.setattr(
        backend_app,
        "BFF_REPLAY_WORKER_ISSUE_LIMITER",
        worker_limiter,
    )

    rejected = fresh_client.post("/_pytincture/state", headers=headers)
    assert rejected.status_code == 429
    assert len(peer_limiter._entries) == 0
    assert len(worker_limiter._entries) == 0


def test_bff_replay_refill_rejects_full_local_store_without_partial_issue(
    fresh_client, monkeypatch, dummy_module
):
    import pytincture.backend.app as backend_app

    headers = _prepare_bff_replay_refill(fresh_client, monkeypatch, dummy_module)
    store = LocalReplayStore(3, 3)
    monkeypatch.setattr(backend_app, "BFF_REPLAY_TOKEN_STORE", store)

    rejected = fresh_client.post("/_pytincture/state", headers=headers)
    assert rejected.status_code == 503
    assert rejected.headers["retry-after"] == "1"
    assert len(store) == 0


def test_bff_replay_refill_store_work_runs_off_the_event_loop(
    fresh_client, monkeypatch, dummy_module
):
    import pytincture.backend.app as backend_app

    headers = _prepare_bff_replay_refill(fresh_client, monkeypatch, dummy_module)
    event_loop_threads = []
    store_threads = []
    original_admission = backend_app._admit_bff_replay_issuance

    def tracked_admission(request, session_id):
        event_loop_threads.append(threading.get_ident())
        return original_admission(request, session_id)

    class RecordingReplayStore(LocalReplayStore):
        def issue_batch(self, subject, records, ttl_seconds):
            store_threads.append(threading.get_ident())
            return super().issue_batch(subject, records, ttl_seconds)

    monkeypatch.setattr(
        backend_app,
        "_admit_bff_replay_issuance",
        tracked_admission,
    )
    monkeypatch.setattr(
        backend_app,
        "BFF_REPLAY_TOKEN_STORE",
        RecordingReplayStore(100, 20),
    )

    response = fresh_client.post("/_pytincture/state", headers=headers)
    assert response.status_code == 200
    assert store_threads[0] != event_loop_threads[0]


def test_bff_methods_default_to_post(fresh_client, monkeypatch, dummy_module):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "user@example.com"})
    response = fresh_client.get(
        "/demoapp/classcall/example.py/ExampleClass/testfunc"
    )
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_revoked_session_is_rejected(fresh_client, monkeypatch, dummy_module):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "true")
    _password_login(
        fresh_client,
        email="person@example.com",
        password="local",
        follow_redirects=False,
    )
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    backend_app.revoke_session(session_data["session_id"])
    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers=_csrf_headers(fresh_client),
    )
    assert response.status_code == 401


def test_revocation_is_stateless_by_default_and_shared_failures_fail_closed(monkeypatch):
    import time
    import pytincture.backend.app as backend_app

    local_store = {}
    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "false")
    monkeypatch.setattr(backend_app, "AUTH_SESSION_REVOCATIONS", local_store)
    backend_app.revoke_session("session-id")
    assert local_store == {}

    class BrokenSharedStore:
        def get(self, key):
            raise RuntimeError("shared store unavailable")

    user = backend_app._build_auth_session_user({"email": "user@example.com"})
    request = type("Request", (), {"session": {
        "user": user,
        "session_id": "session-id",
        "auth_issued_at": time.time(),
    }})()
    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "true")
    monkeypatch.setattr(backend_app, "AUTH_SESSION_REVOCATIONS", BrokenSharedStore())
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    assert backend_app.require_auth(request) is None


def test_main_page_revocation_lookup_runs_off_the_event_loop(
    fresh_client, monkeypatch, dummy_module
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    login = _password_login(
        fresh_client,
        application="example",
        email="person@example.com",
        password="local",
        follow_redirects=False,
    )
    assert login.status_code == 303

    event_loop_threads = []
    store_threads = []
    original_validate_application_name = backend_app.validate_application_name

    def tracked_validate_application_name(value):
        event_loop_threads.append(threading.get_ident())
        return original_validate_application_name(value)

    class RecordingRevocationStore:
        def get(self, _key):
            store_threads.append(threading.get_ident())
            return None

    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "true")
    monkeypatch.setattr(
        backend_app,
        "AUTH_SESSION_REVOCATIONS",
        RecordingRevocationStore(),
    )
    monkeypatch.setattr(
        backend_app,
        "validate_application_name",
        tracked_validate_application_name,
    )

    response = fresh_client.get("/example", follow_redirects=False)
    # Authentication completes before this fixture's intentionally missing UI
    # entrypoint is reported.
    assert response.status_code == 422
    assert store_threads[0] != event_loop_threads[0]


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


def test_public_assets_require_a_real_app_and_explicit_app_ownership(
    fresh_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.delenv("PYTINCTURE_BROWSER_FILES", raising=False)
    monkeypatch.delenv("PYTINCTURE_PUBLIC_ASSET_PATHS", raising=False)
    (tmp_path / "demoapp.py").write_text('APP_FAVICON = "logo.png"\n')
    (tmp_path / "otherapp.py").write_text("# separate application\n")
    (tmp_path / "server.py").write_text("SECRET = 'hidden'\n")
    (tmp_path / ".env").write_text("SECRET=hidden\n")
    (tmp_path / "credentials.json").write_text('{"token":"hidden"}\n')
    (tmp_path / "logo.png").write_bytes(b"png")

    assert fresh_client.get("/demoapp/appcode/server.py").status_code == 404
    assert fresh_client.get("/demoapp/appcode/.env").status_code == 404
    assert fresh_client.get("/demoapp/appcode/logo.png").status_code == 200
    assert fresh_client.get("/otherapp/appcode/logo.png").status_code == 404
    assert fresh_client.get("/missingapp/appcode/logo.png").status_code == 404

    monkeypatch.setenv("PYTINCTURE_PUBLIC_ASSET_PATHS", "*.py")
    assert fresh_client.get("/demoapp/appcode/server.py").status_code == 404

    monkeypatch.setenv("PYTINCTURE_PUBLIC_ASSET_PATHS", "credentials.json")
    assert fresh_client.get("/demoapp/appcode/credentials.json").status_code == 404


def test_public_asset_globs_can_be_scoped_per_application(
    fresh_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PYTINCTURE_PUBLIC_ASSET_PATHS",
        json.dumps({
            "alpha": ["alpha.html"],
            "beta": ["beta.html"],
            "*": ["shared.txt"],
        }),
    )
    (tmp_path / "alpha.py").write_text("# alpha app\n")
    (tmp_path / "beta.py").write_text("# beta app\n")
    (tmp_path / "alpha.html").write_text("alpha")
    (tmp_path / "beta.html").write_text("beta")
    (tmp_path / "shared.txt").write_text("shared")

    assert fresh_client.get("/alpha/appcode/alpha.html").status_code == 200
    assert fresh_client.get("/beta/appcode/alpha.html").status_code == 404
    assert fresh_client.get("/beta/appcode/beta.html").status_code == 200
    assert fresh_client.get("/alpha/appcode/beta.html").status_code == 404
    assert fresh_client.get("/alpha/appcode/shared.txt").status_code == 200
    assert fresh_client.get("/beta/appcode/shared.txt").status_code == 200


def test_public_svg_assets_are_sandboxed(fresh_client, monkeypatch, tmp_path):
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text('APP_FAVICON = "logo.svg"\n')
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>parent.pwned=true</script></svg>'
    (tmp_path / "logo.svg").write_bytes(svg)

    response = fresh_client.get("/demoapp/appcode/logo.svg")
    head = fresh_client.head("/demoapp/appcode/logo.svg")

    assert response.status_code == 200
    assert response.content == svg
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-security-policy"].startswith(
        "sandbox; default-src 'none'"
    )
    assert "script-src" not in response.headers["content-security-policy"]
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(svg))


def test_large_public_asset_head_is_metadata_only_and_get_streams_off_loop(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setenv("PYTINCTURE_PUBLIC_ASSET_PATHS", "large.bin")
    (tmp_path / "demoapp.py").write_text("# demo application\n")
    payload = b"x" * (3 * 1024 * 1024 + 17)
    (tmp_path / "large.bin").write_bytes(payload)

    original_read_contained = backend_app.read_contained_file
    original_run_in_threadpool = backend_app.run_in_threadpool
    original_os_read = backend_app.os.read
    offloaded = []
    read_sizes = []

    def reject_buffered_asset_read(root, relative_path, **kwargs):
        assert relative_path != "large.bin"
        return original_read_contained(root, relative_path, **kwargs)

    async def tracked_threadpool(function, *args, **kwargs):
        offloaded.append(getattr(function, "__name__", repr(function)))
        return await original_run_in_threadpool(function, *args, **kwargs)

    def tracked_os_read(descriptor, size):
        read_sizes.append(size)
        return original_os_read(descriptor, size)

    monkeypatch.setattr(backend_app, "read_contained_file", reject_buffered_asset_read)
    monkeypatch.setattr(backend_app, "run_in_threadpool", tracked_threadpool)
    monkeypatch.setattr(backend_app.os, "read", tracked_os_read)

    head = fresh_client.head("/demoapp/appcode/large.bin")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(payload))
    assert "open_contained_file" not in offloaded

    offloaded.clear()
    response = fresh_client.get("/demoapp/appcode/large.bin")
    assert response.status_code == 200
    assert response.content == payload
    assert "_resolve_public_asset" in offloaded
    assert "open_contained_file" in offloaded
    assert max(read_sizes) <= 64 * 1024


def test_public_asset_authorization_cache_invalidates_on_entrypoint_change(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.delenv("PYTINCTURE_PUBLIC_ASSET_PATHS", raising=False)
    monkeypatch.setattr(
        backend_app,
        "PUBLIC_ASSET_AUTHORIZATION_CACHE",
        backend_app._PublicAssetAuthorizationCache(4),
    )
    entrypoint = tmp_path / "demoapp.py"
    entrypoint.write_text('APP_FAVICON = "old.png"\n', encoding="utf-8")
    (tmp_path / "old.png").write_bytes(b"old")
    (tmp_path / "new.png").write_bytes(b"new-content")

    original_read = backend_app.read_contained_file
    entrypoint_reads = []

    def tracked_read(root, relative_path, **kwargs):
        if relative_path == "demoapp.py":
            entrypoint_reads.append(relative_path)
        return original_read(root, relative_path, **kwargs)

    monkeypatch.setattr(backend_app, "read_contained_file", tracked_read)
    assert fresh_client.get("/demoapp/appcode/missing-one.png").status_code == 404
    assert fresh_client.get("/demoapp/appcode/missing-two.png").status_code == 404
    old = fresh_client.get("/demoapp/appcode/old.png")
    assert old.status_code == 200
    assert old.content == b"old"
    assert entrypoint_reads == ["demoapp.py"]

    entrypoint.write_text('APP_FAVICON = "new.png"\n', encoding="utf-8")
    changed = fresh_client.get("/demoapp/appcode/new.png")
    assert changed.status_code == 200
    assert changed.content == b"new-content"
    assert fresh_client.get("/demoapp/appcode/old.png").status_code == 404
    assert entrypoint_reads == ["demoapp.py", "demoapp.py"]

    (tmp_path / "new.png").write_bytes(b"replacement-content")
    replacement = fresh_client.get("/demoapp/appcode/new.png")
    assert replacement.status_code == 200
    assert replacement.content == b"replacement-content"


def test_public_asset_size_rate_and_admission_apply_before_resolution(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.limits import AdmissionRejected

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setenv("PYTINCTURE_PUBLIC_ASSET_PATHS", "asset.bin")
    (tmp_path / "demoapp.py").write_text("# demo app\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"five!")

    monkeypatch.setattr(backend_app, "PUBLIC_ASSET_MAX_BYTES", 4)
    assert fresh_client.get("/demoapp/appcode/asset.bin").status_code == 413
    monkeypatch.setattr(backend_app, "PUBLIC_ASSET_MAX_BYTES", 1024)

    class RejectingRateLimiter:
        def allow(self, _key):
            return False, 9

    def resolution_must_not_run(*_args, **_kwargs):
        raise AssertionError("resolution ran before cheap admission")

    monkeypatch.setattr(
        backend_app, "PUBLIC_ASSET_RATE_LIMITER", RejectingRateLimiter()
    )
    monkeypatch.setattr(backend_app, "_resolve_public_asset", resolution_must_not_run)
    rejected = fresh_client.get("/demoapp/appcode/asset.bin")
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "9"

    class AllowingRateLimiter:
        def allow(self, _key):
            return True, 0

    class RejectingGate:
        async def acquire(self):
            raise AdmissionRejected("full")

        def release(self):
            raise AssertionError("unacquired gate cannot be released")

    monkeypatch.setattr(
        backend_app, "PUBLIC_ASSET_RATE_LIMITER", AllowingRateLimiter()
    )
    monkeypatch.setattr(backend_app, "PUBLIC_ASSET_GATE", RejectingGate())
    saturated = fresh_client.get("/demoapp/appcode/asset.bin")
    assert saturated.status_code == 503
    assert saturated.headers["retry-after"] == "1"


def test_blocked_public_asset_write_closes_descriptor_and_finishes_once(
    monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app
    from starlette.requests import Request

    payload_path = tmp_path / "asset.bin"
    payload_path.write_bytes(b"payload")
    metadata = backend_app.stat_contained_file(str(tmp_path), "asset.bin")
    opened = []
    finished = []
    original_open = backend_app.open_contained_file

    def tracked_open(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(backend_app, "open_contained_file", tracked_open)

    async def exercise():
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/demoapp/appcode/asset.bin",
            "headers": [],
        })
        response = await backend_app._public_file_response(
            request,
            str(tmp_path),
            "asset.bin",
            metadata,
            max_bytes=1024,
            max_seconds=1,
            write_timeout_seconds=0.01,
            on_finish=lambda: finished.append(True),
        )
        blocked = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and message["more_body"]:
                await blocked.wait()

        await response.stream_response(send)

    asyncio.run(exercise())
    assert len(opened) == 1
    assert opened[0].descriptor == -1
    assert finished == [True]


def test_public_asset_total_deadline_covers_a_stalled_file_read(
    monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app
    from starlette.requests import Request

    (tmp_path / "asset.bin").write_bytes(b"payload")
    metadata = backend_app.stat_contained_file(str(tmp_path), "asset.bin")
    opened = []
    finished = []
    release_read = threading.Event()
    original_open = backend_app.open_contained_file
    original_read = backend_app.os.read

    def tracked_open(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        opened.append(handle)
        return handle

    def stalled_read(descriptor, size):
        release_read.wait(timeout=1)
        return original_read(descriptor, size)

    monkeypatch.setattr(backend_app, "open_contained_file", tracked_open)
    monkeypatch.setattr(backend_app.os, "read", stalled_read)

    async def exercise():
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/demoapp/appcode/asset.bin",
            "headers": [],
        })
        response = await backend_app._public_file_response(
            request,
            str(tmp_path),
            "asset.bin",
            metadata,
            max_bytes=1024,
            max_seconds=0.01,
            write_timeout_seconds=1,
            on_finish=lambda: finished.append(True),
        )

        async def send(_message):
            return None

        await response.stream_response(send)
        assert finished == [True]
        release_read.set()
        for _ in range(100):
            if opened[0].descriptor == -1:
                break
            await asyncio.sleep(0.01)

    try:
        asyncio.run(exercise())
    finally:
        release_read.set()
    assert opened[0].descriptor == -1
    assert finished == [True]


def test_detected_widget_wheel_is_public_but_unrelated_wheels_are_not(
    fresh_client, monkeypatch, tmp_path
):
    from packaging.version import Version
    import pytincture.backend.app as backend_app
    import pytincture.backend.safe_paths as safe_paths
    from pytincture.backend.safe_paths import SecureFileDigestCache

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.delenv("PYTINCTURE_PUBLIC_ASSET_PATHS", raising=False)
    (tmp_path / "demoapp.py").write_text("import demo_widgets\n")
    (tmp_path / "demo_widgets.py").write_text(
        '__widgetset__ = "demo-widgets"\n__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    matching_version = "demo_widgets-0.1.0-py3-none-any.whl"
    matching_dev = "demo_widgets-99.99.99-py3-none-any.whl"
    stale_version = "demo_widgets-0.0.9-py3-none-any.whl"
    configured_dev = "demo_widgets-42.0.dev1-py3-none-any.whl"
    unrelated = "server_helpers-1.0.0-py3-none-any.whl"
    (tmp_path / matching_version).write_bytes(b"versioned-wheel")
    (tmp_path / matching_dev).write_bytes(b"development-wheel")
    (tmp_path / stale_version).write_bytes(b"stale-wheel")
    (tmp_path / configured_dev).write_bytes(b"configured-development-wheel")
    (tmp_path / unrelated).write_bytes(b"server-only-wheel")
    nested = tmp_path / "private"
    nested.mkdir()
    (nested / matching_version).write_bytes(b"nested-wheel")
    monkeypatch.setattr(
        backend_app,
        "PUBLIC_WIDGET_WHEEL_DIGEST_CACHE",
        SecureFileDigestCache(4),
    )
    original_hash = safe_paths.hash_open_file
    hash_calls = []

    def tracked_hash(handle):
        hash_calls.append(handle.metadata.identity)
        return original_hash(handle)

    monkeypatch.setattr(safe_paths, "hash_open_file", tracked_hash)
    uncached_head = fresh_client.head(f"/demoapp/appcode/{matching_version}")
    assert uncached_head.status_code == 200
    assert "x-pytincture-sha256" not in uncached_head.headers
    assert hash_calls == []
    assert fresh_client.get(f"/demoapp/appcode/{matching_version}").status_code == 200
    assert fresh_client.get(f"/demoapp/appcode/{matching_version}").status_code == 200
    assert len(hash_calls) == 1
    assert fresh_client.get(f"/demoapp/appcode/{matching_dev}").status_code == 200
    assert fresh_client.head(f"/demoapp/appcode/{matching_version}").status_code == 200
    assert fresh_client.head(f"/demoapp/appcode/{matching_dev}").status_code == 200
    cached_head = fresh_client.head(f"/demoapp/appcode/{matching_version}")
    assert cached_head.headers["x-pytincture-sha256"] == hashlib.sha256(
        b"versioned-wheel"
    ).hexdigest()
    assert (
        fresh_client.get(f"/demoapp/appcode/{matching_version}").headers[
            "x-pytincture-sha256"
        ]
        == __import__("hashlib").sha256(b"versioned-wheel").hexdigest()
    )
    assert fresh_client.get(f"/demoapp/appcode/{unrelated}").status_code == 404
    assert fresh_client.get(f"/demoapp/appcode/{stale_version}").status_code == 404
    assert fresh_client.head(f"/demoapp/appcode/{stale_version}").status_code == 404
    assert fresh_client.head(f"/demoapp/appcode/{unrelated}").status_code == 404
    assert fresh_client.get(f"/demoapp/appcode/private/{matching_version}").status_code == 404

    etag = cached_head.headers["etag"]
    conditional = fresh_client.get(
        f"/demoapp/appcode/{matching_version}",
        headers={"If-None-Match": etag},
    )
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert len(hash_calls) == 2  # declared and development wheels, once each

    monkeypatch.setattr(backend_app, "DEVELOPMENT_WIDGET_VERSION", Version("42.0.dev1"))
    assert fresh_client.get(f"/demoapp/appcode/{configured_dev}").status_code == 200
    assert fresh_client.get(f"/demoapp/appcode/{matching_dev}").status_code == 404


def test_widget_wheel_serving_enforces_size_rate_and_admission_limits(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.limits import AdmissionRejected

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("import demo_widgets\n")
    (tmp_path / "demo_widgets.py").write_text(
        '__widgetset__ = "demo-widgets"\n__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    wheel = "demo_widgets-0.1.0-py3-none-any.whl"
    (tmp_path / wheel).write_bytes(b"wheel-body")

    monkeypatch.setattr(backend_app, "PUBLIC_WIDGET_WHEEL_MAX_BYTES", 4)
    assert fresh_client.get(f"/demoapp/appcode/{wheel}").status_code == 413
    monkeypatch.setattr(backend_app, "PUBLIC_WIDGET_WHEEL_MAX_BYTES", 1024)

    class RejectingRateLimiter:
        def allow(self, _key):
            return False, 7

    monkeypatch.setattr(
        backend_app, "PUBLIC_WIDGET_WHEEL_RATE_LIMITER", RejectingRateLimiter()
    )
    rate_limited = fresh_client.get(f"/demoapp/appcode/{wheel}")
    assert rate_limited.status_code == 429
    assert rate_limited.headers["retry-after"] == "7"

    class AllowingRateLimiter:
        def allow(self, _key):
            return True, 0

    class RejectingGate:
        async def acquire(self):
            raise AdmissionRejected("full")

        def release(self):
            raise AssertionError("unacquired gate cannot be released")

    monkeypatch.setattr(
        backend_app, "PUBLIC_WIDGET_WHEEL_RATE_LIMITER", AllowingRateLimiter()
    )
    monkeypatch.setattr(backend_app, "PUBLIC_WIDGET_WHEEL_GATE", RejectingGate())
    saturated = fresh_client.get(f"/demoapp/appcode/{wheel}")
    assert saturated.status_code == 503
    assert saturated.headers["retry-after"] == "1"


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


def test_mcp_has_no_automatic_tools_and_rejects_generic_dispatcher(monkeypatch):
    import pytincture.backend.app as backend_app
    from pytincture.backend.mcp import parse_tool_specs

    assert asyncio.run(backend_app.mcp.list_tools()) == []
    with pytest.raises(RuntimeError, match="must contain only"):
        parse_tool_specs('[{"name":"dispatch","operation":"postClassCall"}]')


def test_mcp_tool_mapping_requires_exact_scopes():
    from pytincture.backend.mcp import parse_tool_specs

    with pytest.raises(RuntimeError, match="non-empty exact strings"):
        parse_tool_specs(json.dumps([{
            "name": "run", "application": "demoapp", "module": "service.py",
            "class": "Service", "method": "run", "scopes": ["bff:*"],
        }]))


def test_validation_error_does_not_echo_request_body(fresh_client):
    response = fresh_client.post(
        "/demoapp/auth/mcp",
        json={"email": "person@example.com", "secret": "must-not-echo"},
    )
    assert response.status_code == 422
    assert "must-not-echo" not in response.text


def test_mcp_password_login_requires_one_time_application_transaction(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")

    missing = fresh_client.post(
        "/demoapp/auth/mcp",
        json={"email": "person@example.com", "password": "local"},
    )
    assert missing.status_code == 403

    initiation = fresh_client.get("/demoapp/auth/mcp")
    token = initiation.json()["login_csrf_token"]
    wrong_application = fresh_client.post(
        "/other/auth/mcp",
        json={
            "email": "person@example.com",
            "password": "local",
            "login_csrf_token": token,
        },
    )
    assert wrong_application.status_code == 403

    initiation = fresh_client.get("/demoapp/auth/mcp")
    token = initiation.json()["login_csrf_token"]
    payload = {
        "email": "person@example.com",
        "password": "local",
        "login_csrf_token": token,
    }
    authenticated = fresh_client.post("/demoapp/auth/mcp", json=payload)
    replayed = fresh_client.post("/demoapp/auth/mcp", json=payload)

    assert authenticated.status_code == 200
    assert replayed.status_code == 403


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


def test_main_route_ignores_backend_session_snapshot(
    fresh_client, monkeypatch, tmp_path
):
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
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("value = 1\n", encoding="utf-8")

    response = _password_login(
        fresh_client,
        email="stale@example.com",
        password="old-password",
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

def test_main_route_no_auth_when_disabled(fresh_client, monkeypatch, tmp_path):
    """
    If both ENABLE_GOOGLE_AUTH and ENABLE_USER_LOGIN are disabled,
    the main route should serve the index page (HTTP 200).
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    application_name = "demoapp"
    (tmp_path / f"{application_name}.py").write_text(
        "value = 1\n", encoding="utf-8"
    )
    response = fresh_client.get(f"/{application_name}")
    assert response.status_code == 200


def test_build_streamable_mcp_app_prefers_http_app():
    class DummyMCP:
        def http_app(self, **kwargs):
            return kwargs

    from pytincture.backend.mcp import build_streamable_app
    result = build_streamable_app(
        DummyMCP(), path="/", allowed_hosts=("mcp.example",),
        allowed_origins=("https://mcp.example",),
    )

    assert result == {
        "transport": "streamable-http", "path": "/", "stateless_http": True,
        "host_origin_protection": True, "allowed_hosts": ["mcp.example"],
        "allowed_origins": ["https://mcp.example"],
    }


def test_class_call_noauth(dummy_module, monkeypatch, fresh_client):
    """
    Test the /classcall endpoint when the call is allowed without auth.
    We update MODULES_PATH and ALLOWED_NOAUTH_CLASSCALLS accordingly.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    allowed_calls = [{
        "application": "example",
        "file": "example.py",
        "class": "ExampleClass",
        "function": "testfunc"
    }]
    ALLOWED_NOAUTH_CLASSCALLS.extend(allowed_calls)
    fresh_client.cookies.clear()
    response = fresh_client.post(
        "/example/classcall/example.py/ExampleClass/testfunc", json={"args": [], "kwargs": {}}
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response.get("result") == "success"


@pytest.mark.parametrize("method", ("GET", "POST", "PUT", "PATCH", "DELETE"))
def test_unscoped_bff_routes_are_removed(method, fresh_client):
    response = fresh_client.request(
        method,
        "/classcall/worker.py/Worker/ping",
        json={"args": [], "kwargs": {}} if method != "GET" else None,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_noauth_bff_requires_a_real_application_graph_without_method_allowlist(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    marker = tmp_path / "unrelated-imported"
    public_source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class PublicData:
            def read(self):
                return {"scope": "portal"}
    """)
    unrelated_source = textwrap.dedent(f"""
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend

        Path({str(marker)!r}).write_text("imported")

        @backend_for_frontend
        class InternalData:
            def read(self):
                return {{"scope": "internal"}}
    """)
    (tmp_path / "portal.py").write_text("from public_data import PublicData\n")
    (tmp_path / "public_data.py").write_text(public_source)
    (tmp_path / "dormant.py").write_text(unrelated_source)
    (tmp_path / "admin.py").write_text(unrelated_source)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    ALLOWED_NOAUTH_CLASSCALLS.clear()

    public = fresh_client.post(
        "/portal/classcall/public_data.py/PublicData/read", json={"args": [], "kwargs": {}}
    )
    dormant = fresh_client.post(
        "/portal/classcall/dormant.py/InternalData/read", json={"args": [], "kwargs": {}}
    )
    administrative = fresh_client.post(
        "/portal/classcall/admin.py/InternalData/read", json={"args": [], "kwargs": {}}
    )
    nonexistent = fresh_client.post(
        "/ghost/classcall/public_data.py/PublicData/read", json={"args": [], "kwargs": {}}
    )

    assert public.status_code == 200
    assert public.json() == {"scope": "portal"}
    assert dormant.status_code == 404
    assert administrative.status_code == 404
    assert nonexistent.status_code == 404
    assert not marker.exists()


def _allow_demoapp_noauth_call():
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.append({
        "application": "demoapp",
        "file": "example.py",
        "class": "ExampleClass",
        "function": "testfunc",
    })


def test_noauth_bff_rejects_text_plain(dummy_module, monkeypatch, fresh_client):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        content='{"kwargs": {}}',
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415


def test_noauth_bff_rejects_cross_origin_form(
    dummy_module, monkeypatch, fresh_client
):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        data={"kwargs": "{}"},
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )

    assert response.status_code == 415


def test_noauth_bff_rejects_null_origin(dummy_module, monkeypatch, fresh_client):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403


def test_noauth_bff_rejects_cross_origin_private_network_request(
    dummy_module, monkeypatch, fresh_client
):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 403


def test_noauth_bff_accepts_same_origin_browser_request(
    dummy_module, monkeypatch, fresh_client
):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers={
            "Origin": "https://127.0.0.1",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 200


def test_noauth_bff_accepts_trusted_non_browser_json_client(
    dummy_module, monkeypatch, fresh_client
):
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    _allow_demoapp_noauth_call()

    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 200


def test_noauth_bff_does_not_export_a_local_same_named_decorator(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "admin.py").write_text(textwrap.dedent("""
        def backend_for_frontend(cls):
            return cls

        @backend_for_frontend
        class Accidental:
            def __init__(self, **kwargs):
                pass

            def secret(self):
                return {"exposed": True}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.post(
        "/admin/classcall/admin.py/Accidental/secret",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "BFF operation not exported"}


def test_impossible_bff_operation_is_rejected_before_application_graph_scan(
    dummy_module, monkeypatch, fresh_client
):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    def unexpected_graph_scan(*_args, **_kwargs):
        raise AssertionError("invalid registry target must not scan application graph")

    monkeypatch.setattr(
        backend_app,
        "_application_bff_identifiers",
        unexpected_graph_scan,
    )
    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/not_exported",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "BFF operation not exported"}


def test_application_graph_cache_reuses_and_invalidates_same_name_sources(
    tmp_path, monkeypatch
):
    import pytincture.backend.app as backend_app

    (tmp_path / "demo.py").write_text("import helper\n", encoding="utf-8")
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    calls = []
    original_discovery = backend_app.browser_package_files

    def counted_discovery(*args, **kwargs):
        calls.append(args[0])
        return original_discovery(*args, **kwargs)

    monkeypatch.setattr(backend_app, "browser_package_files", counted_discovery)
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_GRAPH_CACHE",
        backend_app._ApplicationGraphCache(4),
    )
    first = backend_app._application_bff_identifiers("demo", str(tmp_path))
    second = backend_app._application_bff_identifiers("demo", str(tmp_path))

    assert first == second == {"demo.py", "helper.py"}
    assert calls == ["demo"]

    original_times = helper.stat()
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    os.utime(
        helper,
        ns=(original_times.st_atime_ns, original_times.st_mtime_ns),
    )
    third = backend_app._application_bff_identifiers("demo", str(tmp_path))

    assert third == first
    assert calls == ["demo", "demo"]


def test_application_graph_cache_detects_new_globbed_file(tmp_path, monkeypatch):
    import pytincture.backend.app as backend_app

    (tmp_path / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("PYTINCTURE_BROWSER_FILES", '["*.py"]')
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_GRAPH_CACHE",
        backend_app._ApplicationGraphCache(4),
    )

    assert backend_app._application_bff_identifiers("demo", str(tmp_path)) == {
        "demo.py"
    }
    (tmp_path / "added.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert backend_app._application_bff_identifiers("demo", str(tmp_path)) == {
        "added.py",
        "demo.py",
    }


def test_application_graph_cache_detects_new_import_in_existing_namespace(
    tmp_path, monkeypatch
):
    import pytincture.backend.app as backend_app

    namespace = tmp_path / "plugins"
    namespace.mkdir()
    (tmp_path / "demo.py").write_text("import plugins.optional\n", encoding="utf-8")
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_GRAPH_CACHE",
        backend_app._ApplicationGraphCache(4),
    )

    assert backend_app._application_bff_identifiers("demo", str(tmp_path)) == {
        "demo.py"
    }
    (namespace / "optional.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert backend_app._application_bff_identifiers("demo", str(tmp_path)) == {
        "demo.py",
        "plugins/optional.py",
    }


def test_application_graph_applies_aggregate_source_limit(tmp_path, monkeypatch):
    import pytincture.backend.app as backend_app

    (tmp_path / "demo.py").write_text("VALUE = 'larger than limit'\n", encoding="utf-8")
    monkeypatch.setattr(backend_app, "APPCODE_MAX_TOTAL_BYTES", 8)
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_GRAPH_CACHE",
        backend_app._ApplicationGraphCache(4),
    )

    with pytest.raises(HTTPException, match="aggregate-size limit"):
        backend_app._application_bff_identifiers("demo", str(tmp_path))


def test_bff_rebinding_is_rejected_before_module_import(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    import_marker = tmp_path / "module-imported"
    (tmp_path / "worker.py").write_text(textwrap.dedent(f"""
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend

        Path({str(import_marker)!r}).write_text("imported")

        @backend_for_frontend
        class API:
            def read(self):
                return "public"

        class API:
            def read(self):
                return "internal"
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.post(
        "/worker/classcall/worker.py/API/read",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "BFF operation not exported"}
    assert not import_marker.exists()


@pytest.mark.parametrize(
    "runtime_replacement",
    [
        'globals()["API"] = Internal',
        'setattr(API._pytincture_bff_original, "read", Internal.read)',
    ],
)
def test_bff_dispatch_rejects_runtime_class_or_method_replacement_before_construction(
    runtime_replacement, monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    construction_marker = tmp_path / "replacement-constructed"
    (tmp_path / "worker.py").write_text(textwrap.dedent(f"""
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class API:
            def __init__(self, **kwargs):
                Path({str(construction_marker)!r}).write_text("constructed")

            def read(self):
                return "public"

        class Internal:
            def __init__(self, **kwargs):
                Path({str(construction_marker)!r}).write_text("constructed")

            def read(self):
                return "internal"

        {runtime_replacement}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.post(
        "/worker/classcall/worker.py/API/read",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "BFF operation not exported"}
    assert not construction_marker.exists()


def test_bff_dispatch_preserves_inner_class_decorators(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "worker.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        def identity(target):
            return target

        @backend_for_frontend
        @identity
        class API:
            def __init__(self, **kwargs):
                pass

            def read(self):
                return "public"
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.post(
        "/worker/classcall/worker.py/API/read",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 200
    assert response.json() == "public"


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/bad-name"),
        ("GET", "/bad-name/login"),
        ("POST", "/bad-name/auth/user"),
        ("GET", "/bad-name/appcode/appcode.pyt"),
        ("POST", "/bad-name/classcall/worker.py/Worker/ping"),
        ("GET", "/bad-name/frontend/pytincture.js"),
        ("GET", "/bad%5Cname/login"),
        ("GET", "/healthz/login"),
    ),
)
def test_every_application_route_rejects_invalid_or_reserved_names(
    fresh_client, method, path
):
    response = fresh_client.request(method, path, json={"args": [], "kwargs": {}} if method == "POST" else None)
    assert response.status_code == 404


def test_bff_rejects_windows_separator_path(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    response = fresh_client.post(
        "/demoapp/classcall/pkg%5Cworker.py/Worker/ping",
        json={"args": [], "kwargs": {}},
    )
    assert response.status_code == 400


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_appcode_bff_and_public_assets_reject_symlinks(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "worker.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class Worker:\n"
        "    def ping(self): return 'outside'\n",
        encoding="utf-8",
    )
    (outside / "secret.png").write_bytes(b"outside-secret")
    (tmp_path / "worker.py").symlink_to(outside / "worker.py")
    (tmp_path / "secret.png").symlink_to(outside / "secret.png")
    (tmp_path / "demoapp.py").write_text("import worker\n", encoding="utf-8")
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setenv("PYTINCTURE_BROWSER_FILES", '["secret.png"]')
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")

    archive = fresh_client.get("/demoapp/appcode/appcode.pyt")
    bff = fresh_client.post(
        "/demoapp/classcall/worker.py/Worker/ping", json={"args": [], "kwargs": {}}
    )
    asset = fresh_client.get("/demoapp/appcode/secret.png")
    assert archive.status_code == 404
    assert bff.status_code == 404
    assert asset.status_code == 404


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
        response = fresh_client.post(
            "/restricted/classcall/restricted.py/Restricted/secret",
            json={"args": [], "kwargs": {}},
        )
        assert response.status_code == 403

        current_user["roles"] = ["admin"]
        response = fresh_client.post(
            "/restricted/classcall/restricted.py/Restricted/secret",
            json={"args": [], "kwargs": {}},
        )
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
            @bff_policy(custom="admin")
            def inspect(self):
                return {"ok": True}
    """)
    (modules_dir / "public_restricted.py").write_text(module_code)

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.extend([{
        "application": "public_restricted",
        "file": "public_restricted.py",
        "class": "PublicRestricted",
        "function": "inspect",
    }])

    seen_user = {}

    def policy_hook(user, policy, **kwargs):
        seen_user.update(user)
        roles = set(user.get("roles", []))
        required_role = policy.get("custom")
        if required_role and required_role not in roles:
            return False

    set_bff_policy_hook(policy_hook)
    try:
        response = fresh_client.post("/public_restricted/classcall/public_restricted.py/PublicRestricted/inspect", json={"args": [], "kwargs": {}})
        assert response.status_code == 403
        assert seen_user["auth_type"] == "noauth"
        assert seen_user["is_authenticated"] is False
    finally:
        set_bff_policy_hook(None)


def test_policy_hook_true_none_false_and_invalid_results(
    monkeypatch,
    fresh_client,
    tmp_path,
):
    import pytincture.backend.app as backend_app

    (tmp_path / "decision.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class Decision:
            @bff_policy(scope="read")
            def read(self):
                return {"ok": True}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(
        backend_app,
        "require_auth",
        lambda request: {"email": "user@example.com"},
    )
    decision = {"value": True}
    set_bff_policy_hook(lambda **kwargs: decision["value"])
    url = "/decision/classcall/decision.py/Decision/read"
    try:
        allowed = fresh_client.post(url, json={"args": [], "kwargs": {}})
        decision["value"] = None
        compatible_allow = fresh_client.post(url, json={"args": [], "kwargs": {}})
        decision["value"] = False
        denied = fresh_client.post(url, json={"args": [], "kwargs": {}})
        decision["value"] = "allow"
        non_raising_client = TestClient(
            app,
            base_url="https://127.0.0.1",
            client=("127.0.0.1", 50000),
            raise_server_exceptions=False,
        )
        invalid = non_raising_client.post(url, json={"args": [], "kwargs": {}})
    finally:
        set_bff_policy_hook(None)

    assert allowed.status_code == 200
    assert compatible_allow.status_code == 200
    assert denied.status_code == 403
    assert denied.json() == {"detail": "BFF policy denied the operation"}
    assert invalid.status_code == 500
    assert invalid.json()["detail"] == "Internal server error"


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

    response = fresh_client.post(
        "/direct_load/classcall/direct_load.py/DirectLoad/ping", json={"args": [], "kwargs": {}}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_bff_request_validation_finishes_before_application_import(
    monkeypatch,
    fresh_client,
    tmp_path,
):
    import pytincture.backend.app as backend_app

    marker = tmp_path / "module-imported"
    module = tmp_path / "validated.py"
    module.write_text(textwrap.dedent(f"""
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend

        Path({str(marker)!r}).write_text("imported")

        @backend_for_frontend
        class Validated:
            def run(self, value: int):
                return {{"value": value}}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)

    url = "/validated/classcall/validated.py/Validated/run"
    invalid_bodies = (
        '"{\\"args\\":[],\\"kwargs\\":{\\"value\\":1}}"',
        '{"args":[],"kwargs":{"value":1,"value":2}}',
        '{"args":[NaN],"kwargs":{}}',
        '{"args":[[[[[1]]]]],"kwargs":{}}',
        '{"args":[{"name":"value","type":"int","value":1}],"kwargs":{}}',
        '{"args":[],"kwargs":{"value":"wrong-type"}}',
        '{"args":[],"kwargs":{}}',
    )
    monkeypatch.setattr(backend_app, "BFF_REQUEST_MAX_DEPTH", 4)
    for body in invalid_bodies:
        response = fresh_client.post(
            url,
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert not marker.exists()

    monkeypatch.setattr(backend_app, "BFF_REQUEST_MAX_BYTES", 20)
    oversized = fresh_client.post(
        url,
        content='{"args":[],"kwargs":{"value":123}}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert not marker.exists()

    monkeypatch.setattr(backend_app, "BFF_REQUEST_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(backend_app, "BFF_REQUEST_MAX_DEPTH", 32)
    valid = fresh_client.post(
        url,
        json={"args": [], "kwargs": {"value": 3}},
    )
    assert valid.status_code == 200
    assert valid.json() == {"value": 3}
    assert marker.read_text() == "imported"


def test_get_bff_rejects_cross_site_browser_metadata(
    monkeypatch,
    fresh_client,
    tmp_path,
):
    import pytincture.backend.app as backend_app

    (tmp_path / "readonly.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_http_methods

        @backend_for_frontend
        class ReadOnly:
            @bff_http_methods("GET")
            def status(self):
                return {"ready": True}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    url = "/readonly/classcall/readonly.py/ReadOnly/status"

    cross_site = fresh_client.get(url, headers={"Sec-Fetch-Site": "cross-site"})
    hostile_origin = fresh_client.get(
        url,
        headers={"Origin": "https://attacker.example"},
    )
    same_origin = fresh_client.get(url, headers={"Sec-Fetch-Site": "same-origin"})

    assert cross_site.status_code == 403
    assert hostile_origin.status_code == 403
    assert same_origin.status_code == 200
    assert same_origin.json() == {"ready": True}


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

    response = fresh_client.post(
        "/user_aware/classcall/user_aware.py/UserAware/whoami", json={"args": [], "kwargs": {}}
    )
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
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
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
    (modules_dir / "nested_app.py").write_text(
        "from pkg.internal.worker import Worker\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.post(
        "/nested_app/classcall/pkg/internal/worker.py/Worker/ping",
        json={"args": [], "kwargs": {"value": "hello"}}
    )
    assert response.status_code == 200
    assert response.json()["echo"] == "hello"


def test_class_call_noauth_nested_path(monkeypatch, fresh_client, tmp_path):
    """
    No-auth allowances use exact nested paths scoped to one application.
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
    (modules_dir / "nested_app.py").write_text(
        "from pkg.internal.worker import Worker\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.extend([{
        "application": "nested_app",
        "file": "pkg/internal/worker.py",
        "class": "Worker",
        "function": "ping"
    }])

    response = fresh_client.post(
        "/nested_app/classcall/pkg/internal/worker.py/Worker/ping", json={"args": [], "kwargs": {}}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_noauth_bff_grant_does_not_authorize_same_basename(monkeypatch, fresh_client, tmp_path):
    modules_dir = tmp_path / "collision_modules"
    public_dir = modules_dir / "public"
    private_dir = modules_dir / "private"
    public_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Worker:
            def ping(self):
                return {"ok": True}
    """)
    (public_dir / "worker.py").write_text(source)
    (private_dir / "worker.py").write_text(source)
    (modules_dir / "portal.py").write_text(
        "from public.worker import Worker\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    ALLOWED_NOAUTH_CLASSCALLS.clear()
    ALLOWED_NOAUTH_CLASSCALLS.append({
        "application": "portal",
        "file": "public/worker.py",
        "class": "Worker",
        "function": "ping",
    })

    allowed = fresh_client.post(
        "/portal/classcall/public/worker.py/Worker/ping", json={"args": [], "kwargs": {}}
    )
    collision = fresh_client.post(
        "/portal/classcall/private/worker.py/Worker/ping", json={"args": [], "kwargs": {}}
    )

    assert allowed.status_code == 200
    assert collision.status_code in {401, 404}


def test_authenticated_session_cannot_cross_application_audience(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    modules_dir = tmp_path / "audience_modules"
    modules_dir.mkdir()
    module_source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Data:
            def read(self):
                return {"ok": True}
    """)
    (modules_dir / "alpha.py").write_text(module_source)
    (modules_dir / "beta.py").write_text(module_source)
    monkeypatch.setenv("MODULES_PATH", str(modules_dir))
    monkeypatch.setattr(
        backend_app,
        "require_auth",
        lambda request: {
            "email": "user@example.test",
            "is_authenticated": True,
            "application": "alpha",
        },
    )
    monkeypatch.setattr(backend_app, "_validate_csrf", lambda request, user: None)

    same_app = fresh_client.post("/alpha/classcall/alpha.py/Data/read", json={"args": [], "kwargs": {}})
    other_app = fresh_client.post("/beta/classcall/beta.py/Data/read", json={"args": [], "kwargs": {}})

    assert same_app.status_code == 200
    assert other_app.status_code == 403


def test_declared_provider_policy_rejects_other_provider(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "portal.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class Restricted:
            @bff_policy(auth_provider="google")
            def read(self):
                return {"ok": True}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(
        backend_app,
        "require_auth",
        lambda request: {
            "email": "user@example.test",
            "is_authenticated": True,
            "application": "portal",
            "auth_provider": "microsoft",
        },
    )
    set_bff_policy_hook(lambda **kwargs: None)
    try:
        response = fresh_client.post(
            "/portal/classcall/portal.py/Restricted/read", json={"args": [], "kwargs": {}}
        )
    finally:
        set_bff_policy_hook(None)

    assert response.status_code == 403


def test_policy_bearing_export_fails_startup_without_hook(monkeypatch, tmp_path):
    import pytincture.backend.app as backend_app

    (tmp_path / "restricted.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_policy

        @backend_for_frontend
        class Restricted:
            @bff_policy(role="admin")
            def read(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.delenv("BFF_POLICY_HOOK_PATH", raising=False)
    set_bff_policy_hook(None)

    with pytest.raises(RuntimeError, match="@bff_policy exports require"):
        backend_app._validate_bff_policy_configuration()


def test_legacy_app_modules_path_trust_handler_warns_or_fails(
    monkeypatch,
    caplog,
    tmp_path,
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "get_modules_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        backend_app,
        "modules_path_appears_writable",
        lambda path: True,
    )
    monkeypatch.setattr(backend_app, "REQUIRE_READONLY_MODULES_PATH", False)

    with caplog.at_level("WARNING", logger="pytincture.security"):
        backend_app.validate_modules_path_trust_configuration()
    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "security.modules_path_writable"
    assert event["enforcement"] is False

    monkeypatch.setattr(backend_app, "REQUIRE_READONLY_MODULES_PATH", True)
    with pytest.raises(RuntimeError, match="MODULES_PATH is writable"):
        backend_app.validate_modules_path_trust_configuration()


def test_microsoft_mutable_email_admission_warns_without_breaking_configuration(
    monkeypatch, caplog
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", True)
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_ADMISSION",
        {
            "email_only": {
                "providers": ("microsoft",),
                "email_domains": ("example.com",),
            },
            "stable": {
                "providers": ("microsoft",),
                "tenants": ("tenant-123",),
                "object_ids": ("object-456",),
                "emails": ("person@example.com",),
            },
        },
    )
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")

    with caplog.at_level("WARNING", logger="pytincture.security"):
        backend_app.validate_microsoft_identity_admission_configuration()

    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "security.microsoft_mutable_email_admission"
    assert event["enforcement"] is False
    assert event["scopes"] == "email_only,global_allowed_emails"
    assert "stable" not in event["scopes"]


def test_microsoft_stable_identity_admission_emits_no_mutable_email_warning(
    monkeypatch, caplog
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", True)
    monkeypatch.setattr(
        backend_app,
        "APPLICATION_ADMISSION",
        {
            "stable": {
                "providers": ("microsoft",),
                "tenants": ("tenant-123",),
                "object_ids": ("object-456",),
            }
        },
    )
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)

    with caplog.at_level("WARNING", logger="pytincture.security"):
        backend_app.validate_microsoft_identity_admission_configuration()

    assert "security.microsoft_mutable_email_admission" not in caplog.text


@pytest.mark.parametrize("async_execution_mode", ["event-loop", "worker-thread"])
def test_class_call_streaming(
    monkeypatch,
    fresh_client,
    tmp_path,
    async_execution_mode,
):
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
    monkeypatch.setattr(
        backend_app,
        "BFF_ASYNC_EXECUTION_MODE",
        async_execution_mode,
    )
    monkeypatch.setattr(backend_app, "USER_SESSION_DICT", {"tester@example.com": {"email": "tester@example.com"}})
    monkeypatch.setattr(backend_app, "require_auth", lambda request: {"email": "tester@example.com"})

    response = fresh_client.post(
        "/stream_widget/classcall/stream_widget.py/StreamWidget/ticker",
        json={"args": [], "kwargs": {"count": 3}}
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
        "/slow_widget/classcall/slow_widget.py/SlowWidget/wait",
        json={"args": [], "kwargs": {}},
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["correlation_id"]


def test_slow_bff_body_times_out_before_execution_admission(monkeypatch):
    import pytincture.backend.app as backend_app

    class TrackingGate:
        def __init__(self):
            self.acquire_calls = 0

        async def acquire(self):
            self.acquire_calls += 1

        def release(self):
            pass

    async def exercise():
        gate = TrackingGate()
        waiting = asyncio.Event()
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            if receive_calls == 1:
                return {
                    "type": "http.request",
                    "body": b'{"args":',
                    "more_body": True,
                }
            await waiting.wait()
            raise AssertionError("unreachable")

        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/demo/classcall/demo.py/Demo/run",
                "raw_path": b"/demo/classcall/demo.py/Demo/run",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 443),
            },
            receive,
        )
        monkeypatch.setattr(backend_app, "BFF_ADMISSION_GATE", gate)
        monkeypatch.setattr(
            backend_app,
            "BFF_REQUEST_INGRESS_TIMEOUT_SECONDS",
            0.01,
        )
        admission = backend_app._admit_bff_call(request)
        with pytest.raises(HTTPException) as timed_out:
            await admission.__anext__()
        assert timed_out.value.status_code == 408
        assert timed_out.value.detail == "BFF request body timed out"
        assert gate.acquire_calls == 0

    asyncio.run(exercise())


def test_bff_ingress_is_cached_and_does_not_spend_execution_time(monkeypatch):
    import pytincture.backend.app as backend_app

    class TrackingGate:
        def __init__(self):
            self.acquire_calls = 0
            self.release_calls = 0

        async def acquire(self):
            self.acquire_calls += 1

        def release(self):
            self.release_calls += 1

    async def exercise():
        payload = b'{"args":[],"kwargs":{}}'
        gate = TrackingGate()

        async def receive():
            await asyncio.sleep(0.02)
            return {
                "type": "http.request",
                "body": payload,
                "more_body": False,
            }

        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/demo/classcall/demo.py/Demo/run",
                "raw_path": b"/demo/classcall/demo.py/Demo/run",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 443),
            },
            receive,
        )
        monkeypatch.setattr(backend_app, "BFF_ADMISSION_GATE", gate)
        monkeypatch.setattr(
            backend_app,
            "BFF_REQUEST_INGRESS_TIMEOUT_SECONDS",
            0.1,
        )
        monkeypatch.setattr(backend_app, "BFF_CALL_TIMEOUT_SECONDS", 0.5)

        admission = backend_app._admit_bff_call(request)
        await admission.__anext__()
        try:
            assert gate.acquire_calls == 1
            assert request.state.bff_request_body == payload
            assert await request.body() == payload
            assert backend_app._remaining_bff_seconds(request) > 0.45
        finally:
            await admission.aclose()
        assert gate.release_calls == 1

    asyncio.run(exercise())


def test_opt_in_async_worker_mode_keeps_health_responsive(
    monkeypatch,
    tmp_path,
):
    import pytincture.backend.app as backend_app

    started_marker = tmp_path / "async-started"
    (tmp_path / "busy_async.py").write_text(textwrap.dedent(f"""
        import time
        from pathlib import Path
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class BusyAsync:
            async def run(self):
                Path({str(started_marker)!r}).write_text("started")
                deadline = time.monotonic() + 0.4
                while time.monotonic() < deadline:
                    pass
                return {{"done": True}}

            async def echo_timeout_detail(self, timeout_detail):
                return timeout_detail
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "BFF_ASYNC_EXECUTION_MODE", "worker-thread")
    monkeypatch.setattr(backend_app, "BFF_CALL_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    backend_app.reload_bff_registry(str(tmp_path))

    async def exercise():
        transport = httpx.ASGITransport(
            app=app,
            client=("127.0.0.1", 50000),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://127.0.0.1",
        ) as client:
            started = time.monotonic()
            call = asyncio.create_task(client.post(
                "/busy_async/classcall/busy_async.py/BusyAsync/run",
                json={"args": [], "kwargs": {}},
            ))
            while not started_marker.exists():
                if time.monotonic() - started > 1:
                    await call
                    raise AssertionError("async BFF did not start")
                await asyncio.sleep(0.005)
            health = await client.get("/healthz")
            health_elapsed = time.monotonic() - started
            response = await call
            echo = await client.post(
                "/busy_async/classcall/busy_async.py/BusyAsync/echo_timeout_detail",
                json={"args": [], "kwargs": {"timeout_detail": "application value"}},
            )
            return health, health_elapsed, response, echo

    health, health_elapsed, response, echo = asyncio.run(exercise())

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health_elapsed < 0.3
    assert response.status_code == 200
    assert response.json() == {"done": True}
    assert echo.status_code == 200
    assert echo.json() == "application value"


def test_ordinary_bff_results_are_serialized_under_a_hard_byte_limit(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "bounded.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend
        from fastapi import Response

        @backend_for_frontend
        class Bounded:
            def __init__(self, _user): pass
            def large(self): return {"payload": "x" * 1000}
            def raw(self): return Response(content=b"x" * 1000)
            def ping(self): return {"ready": True}
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "BFF_RESULT_MAX_BYTES", 64)
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    backend_app.reload_bff_registry(str(tmp_path))

    oversized = fresh_client.post(
        "/bounded/classcall/bounded.py/Bounded/large",
        json={"args": [], "kwargs": {}},
    )
    recovered = fresh_client.post(
        "/bounded/classcall/bounded.py/Bounded/ping",
        json={"args": [], "kwargs": {}},
    )
    oversized_response = fresh_client.post(
        "/bounded/classcall/bounded.py/Bounded/raw",
        json={"args": [], "kwargs": {}},
    )

    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "BFF result byte limit exceeded"}
    assert oversized_response.status_code == 413
    assert recovered.status_code == 200
    assert recovered.json() == {"ready": True}


def test_ordinary_bff_iterables_are_bounded_before_json_conversion(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "iterables.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Iterables:
            def finite(self):
                return (number for number in range(3))

            def unbounded(self):
                def values():
                    number = 0
                    while True:
                        yield number
                        number += 1
                return values()

            def async_iterable(self):
                async def values():
                    yield 1
                return values()
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "BFF_RESULT_MAX_ITEMS", 3)
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    backend_app.reload_bff_registry(str(tmp_path))

    finite = fresh_client.post(
        "/iterables/classcall/iterables.py/Iterables/finite",
        json={"args": [], "kwargs": {}},
    )
    unbounded = fresh_client.post(
        "/iterables/classcall/iterables.py/Iterables/unbounded",
        json={"args": [], "kwargs": {}},
    )
    async_iterable = fresh_client.post(
        "/iterables/classcall/iterables.py/Iterables/async_iterable",
        json={"args": [], "kwargs": {}},
    )

    assert finite.status_code == 200
    assert finite.json() == [0, 1, 2]
    assert unbounded.status_code == 413
    assert unbounded.json() == {"detail": "BFF result item limit exceeded"}
    assert async_iterable.status_code == 413
    assert async_iterable.json() == {
        "detail": "Async BFF result iterables require an explicit streaming export"
    }


def test_class_call_uses_optional_process_isolation_when_configured(
    monkeypatch, fresh_client, tmp_path
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.execution import ProcessIsolatedBFFExecutor

    (tmp_path / "isolated.py").write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class Isolated:
            def __init__(self, _user): self.user = _user
            def ping(self): return {"isolated": True}
            @bff_stream()
            async def stream(self):
                yield "unsupported"
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "BFF_EXECUTION_MODE", "isolated-process")
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    monkeypatch.setattr(
        backend_app,
        "BFF_ISOLATED_EXECUTOR",
        ProcessIsolatedBFFExecutor(
            max_concurrency=1,
            max_per_user=1,
            cpu_seconds=2,
            memory_bytes=64 * 1024 * 1024 * 1024,
            result_max_bytes=1024,
            result_max_depth=8,
            result_max_items=100,
        ),
    )
    backend_app.reload_bff_registry(str(tmp_path))

    response = fresh_client.post(
        "/isolated/classcall/isolated.py/Isolated/ping",
        json={"args": [], "kwargs": {}},
    )
    stream_response = fresh_client.post(
        "/isolated/classcall/isolated.py/Isolated/stream",
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 200
    assert response.json() == {"isolated": True}
    assert stream_response.status_code == 501


def test_isolated_process_fairness_uses_stable_hmac_identity():
    import pytincture.backend.app as backend_app

    def request(session_id):
        return Request({
            "type": "http",
            "headers": [],
            "client": ("203.0.113.8", 50000),
            "session": {"session_id": session_id},
        })

    first_session = request("session-one")
    second_session = request("session-two")
    stable_user = {
        "is_authenticated": True,
        "auth_provider": "microsoft",
        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
        "tenant": "TENANT",
        "subject": "immutable-user-id",
        "email": "mutable@example.com",
    }
    first_key = backend_app._isolated_bff_subject(first_session, stable_user)
    second_key = backend_app._isolated_bff_subject(second_session, {
        **stable_user,
        "email": "changed@example.com",
    })

    assert first_key == second_key
    assert len(first_key) == 64
    assert "immutable-user-id" not in first_key
    assert first_key != backend_app._isolated_bff_subject(
        request("session-three"),
        {**stable_user, "subject": "different-user-id"},
    )

    local_key = backend_app._isolated_bff_subject(first_session, {
        "is_authenticated": True,
        "auth_provider": "user",
        "email": "Person@Example.com",
    })
    assert local_key == backend_app._isolated_bff_subject(second_session, {
        "is_authenticated": True,
        "auth_provider": "user",
        "email": " person@example.COM ",
    })
    unidentified_external = {
        "is_authenticated": True,
        "auth_provider": "custom-oidc",
        "issuer": "https://issuer.example",
        "email": "first@example.com",
    }
    assert backend_app._isolated_bff_subject(
        first_session,
        unidentified_external,
    ) == backend_app._isolated_bff_subject(second_session, {
        **unidentified_external,
        "email": "second@example.com",
    })


def test_multiple_sessions_share_one_isolated_process_fairness_quota():
    import pytincture.backend.app as backend_app
    from pytincture.backend.execution import (
        IsolatedExecutionRejected,
        ProcessIsolatedBFFExecutor,
    )

    executor = ProcessIsolatedBFFExecutor(
        max_concurrency=2,
        max_per_user=1,
        cpu_seconds=2,
        memory_bytes=64 * 1024 * 1024 * 1024,
        result_max_bytes=1024,
        result_max_depth=8,
        result_max_items=100,
    )
    user = {
        "is_authenticated": True,
        "auth_provider": "google",
        "issuer": "https://accounts.google.com",
        "subject": "stable-subject",
        "email": "person@example.com",
    }
    first = Request({
        "type": "http",
        "headers": [],
        "session": {"session_id": "first-session"},
    })
    second = Request({
        "type": "http",
        "headers": [],
        "session": {"session_id": "second-session"},
    })
    first_key = backend_app._isolated_bff_subject(first, user)
    second_key = backend_app._isolated_bff_subject(second, user)

    executor._acquire(first_key)
    try:
        with pytest.raises(IsolatedExecutionRejected, match="per-identity"):
            executor._acquire(second_key)
    finally:
        executor._release(first_key)


def test_timed_out_sync_bff_holds_capacity_until_worker_recovers(monkeypatch):
    import time
    from starlette.requests import Request
    import pytincture.backend.app as backend_app
    from pytincture.backend.limits import AdmissionRejected, AsyncAdmissionGate

    async def exercise():
        gate = AsyncAdmissionGate(1, 0, 0.001)
        monkeypatch.setattr(backend_app, "BFF_ADMISSION_GATE", gate)
        request = Request({"type": "http", "method": "POST", "path": "/"})
        request.state.bff_deadline = time.monotonic() + 0.001
        request.state.bff_slot_held = True
        await gate.acquire()

        with pytest.raises(HTTPException) as timed_out:
            await backend_app._run_bff_thread_stage(request, time.sleep, 0.03)
        assert timed_out.value.status_code == 504
        with pytest.raises(AdmissionRejected):
            await gate.acquire()

        deferred = request.state.bff_deferred_task
        deferred.add_done_callback(lambda _task: backend_app._release_bff_slot(request))
        await deferred
        await gate.acquire()
        gate.release()

    asyncio.run(exercise())

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
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["vary"] == "Cookie, Authorization"
    # Check that the content appears to be a zip archive (starts with PK).
    assert response.content.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        generated_app = archive.read("demoapp.py").decode("utf-8")
    assert hostile_host not in generated_app
    assert "protocol-with-" not in generated_app
    assert "url = '/demoapp/classcall/demoapp.py/Demo/ping'" in generated_app
    compile(generated_app, "demoapp.py", "exec")


def test_appcode_build_saturation_rejects_then_recovers(
    fresh_client, monkeypatch, tmp_path
):
    import threading
    import pytincture.backend.app as backend_app

    (tmp_path / "demoapp.py").write_text("value = 1\n", encoding="utf-8")
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "require_auth", lambda request: "noauth")
    monkeypatch.setattr(backend_app, "APPCODE_BUILD_GATE", gate)
    monkeypatch.setattr(backend_app, "APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS", 0.001)

    saturated = fresh_client.get("/demoapp/appcode/appcode.pyt")
    assert saturated.status_code == 503
    gate.release()
    recovered = fresh_client.get("/demoapp/appcode/appcode.pyt")
    assert recovered.status_code == 200

def test_frontend_runtime_cache_busts_packaged_app_fetch(fresh_client):
    """
    The packaged app fetch should include the server instance uuid query parameter.
    """
    response = fresh_client.get("/frontend/pytincture.js")
    assert response.status_code == 200
    assert "installCacheBustingFetch" not in response.text
    assert "globalThis.fetch =" not in response.text
    assert 'withRequestUuid(`${config.application}/appcode/appcode.pyt`, config.requestUuid)' in response.text
    assert "`${config.pyodideBaseUrl}pyodide.asm.js`" in response.text
    assert 'config.pyodideScriptIntegrity?.["pyodide.asm.js"]' in response.text
    assert "withSameOriginRequestUuid(url, requestUuid)" in response.text
    assert "cache_bust_url(cleaned)" not in response.text


def test_frontend_runtime_cache_busts_only_backend_micropip_installs(fresh_client):
    response = fresh_client.get("/frontend/pytincture.js")

    assert response.status_code == 200
    assert "cacheBustingSuspensionDepth" not in response.text
    assert "withoutCacheBusting" not in response.text
    assert "activeRequestUuid" not in response.text
    assert "requestUuid ? withRequestUuid(source, requestUuid) : source" in response.text
    assert "await installWidgetsetSource(pyodide, primarySource);" in response.text
    assert "await installWidgetsetSource(pyodide, builtinLockedSource);" in response.text
    assert "await installWidgetsetSource(pyodide, lockedSource, config.requestUuid);" in response.text
    assert "#sha256=${backendWheel.sha256}" in response.text


def test_frontend_runtime_resolves_versioned_wheels_and_sends_log_csrf(fresh_client):
    response = fresh_client.get("/frontend/pytincture.js")
    assert response.status_code == 200
    assert "candidateVersions.push(pinnedMatch[1])" in response.text
    assert "candidateVersions.push(config.devWheelVersion)" in response.text
    assert response.text.index("candidateVersions.push(pinnedMatch[1])") < response.text.index(
        "candidateVersions.push(config.devWheelVersion)"
    )
    assert response.text.index("const backendWheel = await probeBackendWheel(source)") < response.text.index(
        "await installWidgetsetSource(pyodide, lockedSource, config.requestUuid)"
    )
    assert response.text.index("const backendSources = await resolveBackendWidgetSources(config)") < response.text.index(
        "const builtinLockedSource = BUILTIN_WIDGET_WHEEL_LOCKS[primarySource]"
    )
    assert response.text.index("const backendSources = await resolveBackendWidgetSources(config)") < response.text.index(
        "if (config.allowPublicWidgetIndex)"
    )
    assert "No trusted backend wheel is available" in response.text
    assert "x-pytincture-sha256" in response.text
    assert "throw lastInstallError" in response.text
    assert 'CSRF_COOKIE_NAMES.includes(name)' in response.text
    assert 'headers["X-CSRF-Token"] = csrfToken' in response.text

def test_service_worker_only_caches_manifested_framework_assets(fresh_client):
    response = fresh_client.get("/frontend/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "FRAMEWORK_ASSET_PATHS" in response.text
    assert '"vendor/materialdesignicons/materialdesignicons.css"' in response.text
    assert '"pyodide/0.29.3/full/pyodide.js"' in response.text
    assert 'canonicalUrl.searchParams.set("uuid", REQUEST_UUID)' in response.text
    assert 'url.pathname.includes("/appcode/")' not in response.text
    assert "CACHEABLE_EXTENSIONS" not in response.text
    assert "caches.delete" not in response.text
    assert 'credentials: "omit"' in response.text


def test_application_service_worker_is_limited_to_its_frontend_scope(fresh_client):
    response = fresh_client.get("/demoapp/frontend/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/demoapp/"
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_service_worker_bytes_change_with_cache_namespace(fresh_client):
    first = fresh_client.get("/demoapp/frontend/sw.js?uuid=instance-a&release=rc1")
    repeated = fresh_client.get("/demoapp/frontend/sw.js?uuid=instance-a&release=rc1")
    restarted = fresh_client.get("/demoapp/frontend/sw.js?uuid=instance-b&release=rc1")
    upgraded = fresh_client.get("/demoapp/frontend/sw.js?uuid=instance-a&release=rc2")

    assert first.content == repeated.content
    assert first.content != restarted.content
    assert first.content != upgraded.content


def test_authenticated_frontend_assets_are_public_and_do_not_rotate_session(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setenv("ALLOWED_EMAILS", "asset-user@example.com")
    login = _password_login(
        fresh_client,
        email="asset-user@example.com",
        password="unused",
        follow_redirects=False,
    )
    assert login.status_code == 303

    asset = fresh_client.get("/demoapp/frontend/pytincture.js?uuid=test-instance")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "set-cookie" not in asset.headers


def test_health_and_readiness_endpoints(fresh_client):
    from pytincture import __version__

    health = fresh_client.get("/healthz", headers={"X-Request-ID": "health-check-1"})
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": __version__}
    assert health.headers["X-Request-ID"] == "health-check-1"

    edge_probe = fresh_client.get(
        "/_pytincture/edge-client",
        headers={"X-Forwarded-For": "198.51.100.77"},
    )
    assert edge_probe.status_code == 200
    assert edge_probe.json() == {"client_host": "127.0.0.1"}
    assert edge_probe.headers["cache-control"] == "private, no-store, max-age=0"
    assert edge_probe.headers["vary"] == "Forwarded, X-Forwarded-For"

    readiness = fresh_client.get("/readyz")
    assert readiness.status_code == 200
    assert readiness.json()["status"] == "ready"
    assert all(readiness.json()["checks"].values())


def test_diagnostic_details_can_be_minimal_or_operator_only(tmp_path):
    from pytincture.configuration import PytinctureConfig
    from pytincture.factory import create_app

    token = "operator-token-with-32-distinct-ish-characters-1234"
    minimal = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            diagnostic_details_mode="minimal",
        )
    )
    with TestClient(minimal) as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")
        assert health.json() == {"status": "ok"}
        assert readiness.json() == {"status": "ready"}
        assert health.headers["cache-control"] == "private, no-store, max-age=0"

    operator = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            diagnostic_details_mode="operator",
            diagnostic_operator_token=token,
        )
    )
    with TestClient(operator) as client:
        anonymous_health = client.get("/healthz")
        wrong_readiness = client.get(
            "/readyz", headers={"Authorization": "Bearer incorrect"}
        )
        detailed_health = client.get(
            "/healthz", headers={"Authorization": f"Bearer {token}"}
        )
        detailed_readiness = client.get(
            "/readyz", headers={"Authorization": f"Bearer {token}"}
        )

        assert anonymous_health.json() == {"status": "ok"}
        assert wrong_readiness.json() == {"status": "ready"}
        assert detailed_health.json()["version"]
        assert all(detailed_readiness.json()["checks"].values())
        assert anonymous_health.headers["vary"] == "Authorization"
        assert detailed_readiness.headers["cache-control"] == (
            "private, no-store, max-age=0"
        )


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


def test_readiness_checks_are_offloaded_coalesced_and_briefly_cached(monkeypatch):
    import pytincture.backend.app as backend_app

    calls = []
    entered = threading.Event()
    release = threading.Event()

    def slow_readiness(*_args):
        calls.append(time.monotonic())
        entered.set()
        assert release.wait(1.0)
        return True, {"modules_path": True}

    monkeypatch.setattr(backend_app, "USE_REDIS_INSTANCE", "false")
    monkeypatch.setattr(backend_app, "readiness_report", slow_readiness)
    monkeypatch.setattr(backend_app, "READINESS_CACHE_TTL_SECONDS", 10.0)
    monkeypatch.setattr(backend_app, "_READINESS_CACHE_VALUE", None)
    monkeypatch.setattr(backend_app, "_READINESS_CACHE_LOCK", asyncio.Lock())
    monkeypatch.setattr(
        backend_app,
        "REMOTE_STORE_GATE",
        backend_app.AsyncAdmissionGate(2, 2, 0.1),
    )

    async def exercise():
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/readyz",
                "headers": [],
            }
        )
        pending = [
            asyncio.create_task(backend_app.readiness_check(request))
            for _ in range(5)
        ]
        while not entered.is_set():
            await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(*pending)
        cached = await backend_app.readiness_check(request)
        return responses, cached

    responses, cached = asyncio.run(exercise())
    assert len(calls) == 1
    assert all(response.status_code == 200 for response in responses)
    assert cached.status_code == 200

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
    (static_dir / "dummywidget.py").write_text(
        '__widgetset__ = "widget_value"\n__version__ = "1.0"\n',
        encoding="utf-8",
    )
    result = get_widgetset("testapp", str(static_dir))
    assert result == "widget_value==1.0"

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


def test_logs_endpoint(fresh_client, monkeypatch, caplog):
    """
    Test the /logs endpoint.
    """
    import pytincture.backend.app as backend_app
    monkeypatch.setattr(backend_app, "require_auth", lambda req: {"email": "dummy@example.com"})
    with caplog.at_level("INFO", logger="pytincture.backend.app"):
        response = fresh_client.post(
            "/logs",
            json={
                "level": "info",
                "message": "test log secret=must-not-cross-the-log-boundary",
                "timestamp": "2026-08-31T12:00:00Z",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert set(response.headers["vary"].split(", ")) == {
        "Cookie",
        "Authorization",
    }
    assert "must-not-cross-the-log-boundary" not in caplog.text


@pytest.mark.parametrize(
    "body",
    (
        b'{"level":"notice","message":"x","timestamp":"2026-08-31T12:00:00Z"}',
        b'{"level":"info","message":"x","timestamp":"2026-08-31T12:00:00"}',
        b'{"level":"info","message":"x","timestamp":123}',
        b'{"level":"info","message":"x","timestamp":"2026-08-31T12:00:00Z","extra":true}',
        b'{"level":"info","level":"error","message":"x","timestamp":"2026-08-31T12:00:00Z"}',
    ),
)
def test_logs_endpoint_rejects_noncanonical_payloads(
    fresh_client, monkeypatch, body
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(
        backend_app,
        "require_auth",
        lambda _request: {"email": "dummy@example.com"},
    )
    response = fresh_client.post(
        "/logs",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_logs_endpoint_has_dedicated_size_and_rate_limits(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(
        backend_app,
        "require_auth",
        lambda _request: {"email": "dummy@example.com"},
    )
    monkeypatch.setattr(backend_app, "BROWSER_LOG_MAX_BYTES", 64)
    oversized = fresh_client.post(
        "/logs",
        content=b'{"message":"' + (b"x" * 100) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413

    monkeypatch.setattr(backend_app, "BROWSER_LOG_MAX_BYTES", 4096)
    monkeypatch.setattr(
        backend_app,
        "BROWSER_LOG_RATE_LIMITER",
        backend_app.SlidingWindowRateLimiter(1, 60),
    )
    payload = {
        "level": "info",
        "message": "bounded",
        "timestamp": "2026-08-31T12:00:00Z",
    }
    assert fresh_client.post("/logs", json=payload).status_code == 200
    rejected = fresh_client.post("/logs", json=payload)
    assert rejected.status_code == 429
    assert int(rejected.headers["retry-after"]) >= 1


def test_noauth_browser_logging_requires_explicit_opt_in(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    for name in (
        "ENABLE_GOOGLE_AUTH",
        "ENABLE_MICROSOFT_AUTH",
        "ENABLE_USER_LOGIN",
        "ENABLE_SAML_AUTH",
    ):
        monkeypatch.setattr(backend_app, name, False)
    monkeypatch.setattr(backend_app, "ALLOW_NOAUTH_BROWSER_LOGS", False)
    payload = {
        "level": "info",
        "message": "bounded",
        "timestamp": "2026-08-31T12:00:00Z",
    }
    assert backend_app._browser_logging_available() is False
    assert fresh_client.post("/logs", json=payload).status_code == 404

    monkeypatch.setattr(backend_app, "ALLOW_NOAUTH_BROWSER_LOGS", True)
    assert backend_app._browser_logging_available() is True
    assert fresh_client.post("/logs", json=payload).status_code == 200


def test_require_auth_does_not_print_debug_output(monkeypatch, capsys):
    """Successful session validation should not write authentication details to stdout."""
    import pytincture.backend.app as backend_app

    user = backend_app._build_auth_session_user({"email": "quiet@example.com"})
    request = type("Request", (), {
        "session": {
            "user": user,
            "session_id": "test-session",
            "auth_issued_at": __import__("time").time(),
        }
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

    response = _password_login(
        fresh_client,
        email="person@example.com",
        password="do-not-store",
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


def test_authenticated_identity_enforces_claim_count_and_total_size(monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setenv(
        "AUTH_SESSION_CLAIM_KEYS",
        ",".join(f"custom_{index}" for index in range(20)),
    )
    monkeypatch.setattr(backend_app, "AUTH_SESSION_MAX_CLAIM_COUNT", 12)
    with pytest.raises(HTTPException, match="session limits"):
        backend_app._build_auth_session_user(
            {
                "email": "person@example.com",
                **{f"custom_{index}": index for index in range(20)},
            }
        )

    monkeypatch.setattr(backend_app, "AUTH_SESSION_MAX_CLAIM_COUNT", 32)
    monkeypatch.setattr(backend_app, "AUTH_SESSION_MAX_IDENTITY_BYTES", 256)
    with pytest.raises(HTTPException, match="session limits"):
        backend_app._build_auth_session_user(
            {"email": "person@example.com", "name": "x" * 512}
        )


def test_authenticated_session_enforces_signed_cookie_envelope(monkeypatch):
    import pytincture.backend.app as backend_app

    request = type("Request", (), {"session": {"return_to": "/demoapp"}})()
    original_session = dict(request.session)
    monkeypatch.setattr(backend_app, "AUTH_SESSION_MAX_COOKIE_BYTES", 128)

    with pytest.raises(HTTPException, match="session limits"):
        backend_app._set_authenticated_user(
            request,
            {"email": "person@example.com"},
            application="demoapp",
            auth_type="user",
        )

    assert request.session == original_session


def test_oversized_authenticator_claims_fail_login_without_session(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    backend_app.set_user_authenticator(
        lambda **_kwargs: {"name": "x" * 4096}
    )

    response = _password_login(
        fresh_client,
        email="person@example.com",
        password="verified",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert fresh_client.cookies.get(_SESSION_COOKIE) is None


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

    first_login = _password_login(
        fresh_client,
        email="person@example.com",
        password="first",
        follow_redirects=False,
    )
    assert first_login.status_code == 303

    with TestClient(
        app, base_url="https://127.0.0.1", client=("127.0.0.1", 50001)
    ) as second_browser, TestClient(
        app, base_url="https://127.0.0.1", client=("127.0.0.1", 50002)
    ) as another_replica:
        second_login = _password_login(
            second_browser,
            email="person@example.com",
            password="second",
            follow_redirects=False,
        )
        assert second_login.status_code == 303
        second_cookie = second_browser.cookies.get(_SESSION_COOKIE)
        second_csrf_cookie = second_browser.cookies.get(_CSRF_COOKIE)
        assert second_cookie
        assert second_csrf_cookie

        backend_app.USER_SESSION_DICT["person@example.com"] = {
            "email": "person@example.com",
            "stale": True,
        }
        fresh_client.post(
            "/demoapp/auth/logout",
            headers=_csrf_headers(fresh_client),
            follow_redirects=False,
        )

        another_replica.cookies.set(_SESSION_COOKIE, second_cookie)
        another_replica.cookies.set(_CSRF_COOKIE, second_csrf_cookie)
        response = another_replica.post(
            "/demoapp/classcall/example.py/ExampleClass/testfunc",
            json={"args": [], "kwargs": {"source": "replica"}},
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

    _password_login(
        fresh_client,
        email="person@example.com",
        password="secret",
        follow_redirects=False,
    )
    valid_cookie = fresh_client.cookies.get(_SESSION_COOKIE)
    assert valid_cookie

    fresh_client.cookies.clear()
    fresh_client.cookies.set(_SESSION_COOKIE, _tamper_token(valid_cookie))
    tampered_response = fresh_client.get(
        "/demoapp/classcall/example.py/ExampleClass/testfunc"
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
    fresh_client.cookies.set(_SESSION_COOKIE, expired_cookie)
    expired_response = fresh_client.get(
        "/demoapp/classcall/example.py/ExampleClass/testfunc"
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
        fresh_client.cookies[
            backend_app._saml_handshake_cookie_name("demoapp")
        ],
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
    assert backend_app._saml_handshake_cookie_name("demoapp").startswith("__Host-")
    assert (
        backend_app._saml_handshake_cookie_name("demoapp")
        != backend_app._saml_handshake_cookie_name("anotherapp")
    )
    assert "path=/" in set_cookie
    assert "domain=" not in set_cookie
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
        validation_thread_id = None

        def get_settings(self):
            return FakeSettings()

        def process_response(self, request_id=None):
            self.processed_request_id = request_id
            self.validation_thread_id = threading.get_ident()

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

        def get_last_response_xml(self):
            return """<samlp:Response
              xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
              xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
              xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
              InResponseTo="ONELOGIN_original_request">
              <ds:Signature/>
              <saml:Assertion/>
            </samlp:Response>"""

        def get_session_expiration(self):
            return time.time() + 120

        def get_last_assertion_not_on_or_after(self):
            return time.time() + 90

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
    acs_thread_ids = []
    original_load_relay_state = backend_app._load_saml_relay_state

    def tracked_load_relay_state(token):
        acs_thread_ids.append(threading.get_ident())
        return original_load_relay_state(token)

    monkeypatch.setattr(
        backend_app,
        "_load_saml_relay_state",
        tracked_load_relay_state,
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
        backend_app._saml_handshake_cookie_name("demoapp"),
        handshake_cookie,
        path=backend_app._saml_handshake_cookie_path("demoapp"),
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
    assert fake_saml_auth.validation_thread_id != acs_thread_ids[0]
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
    assert 0 < session_data["auth_expires_at"] - time.time() <= 90
    assert len(session_data["saml_replay_proof"]) == 64
    assert "ONELOGIN_response" not in fresh_client.cookies[_SESSION_COOKIE]
    assert "ONELOGIN_assertion" not in fresh_client.cookies[_SESSION_COOKIE]
    assert any(
        backend_app._saml_handshake_cookie_name("demoapp") in value
        and "Max-Age=0" in value
        for value in response.headers.get_list("set-cookie")
    )
    session_cookie_header = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{_SESSION_COOKIE}=")
    )
    assert "Max-Age=90" in session_cookie_header or "Max-Age=89" in session_cookie_header
    # Even a raw client that restores the consumed handshake cookie is rejected
    # by the replay proof carried in the signed browser session.
    fresh_client.cookies.set(
        backend_app._saml_handshake_cookie_name("demoapp"),
        handshake_cookie,
        path=backend_app._saml_handshake_cookie_path("demoapp"),
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
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
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
        backend_app._saml_handshake_cookie_name("demoapp"),
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
        path=backend_app._saml_handshake_cookie_path("demoapp"),
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
        backend_app._saml_handshake_cookie_name("demoapp"),
        backend_app._get_saml_handshake_cookie_serializer().dumps(record),
        path=backend_app._saml_handshake_cookie_path("demoapp"),
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


def test_saml_acs_rejects_encrypted_assertions_before_toolkit(
    fresh_client,
    monkeypatch,
):
    import pytincture.backend.app as backend_app
    from pytincture.backend.saml import SlidingWindowRateLimiter

    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", True)
    monkeypatch.setattr(
        backend_app,
        "SAML_ACS_RATE_LIMITER",
        SlidingWindowRateLimiter(2, 60),
    )
    toolkit_called = False

    def fail_if_toolkit_is_called(*args, **kwargs):
        nonlocal toolkit_called
        toolkit_called = True
        raise AssertionError("encrypted XML reached the SAML toolkit")

    monkeypatch.setattr(backend_app, "_init_saml_auth", fail_if_toolkit_is_called)
    encrypted_response = (
        Path(__file__).parent / "fixtures" / "saml" / "encrypted-assertion.xml"
    ).read_bytes()

    rejected = fresh_client.post(
        "/demoapp/auth/saml/acs",
        data={
            "SAMLResponse": base64.b64encode(encrypted_response).decode("ascii"),
            "RelayState": "preflight-rejects-before-state",
        },
    )

    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "Invalid SAML response"}
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


def test_microsoft_oauth_requests_only_consumed_identity_scopes():
    import pytincture.backend.app as backend_app

    assert set(backend_app._MICROSOFT_OIDC_SCOPES.split()) == {
        "openid",
        "email",
        "profile",
    }
    assert "offline_access" not in backend_app._MICROSOFT_OIDC_SCOPES


@pytest.mark.parametrize(
    "dead_helper",
    (
        "_strip_pem_headers",
        "_certificate_fingerprint",
        "_extract_response_certificates",
    ),
)
def test_dead_certificate_xml_helpers_are_not_exposed(dead_helper):
    import pytincture.backend.app as backend_app

    assert not hasattr(backend_app, dead_helper)


def test_microsoft_login_stores_only_compact_stateless_claims(
    fresh_client,
    monkeypatch,
    tmp_path,
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
                    "tid": "tenant-123",
                    "iss": "https://login.microsoftonline.com/tenant-123/v2.0",
                    "sub": "subject-123",
                    "oid": "object-456",
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
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "tenant-123")
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("# demo app\n", encoding="utf-8")

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
        "issuer": "https://login.microsoftonline.com/tenant-123/v2.0",
        "subject": "subject-123",
        "tenant": "tenant-123",
        "oid": "object-456",
        "application": "demoapp",
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
    response = _password_login(
        fresh_client,
        email="test@example.com",
        password="secret",
        follow_redirects=False   # Prevent auto-following the redirect
    )
    # Now the response should be the raw RedirectResponse with status 303.
    assert response.status_code == 303
    assert "/demoapp" in response.headers.get("location", "")


def test_authenticated_session_has_absolute_lifetime(
    fresh_client, monkeypatch, dummy_module
):
    import time
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")
    monkeypatch.setenv("MODULES_PATH", str(dummy_module))
    assert _password_login(
        fresh_client,
        email="person@example.com",
        password="password",
        follow_redirects=False,
    ).status_code == 303
    session_data = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    assert isinstance(session_data["auth_issued_at"], int)
    session_data["auth_issued_at"] = (
        time.time() - backend_app.AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS - 1
    )
    encoded = base64.b64encode(json.dumps(session_data).encode("utf-8"))
    expired_absolute_cookie = TimestampSigner(
        backend_app.SAML_SECRET_KEY
    ).sign(encoded).decode("utf-8")
    fresh_client.cookies.set(_SESSION_COOKIE, expired_absolute_cookie)
    response = fresh_client.post(
        "/demoapp/classcall/example.py/ExampleClass/testfunc",
        json={"args": [], "kwargs": {}},
        headers={"X-CSRF-Token": fresh_client.cookies[_CSRF_COOKIE]},
    )
    assert response.status_code == 401


def test_browser_login_and_logout_have_csrf_and_origin_protection(
    fresh_client, monkeypatch
):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_DEV_EMAIL_LOGIN", True)
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)
    monkeypatch.setenv("ALLOWED_EMAILS", "person@example.com")

    login_page = fresh_client.get("/demoapp/login")
    assert 'name="login_csrf_token"' in login_page.text
    missing_token = fresh_client.post(
        "/demoapp/auth/user",
        data={"email": "person@example.com", "password": "password"},
        follow_redirects=False,
    )
    assert missing_token.status_code == 403
    login_page = fresh_client.get("/demoapp/login")
    token = re.search(
        r'name="login_csrf_token" value="([^"]+)"', login_page.text
    ).group(1)
    cross_origin = fresh_client.post(
        "/demoapp/auth/user",
        data={
            "email": "person@example.com",
            "password": "password",
            "login_csrf_token": token,
        },
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert cross_origin.status_code == 403

    assert fresh_client.post(
        "/demoapp/auth/user",
        data={
            "email": "person@example.com",
            "password": "password",
            "login_csrf_token": token,
        },
        follow_redirects=False,
    ).status_code == 303
    assert fresh_client.post(
        "/demoapp/auth/user",
        data={
            "email": "person@example.com",
            "password": "password",
            "login_csrf_token": token,
        },
        follow_redirects=False,
    ).status_code == 403
    assert fresh_client.get("/demoapp/auth/logout").status_code == 405
    assert fresh_client.post("/demoapp/auth/logout").status_code == 403
    assert fresh_client.post(
        "/demoapp/auth/logout",
        headers=_csrf_headers(fresh_client),
        follow_redirects=False,
    ).status_code == 302


def test_security_headers_and_static_manifest(fresh_client):
    response = fresh_client.get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "cdnjs.cloudflare.com" not in response.headers["content-security-policy"]
    assert "camera=()" in response.headers["permissions-policy"]
    assert fresh_client.get("/frontend/pytincture.js").status_code == 200
    assert fresh_client.get(
        "/frontend/vendor/materialdesignicons/materialdesignicons.css"
    ).status_code == 200
    assert fresh_client.get(
        "/frontend/vendor/materialdesignicons/fonts/materialdesignicons-webfont.woff2"
    ).status_code == 200
    assert fresh_client.get(
        f"/frontend/integrity/pytincture-{__version__}.json"
    ).status_code == 200
    assert fresh_client.get("/frontend/README.md").status_code == 404
    assert fresh_client.get("/frontend/package.json").status_code == 404
    assert fresh_client.get("/frontend/browser-tests/lifecycle.spec.mjs").status_code == 404


def test_disabled_google_routes_return_404(fresh_client, monkeypatch):
    import pytincture.backend.app as backend_app

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "oauth", None)
    assert fresh_client.get("/demoapp/auth/google").status_code == 404
    assert fresh_client.get("/demoapp/auth/google/callback").status_code == 404


def test_google_callback_requires_verified_immutable_identity(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    class FakeGoogle:
        def __init__(self, verified):
            self.verified = verified

        async def authorize_access_token(self, request):
            return {"userinfo": {
                "email": "person@example.com",
                "email_verified": self.verified,
                "iss": "https://accounts.google.com",
                "sub": "google-subject",
            }}

    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("# demo app\n", encoding="utf-8")
    monkeypatch.setattr(backend_app, "oauth", type("OAuth", (), {"google": FakeGoogle(False)})())
    assert fresh_client.get(
        "/demoapp/auth/google/callback", follow_redirects=False
    ).status_code == 401
    monkeypatch.setattr(backend_app, "oauth", type("OAuth", (), {"google": FakeGoogle(True)})())
    response = fresh_client.get("/demoapp/auth/google/callback", follow_redirects=False)
    assert response.status_code in {302, 307}
    session = _decode_session_cookie(fresh_client, backend_app.SAML_SECRET_KEY)
    assert session["user"]["issuer"] == "https://accounts.google.com"
    assert session["user"]["subject"] == "google-subject"


def test_oauth_initiation_rejects_missing_app_before_provider_redirect(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    calls = []

    class FakeGoogle:
        async def authorize_redirect(self, request, redirect_uri):
            calls.append(redirect_uri)
            return RedirectResponse(redirect_uri)

    class AllowingLimiter:
        def allow(self, _key):
            return True, 0

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(
        backend_app, "oauth", type("OAuth", (), {"google": FakeGoogle()})()
    )
    monkeypatch.setattr(
        backend_app, "OAUTH_INITIATION_RATE_LIMITER", AllowingLimiter()
    )

    response = fresh_client.get("/missing/auth/google", follow_redirects=False)

    assert response.status_code == 404
    assert calls == []


def test_oauth_callback_rate_limit_runs_before_provider_exchange(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    calls = []

    class FakeGoogle:
        async def authorize_access_token(self, request):
            calls.append(request)
            return {}

    class RejectingLimiter:
        def allow(self, _key):
            return False, 7

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("# demo app\n", encoding="utf-8")
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(
        backend_app, "oauth", type("OAuth", (), {"google": FakeGoogle()})()
    )
    monkeypatch.setattr(
        backend_app, "OAUTH_CALLBACK_RATE_LIMITER", RejectingLimiter()
    )

    response = fresh_client.get(
        "/demoapp/auth/google/callback", follow_redirects=False
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert calls == []


def test_oauth_exchange_timeout_releases_admission(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    class FakeGoogle:
        async def authorize_access_token(self, request):
            await asyncio.Event().wait()

    class AllowingLimiter:
        def allow(self, _key):
            return True, 0

    class RecordingGate:
        def __init__(self):
            self.acquired = 0
            self.released = 0

        async def acquire(self):
            self.acquired += 1

        def release(self):
            self.released += 1

    gate = RecordingGate()
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    (tmp_path / "demoapp.py").write_text("# demo app\n", encoding="utf-8")
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", True)
    monkeypatch.setattr(
        backend_app, "oauth", type("OAuth", (), {"google": FakeGoogle()})()
    )
    monkeypatch.setattr(
        backend_app, "OAUTH_CALLBACK_RATE_LIMITER", AllowingLimiter()
    )
    monkeypatch.setattr(backend_app, "OAUTH_EXCHANGE_GATE", gate)
    monkeypatch.setattr(backend_app, "OAUTH_EXCHANGE_TIMEOUT_SECONDS", 0.01)

    response = fresh_client.get(
        "/demoapp/auth/google/callback", follow_redirects=False
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert gate.acquired == 1
    assert gate.released == 1


def test_oauth_http_client_has_explicit_phase_deadlines():
    import pytincture.backend.app as backend_app

    kwargs = backend_app._oauth_client_kwargs("openid")
    timeout = kwargs["timeout"]
    assert kwargs["scope"] == "openid"
    assert timeout.connect == backend_app.OAUTH_CONNECT_TIMEOUT_SECONDS
    assert timeout.read == backend_app.OAUTH_READ_TIMEOUT_SECONDS
    assert timeout.write == backend_app.OAUTH_WRITE_TIMEOUT_SECONDS
    assert timeout.pool == backend_app.OAUTH_POOL_TIMEOUT_SECONDS

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
    (dummy_modules / "dummywidget.py").write_text(
        '__widgetset__ = "widgetset_val"\n__version__ = "3.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MODULES_PATH", str(dummy_modules))
    response = fresh_client.get("/demoapp")
    assert response.status_code == 200
    html = response.text
    # Check that placeholders are replaced.
    assert "***APPLICATION***" not in html
    assert "widgetset_val==3.0" in html


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
    assert "enableBackendLogging: false" in first_response.text
    assert first_response.headers["cache-control"] == "no-store, max-age=0"
    assert first_response.headers["pragma"] == "no-cache"


def test_page_and_appcode_requests_never_execute_browser_module(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    executed = tmp_path / "server-executed.txt"
    (tmp_path / "safeapp.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(executed)!r}).write_text('executed')\n"
        "from dhxpyt.layout import MainWindow as Window\n"
        "class BrowserOnly(Window):\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    page = fresh_client.get("/safeapp")
    archive = fresh_client.get("/safeapp/appcode/appcode.pyt")

    assert page.status_code == 200
    assert 'entrypoint: "BrowserOnly"' in page.text
    assert archive.status_code == 200
    assert not executed.exists()


def test_main_page_reports_dynamic_entrypoint_metadata(
    fresh_client, monkeypatch, tmp_path
):
    import pytincture.backend.app as backend_app

    (tmp_path / "dynamicapp.py").write_text(
        "APP_ENTRYPOINT = choose_entrypoint()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    monkeypatch.setattr(backend_app, "ENABLE_GOOGLE_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_MICROSOFT_AUTH", False)
    monkeypatch.setattr(backend_app, "ENABLE_USER_LOGIN", False)
    monkeypatch.setattr(backend_app, "ENABLE_SAML_AUTH", False)

    response = fresh_client.get("/dynamicapp")

    assert response.status_code == 422
    assert "APP_ENTRYPOINT must be a literal" in response.json()["detail"]


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
    modules_root = tmp_path / "modules"
    modules_root.mkdir()
    (modules_root / "demoapp.py").write_text("# demo application\n")
    favicon_root = tmp_path / "favicons"
    app_favicon_folder = favicon_root / "demoapp"
    app_favicon_folder.mkdir(parents=True)
    (favicon_root / "favicon.ico").write_bytes(b"shared-icon")
    (app_favicon_folder / "favicon.ico").write_bytes(b"demo-icon")
    (app_favicon_folder / "safari-pinned-tab.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    )
    monkeypatch.setenv("MODULES_PATH", str(modules_root))
    monkeypatch.setenv("PYTINCTURE_FAVICON_FOLDER", str(favicon_root))

    response = fresh_client.get("/demoapp/favicon-assets/favicon.ico")

    assert response.status_code == 200
    assert response.content == b"demo-icon"
    assert fresh_client.get("/missing/favicon-assets/favicon.ico").status_code == 404
    svg = fresh_client.get("/demoapp/favicon-assets/safari-pinned-tab.svg")
    assert svg.status_code == 200
    assert svg.headers["content-security-policy"].startswith(
        "sandbox; default-src 'none'"
    )


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
    assert settings["security"]["rejectDeprecatedAlgorithm"] is True
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
