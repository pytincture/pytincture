import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pytincture import PytinctureConfig, create_app, get_modules_path
from pytincture.configuration import configuration_context


def test_from_env_applies_defaults_environment_then_explicit_overrides(tmp_path):
    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "ENABLE_USER_LOGIN": "true",
            "ENABLE_DEV_EMAIL_LOGIN": "true",
            "BFF_CALL_TIMEOUT_SECONDS": "12.5",
            "BFF_MAX_CONCURRENCY": "9",
            "BFF_STREAM_IDLE_TIMEOUT_SECONDS": "4.5",
            "APPCODE_MAX_FILES": "80",
            "REMOTE_STORE_TIMEOUT_SECONDS": "1.25",
            "MCP_ALLOWED_HOSTS": '["mcp.example.test"]',
            "MCP_ALLOWED_ORIGINS": '["https://mcp.example.test"]',
            "PYTINCTURE_ALLOWED_HOSTS": "app.example.test,api.example.test",
            "PYTINCTURE_CANONICAL_ORIGIN": "https://app.example.test/",
            "SAML_RESPONSE_MAX_BYTES": "262144",
            "SAML_RELAY_STATE_TTL_SECONDS": "480",
            "SAML_ACS_RATE_LIMIT_ATTEMPTS": "30",
            "SAML_ACS_RATE_LIMIT_WINDOW_SECONDS": "45",
            "APP_SPECIFIC_VALUE": "kept",
        },
        bff_call_timeout_seconds=8.0,
    )

    assert config.modules_path == str(tmp_path.resolve())
    assert config.enable_user_login is True
    assert config.bff_call_timeout_seconds == 8.0
    assert config.bff_max_concurrency == 9
    assert config.bff_stream_idle_timeout_seconds == 4.5
    assert config.appcode_max_files == 80
    assert config.remote_store_timeout_seconds == 1.25
    assert config.mcp_allowed_hosts == ("mcp.example.test",)
    assert config.mcp_allowed_origins == ("https://mcp.example.test",)
    assert config.allowed_hosts == ("app.example.test", "api.example.test")
    assert config.canonical_origin == "https://app.example.test"
    assert config.trusted_proxy_headers is False
    assert config.saml_response_max_bytes == 262144
    assert config.saml_transaction_ttl_seconds == 480
    assert config.saml_acs_rate_limit_attempts == 30
    assert config.saml_acs_rate_limit_window_seconds == 45
    assert config.environment == {"APP_SPECIFIC_VALUE": "kept"}
    assert config.to_environ()["ENABLE_USER_LOGIN"] == "true"


def test_cors_origins_use_the_backend_csv_format(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        cors_allowed_origins=("https://one.example", "https://two.example"),
    )

    assert config.to_environ()["CORS_ALLOWED_ORIGINS"] == (
        "https://one.example,https://two.example"
    )


def test_saml_limits_do_not_constrain_services_with_saml_disabled(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        max_request_body_bytes=1024,
        saml_response_max_bytes=2048,
    )
    assert config.enable_saml_auth is False


def test_legacy_secret_key_is_an_environment_fallback(tmp_path):
    secret = "a-strong-legacy-secret-with-many-characters"
    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "ENABLE_GOOGLE_AUTH": "true",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "SECRET_KEY": secret,
        }
    )

    assert config.session_secret == secret
    assert config.to_environ()["SAML_SECRET_KEY"] == secret


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_request_body_bytes": 0}, "max_request_body_bytes"),
        ({"default_application": "bad-name"}, "Python identifier"),
        ({"default_application": "classcall"}, "Python identifier"),
        ({"bff_max_queue": -1}, "bff_max_queue"),
        ({"password_hash_max_concurrency": 0}, "resource limits"),
        ({"saml_response_max_bytes": 0}, "saml_response_max_bytes"),
        ({"saml_transaction_ttl_seconds": 0}, "saml_transaction_ttl_seconds"),
        ({"saml_acs_rate_limit_attempts": 0}, "rate-limit"),
        (
            {
                "enable_saml_auth": True,
                "session_secret": "0123456789abcdef" * 2,
                "max_request_body_bytes": 1024,
                "saml_response_max_bytes": 2048,
            },
            "cannot exceed max_request_body_bytes",
        ),
        ({"session_same_site": "none", "session_https_only": False}, "https"),
        (
            {"enable_google_auth": True, "session_secret": "0123456789abcdef" * 2},
            "Google",
        ),
        (
            {
                "enable_saml_auth": True,
                "session_secret": "0123456789abcdef" * 2,
                "saml_providers": '{"work": {"idp_entity_id": "entity"}}',
            },
            "each SAML provider",
        ),
        ({"use_redis_instance": True}, "Redis"),
        ({"cors_allowed_origins": ("*",)}, "cannot use '*'"),
        ({"allowed_hosts": ("https://app.example",)}, "allowed host"),
        ({"allowed_hosts": ("*",)}, "allowed host"),
        ({"canonical_origin": "https://app.example/path"}, "canonical_origin"),
        (
            {
                "allowed_hosts": ("other.example",),
                "canonical_origin": "https://app.example",
            },
            "included in allowed_hosts",
        ),
    ],
)
def test_invalid_configuration_fails_with_actionable_message(tmp_path, overrides, message):
    with pytest.raises(ValueError, match=message):
        PytinctureConfig(modules_path=str(tmp_path), **overrides)


