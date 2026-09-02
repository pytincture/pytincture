import json
import re

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.backend.application_admission import (
    canonical_application_admission,
    identity_is_admitted,
    parse_application_admission,
)


def _password_login(client, application):
    page = client.get(f"/{application}/login")
    token = re.search(
        r'name="login_csrf_token" value="([^"]+)"', page.text
    ).group(1)
    return client.post(
        f"/{application}/auth/user",
        data={
            "email": "analyst@example.com",
            "password": "verified",
            "login_csrf_token": token,
        },
        follow_redirects=False,
    )


def test_application_admission_is_stateless_and_fails_closed():
    policies = parse_application_admission(
        {
            "reports": {
                "providers": ["google", "microsoft"],
                "issuers": ["https://accounts.google.com"],
                "tenants": ["tenant-1"],
                "subjects": ["subject-1"],
                "emails": ["Analyst@Example.com"],
                "email_domains": ["@Example.com"],
                "roles": ["Reader", "admin"],
            }
        }
    )
    identity = {
        "auth_provider": "google",
        "issuer": "https://accounts.google.com",
        "tenant": "tenant-1",
        "subject": "subject-1",
        "email": "analyst@example.com",
        "roles": ["reader"],
    }

    assert identity_is_admitted(policies, "reports", identity)
    assert not identity_is_admitted(policies, "admin", identity)
    assert not identity_is_admitted(
        policies, "reports", {**identity, "subject": "different"}
    )


@pytest.mark.parametrize(
    ("application", "identity", "allowed"),
    [
        (
            "google_app",
            {
                "auth_provider": "google",
                "issuer": "https://accounts.google.com",
                "subject": "google-subject",
                "email": "user@example.com",
                "roles": ["reader"],
            },
            True,
        ),
        (
            "microsoft_app",
            {
                "auth_provider": "microsoft",
                "issuer": "https://login.microsoftonline.com/tenant-1/v2.0",
                "tenant": "tenant-1",
                "subject": "microsoft-subject",
                "email": "user@example.com",
                "roles": ["operator"],
            },
            True,
        ),
        (
            "saml_app",
            {
                "auth_provider": "workforce",
                "issuer": "https://idp.example.com/metadata",
                "subject": "saml-name-id",
                "email": "user@example.com",
                "roles": ["staff"],
            },
            True,
        ),
        (
            "microsoft_app",
            {
                "auth_provider": "google",
                "issuer": "https://accounts.google.com",
                "subject": "google-subject",
                "email": "user@example.com",
                "roles": ["operator"],
            },
            False,
        ),
    ],
)
def test_application_admission_across_identity_providers(
    application, identity, allowed
):
    policies = parse_application_admission(
        {
            "google_app": {
                "providers": ["google"],
                "issuers": ["https://accounts.google.com"],
                "subjects": ["google-subject"],
                "roles": ["reader"],
            },
            "microsoft_app": {
                "providers": ["microsoft"],
                "issuers": [
                    "https://login.microsoftonline.com/tenant-1/v2.0"
                ],
                "tenants": ["tenant-1"],
                "subjects": ["microsoft-subject"],
                "roles": ["operator"],
            },
            "saml_app": {
                "providers": ["workforce"],
                "issuers": ["https://idp.example.com/metadata"],
                "subjects": ["saml-name-id"],
                "roles": ["staff"],
            },
        }
    )

    assert identity_is_admitted(policies, application, identity) is allowed


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "JSON object"),
        ({"bad-name": {}}, "invalid application"),
        ({"reports": {"unknown": ["value"]}}, "unknown fields"),
        ({"reports": {"roles": []}}, "must contain strings"),
        ({"reports": {"email_domains": ["*"]}}, "wildcards"),
        ({"reports": {"emails": ["not-an-email"]}}, "invalid email"),
    ],
)
def test_application_admission_rejects_invalid_configuration(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_application_admission(raw)


def test_empty_application_admission_preserves_single_trust_mode():
    assert identity_is_admitted({}, "any_app", {"email": "user@example.com"})
    assert canonical_application_admission({}) == ""


def test_config_normalizes_application_admission_and_round_trips_to_environment(
    tmp_path,
):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        application_admission={
            "reports": {
                "emails": ["analyst@example.com"],
                "email_domains": ["Example.com"],
                "roles": ["Reader"],
            }
        },
    )

    serialized = config.to_environ()["AUTH_APPLICATION_ADMISSION"]
    assert json.loads(serialized) == {
        "reports": {
            "emails": ["analyst@example.com"],
            "email_domains": ["example.com"],
            "roles": ["reader"],
        }
    }
    assert "analyst@example.com" not in repr(config)
    assert PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "AUTH_APPLICATION_ADMISSION": serialized,
        }
    ).application_admission == serialized


def test_local_login_enforces_application_admission_before_session_issuance(
    tmp_path,
):
    for application in ("reports", "admin", "unlisted"):
        (tmp_path / f"{application}.py").write_text(
            f"class {application}:\n    pass\n", encoding="utf-8"
        )

    service = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_user_login=True,
            session_secret="0123456789abcdef" * 2,
            allowed_hosts=("app.example.test",),
            canonical_origin="https://app.example.test",
            application_admission={
                "reports": {
                    "providers": ["user"],
                    "issuers": ["local-directory"],
                    "tenants": ["tenant-1"],
                    "subjects": ["analyst-1"],
                    "email_domains": ["example.com"],
                    "roles": ["reader"],
                },
                "admin": {
                    "providers": ["user"],
                    "roles": ["administrator"],
                },
            },
        )
    )
    backend = service.state.pytincture_backend
    backend.set_user_authenticator(
        lambda **_kwargs: {
            "issuer": "local-directory",
            "tenant": "tenant-1",
            "subject": "analyst-1",
            "roles": ["reader"],
        }
    )

    with TestClient(service, base_url="https://app.example.test") as client:
        allowed = _password_login(client, "reports")
        assert allowed.status_code == 303
        session = client.cookies.get(backend._SESSION_COOKIE)
        assert session
        wrong_audience = client.get("/admin", follow_redirects=False)
        assert wrong_audience.status_code == 307
        assert wrong_audience.headers["location"] == "/admin/login"

        client.cookies.clear()
        wrong_role = _password_login(client, "admin")
        assert wrong_role.status_code == 403
        assert client.cookies.get(backend._SESSION_COOKIE) is None

        unlisted = _password_login(client, "unlisted")
        assert unlisted.status_code == 403
        assert client.cookies.get(backend._SESSION_COOKIE) is None
