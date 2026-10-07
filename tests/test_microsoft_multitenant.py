import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from pytincture import PytinctureConfig, create_app
from pytincture.backend.microsoft import (
    MICROSOFT_CONSUMER_TENANT,
    MICROSOFT_ISSUER_TEMPLATE,
)


TENANT_A = "11111111-2222-3333-4444-555555555555"
TENANT_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _signed_token(key, claims, kid="test-key"):
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    message = ".".join(_b64(json.dumps(part).encode()) for part in (header, claims))
    signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{message}.{_b64(signature)}"


def _jwk(key, issuer, kid="test-key"):
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        **({"issuer": issuer} if issuer is not None else {}),
    }


def _config(tmp_path, **overrides):
    (tmp_path / "demo.py").write_text("# Microsoft login test\n")
    return PytinctureConfig(
        modules_path=str(tmp_path), enable_microsoft_auth=True,
        microsoft_client_id="platform-client", microsoft_client_secret="test-secret",
        session_secret="production-test-secret-at-least-32-bytes",
        allowed_hosts=("service.example",), canonical_origin="https://service.example",
        **{"microsoft_tenant_id": "organizations", "microsoft_allow_multitenant": True,
           **overrides},
    )


def _login(app, key, *, tenant=TENANT_A, changes=None,
           key_issuer=MICROSOFT_ISSUER_TEMPLATE, token_key=None, bad_state=False,
           missing_id_token=False, rotate=False):
    backend = app.state.pytincture_backend
    provider = backend.oauth.microsoft
    issued = {}
    key_fetches = []

    def provider_response(request):
        # Never fetch URLs selected by a token or supplied by a user.
        assert request.url.host == "login.microsoftonline.com"
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": MICROSOFT_ISSUER_TEMPLATE if provider._server_metadata_url.find("/organizations/") >= 0
                else MICROSOFT_ISSUER_TEMPLATE.format(tenantid=tenant),
                "authorization_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
                "token_endpoint": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
                "jwks_uri": "https://login.microsoftonline.com/organizations/discovery/v2.0/keys",
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            })
        if path.endswith("/keys"):
            key_fetches.append(True)
            return httpx.Response(200, json={"keys": [_jwk(key, key_issuer)]})
        assert path.endswith("/token")
        assert request.method == "POST"
        claims = {
            "iss": MICROSOFT_ISSUER_TEMPLATE.format(tenantid=tenant),
            "tid": tenant, "sub": "same-subject", "oid": "same-object",
            "aud": "platform-client", "iat": int(time.time()),
            "exp": int(time.time()) + 300, "nonce": issued["nonce"],
            "email": "person@example.com", "name": "Example Person",
            "at_hash": _b64(hashlib.sha256(b"test-access-token").digest()[:16]),
        }
        claims.update(changes or {})
        claims = {key: value for key, value in claims.items() if value is not None}
        payload = {"access_token": "test-access-token", "token_type": "Bearer"}
        if not missing_id_token:
            payload["id_token"] = _signed_token(token_key or key, claims)
        return httpx.Response(200, json=payload)

    provider.client_kwargs["transport"] = httpx.MockTransport(provider_response)
    if rotate:
        provider.server_metadata["jwks"] = {"keys": [_jwk(key, key_issuer, "old-key")]}
    with TestClient(app, base_url="https://service.example") as client:
        start = client.get("/demo/auth/microsoft", follow_redirects=False)
        assert start.status_code == 302
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["redirect_uri"] == ["https://service.example/demo/auth/microsoft/callback"]
        assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
        issued["nonce"] = query["nonce"][0]
        state = "wrong-state" if bad_state else query["state"][0]
        callback = "/demo/auth/microsoft/callback?" + urlencode({"state": state, "code": "test-code"})
        response = client.get(callback, follow_redirects=False)
        cookie = client.cookies.get(backend._SESSION_COOKIE)
        session = json.loads(base64.b64decode(TimestampSigner(backend.SAML_SECRET_KEY).unsign(cookie))) if cookie else {}
        return response, session, key_fetches