def test_missing_modules_path_fails_before_application_creation(tmp_path):
    with pytest.raises(ValueError, match="modules_path is not a directory"):
        create_app(PytinctureConfig(modules_path=str(tmp_path / "missing")))


def test_configuration_context_is_local_and_does_not_change_legacy_global(tmp_path):
    from pytincture import MODULES_PATH

    config = PytinctureConfig(modules_path=str(tmp_path))
    with configuration_context(config):
        assert get_modules_path() == str(tmp_path.resolve())
    assert get_modules_path() == (MODULES_PATH or os.getenv("MODULES_PATH") or os.getcwd())


def _write_application(root: Path, application: str, result: str):
    (root / f"{application}.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n\n"
        "@backend_for_frontend\n"
        f"class {application.title()}:\n"
        "    def identify(self):\n"
        f"        return {result!r}\n",
        encoding="utf-8",
    )


def test_create_app_isolates_routes_registries_and_session_stores(tmp_path, monkeypatch):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _write_application(first_root, "alpha", "first")
    _write_application(second_root, "beta", "second")
    monkeypatch.setenv("MODULES_PATH", "process-value-must-be-restored")

    first = create_app(PytinctureConfig(modules_path=str(first_root)))
    second = create_app(
        PytinctureConfig(modules_path=str(second_root), trusted_proxy_headers=True)
    )

    assert isinstance(first, FastAPI)
    assert first.state.pytincture_config.modules_path == str(first_root.resolve())
    assert second.state.pytincture_config.modules_path == str(second_root.resolve())
    assert os.environ["MODULES_PATH"] == "process-value-must-be-restored"

    first_backend = first.state.pytincture_backend
    second_backend = second.state.pytincture_backend
    assert first_backend.BFF_REGISTRY_ROOT == str(first_root.resolve())
    assert second_backend.BFF_REGISTRY_ROOT == str(second_root.resolve())
    assert first_backend.BFF_REGISTRY is not second_backend.BFF_REGISTRY
    assert first_backend.USER_SESSION_DICT is not second_backend.USER_SESSION_DICT
    assert first_backend.AUTH_SESSION_REVOCATIONS is not second_backend.AUTH_SESSION_REVOCATIONS

    first_backend.USER_SESSION_DICT["only-first"] = {"email": "first@example.test"}
    assert "only-first" not in second_backend.USER_SESSION_DICT

    proxy_request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (b"host", b"internal.example"),
                (b"x-forwarded-host", b"public.example"),
                (b"x-forwarded-proto", b"https"),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("internal.example", 80),
        }
    )
    assert first_backend._request_origin(proxy_request) == "http://internal.example"
    assert second_backend._request_origin(proxy_request) == "https://public.example"

    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert first_client.get("/alpha/appcode/appcode.pyt").status_code == 200
        assert first_client.get("/beta/appcode/appcode.pyt").status_code == 404
        assert second_client.get("/alpha/appcode/appcode.pyt").status_code == 404
        assert second_client.get("/beta/appcode/appcode.pyt").status_code == 200


def test_canonical_origin_and_allowed_hosts_are_enforced(tmp_path):
    configured = create_app(PytinctureConfig(
        modules_path=str(tmp_path),
        allowed_hosts=("app.example.test",),
        canonical_origin="https://app.example.test/",
        trusted_proxy_headers=True,
    ))
    backend = configured.state.pytincture_backend
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (b"host", b"internal.example"),
                (b"x-forwarded-host", b"attacker.example"),
                (b"x-forwarded-proto", b"http"),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("internal.example", 80),
        }
    )

    assert backend._request_origin(request) == "https://app.example.test"
    assert backend._extract_request_origin(request) == {
        "protocol": "https",
        "host": "app.example.test",
        "host_with_port": "app.example.test",
        "port": 443,
        "base_url": "https://app.example.test",
    }
    with TestClient(configured) as client:
        assert client.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400
        assert client.get("/healthz", headers={"Host": "app.example.test"}).status_code == 200


def test_configuration_reference_document_matches_typed_model():
    documentation = (
        Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
    ).read_text(encoding="utf-8")

    for field_name, environment_name, description in PytinctureConfig.reference():
        expected_row = f"| `{field_name}` | `{environment_name}` | {description} |"
        assert expected_row in documentation


def test_log_level_is_normalized_and_validated(tmp_path):
    assert PytinctureConfig(modules_path=str(tmp_path), log_level="warning").log_level == "WARNING"
    with pytest.raises(ValueError, match="log_level"):
        PytinctureConfig(modules_path=str(tmp_path), log_level="verbose")