def test_multi_organization_login_preserves_tenant_scoped_identity(tmp_path, signing_key):
    app = create_app(_config(tmp_path))
    assert "/organizations/" in app.state.pytincture_backend.oauth.microsoft._server_metadata_url
    users = []
    for tenant in (TENANT_A, TENANT_B):
        response, session, _ = _login(app, signing_key, tenant=tenant)
        assert response.status_code == 307
        user = session["user"]
        assert user["tenant"] == tenant
        assert user["issuer"] == MICROSOFT_ISSUER_TEMPLATE.format(tenantid=tenant)
        assert user["subject"] == "same-subject"
        assert user["oid"] == "same-object"
        assert "test-access-token" not in json.dumps(session)
        assert "id_token" not in session
        users.append(user)
    assert (users[0]["issuer"], users[0]["subject"]) != (users[1]["issuer"], users[1]["subject"])


@pytest.mark.parametrize("changes", [
    {"iss": "https://evil.example/tenant/v2.0"},
    {"iss": MICROSOFT_ISSUER_TEMPLATE.format(tenantid=TENANT_B)},
    {"iss": MICROSOFT_ISSUER_TEMPLATE.format(tenantid=TENANT_A) + "/"},
    {"tid": "not-a-guid"}, {"tid": "../other"}, {"tid": None},
    {"sub": None}, {"aud": "another-platform"}, {"aud": None},
    {"nonce": "wrong-nonce"}, {"nonce": None}, {"nonce_supported": False},
    {"exp": int(time.time()) - 1000}, {"exp": None},
    {"nbf": int(time.time()) + 1000}, {"azp": "another-client"},
    {"at_hash": "wrong-hash"},
])
def test_invalid_signed_identity_cannot_create_session(tmp_path, signing_key, changes):
    response, session, _ = _login(create_app(_config(tmp_path)), signing_key, changes=changes)
    assert response.status_code == 401
    assert response.json() == {"error": "Authentication failed"}
    assert "user" not in session


@pytest.mark.parametrize("issuer", [None, "https://evil.example", MICROSOFT_ISSUER_TEMPLATE.format(tenantid=TENANT_B)])
def test_signing_key_issuer_must_match_token(tmp_path, signing_key, issuer):
    response, session, _ = _login(create_app(_config(tmp_path)), signing_key, key_issuer=issuer)
    assert response.status_code == 401
    assert "user" not in session


def test_tenant_specific_key_and_key_rotation_work(tmp_path, signing_key):
    response, session, fetched = _login(
        create_app(_config(tmp_path)), signing_key, rotate=True,
        key_issuer=MICROSOFT_ISSUER_TEMPLATE.format(tenantid=TENANT_A),
    )
    assert response.status_code == 307
    assert session["user"]["tenant"] == TENANT_A
    assert fetched


@pytest.mark.parametrize("case", ["signature", "state", "missing_token", "personal_account"])
def test_untrusted_login_cannot_create_session(tmp_path, signing_key, case):
    options = {
        "signature": {"token_key": rsa.generate_private_key(public_exponent=65537, key_size=2048)},
        "state": {"bad_state": True}, "missing_token": {"missing_id_token": True},
        "personal_account": {"tenant": MICROSOFT_CONSUMER_TENANT},
    }
    response, session, _ = _login(create_app(_config(tmp_path)), signing_key, **options[case])
    assert response.status_code == 401
    assert "user" not in session


def test_application_admission_still_limits_customer_tenants(tmp_path, signing_key):
    app = create_app(_config(tmp_path, application_admission={
        "demo": {"providers": ["microsoft"], "tenants": [TENANT_A]},
    }))
    response, session, _ = _login(app, signing_key, tenant=TENANT_B)
    assert response.status_code == 403
    assert "user" not in session
    response, session, _ = _login(app, signing_key, tenant=TENANT_A)
    assert response.status_code == 307
    assert session["user"]["tenant"] == TENANT_A


@pytest.mark.parametrize("allow_multitenant", [False, True])
def test_single_tenant_registration_and_login_remain_default(tmp_path, signing_key, allow_multitenant):
    app = create_app(_config(tmp_path, microsoft_tenant_id=TENANT_A, microsoft_allow_multitenant=allow_multitenant))
    assert f"/{TENANT_A}/" in app.state.pytincture_backend.oauth.microsoft._server_metadata_url
    response, session, _ = _login(app, signing_key)
    assert response.status_code == 307
    assert session["user"]["tenant"] == TENANT_A
