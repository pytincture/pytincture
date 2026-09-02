import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pytincture import PytinctureConfig, create_app, get_modules_path
from pytincture.configuration import configuration_context


_FLOAT_CONFIG_FIELDS = tuple(
    name
    for name, annotation in get_type_hints(PytinctureConfig).items()
    if annotation is float
)


def test_from_env_applies_defaults_environment_then_explicit_overrides(tmp_path):
    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "ENABLE_USER_LOGIN": "true",
            "BFF_CALL_TIMEOUT_SECONDS": "12.5",
            "BFF_MAX_CONCURRENCY": "9",
            "BFF_REQUEST_INGRESS_TIMEOUT_SECONDS": "6.5",
            "BFF_REQUEST_MAX_BYTES": "524288",
            "BFF_REQUEST_MAX_DEPTH": "24",
            "BFF_REQUEST_MAX_ITEMS": "5000",
            "BFF_STREAM_IDLE_TIMEOUT_SECONDS": "4.5",
            "APPCODE_MAX_FILES": "80",
            "APPCODE_CACHE_MAX_BYTES": "67108864",
            "REMOTE_STORE_TIMEOUT_SECONDS": "1.25",
            "MCP_ALLOWED_HOSTS": '["mcp.example.test"]',
            "MCP_ALLOWED_ORIGINS": '["https://mcp.example.test"]',
            "PYTINCTURE_ALLOWED_HOSTS": "app.example.test,api.example.test",
            "PYTINCTURE_CANONICAL_ORIGIN": "https://app.example.test/",
            "PYTINCTURE_DEV_WHEEL_VERSION": "42.0.dev1",
            "PYTINCTURE_WIDGET_WHEEL_MAX_BYTES": "33554432",
            "PYTINCTURE_WIDGET_WHEEL_DIGEST_CACHE_ENTRIES": "9",
            "PYTINCTURE_WIDGET_WHEEL_MAX_CONCURRENCY": "3",
            "PYTINCTURE_WIDGET_WHEEL_MAX_QUEUE": "5",
            "PYTINCTURE_WIDGET_WHEEL_QUEUE_TIMEOUT_SECONDS": "0.25",
            "PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_ATTEMPTS": "40",
            "PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_WINDOW_SECONDS": "20",
            "PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST": '["Corp_Widget==1.2.3"]',
            "SAML_SECRET_KEY": "0123456789abcdef0123456789abcdef",
            "SAML_RESPONSE_MAX_BYTES": "262144",
            "SAML_RELAY_STATE_TTL_SECONDS": "480",
            "SAML_ACS_RATE_LIMIT_ATTEMPTS": "30",
            "SAML_ACS_RATE_LIMIT_WINDOW_SECONDS": "45",
            "SAML_VALIDATION_MAX_CONCURRENCY": "3",
            "SAML_VALIDATION_MAX_QUEUE": "7",
            "SAML_VALIDATION_QUEUE_TIMEOUT_SECONDS": "0.75",
            "SAML_VALIDATION_TIMEOUT_SECONDS": "8.5",
            "BFF_REPLAY_ISSUE_SESSION_LIMIT": "11",
            "BFF_REPLAY_ISSUE_PEER_LIMIT": "22",
            "BFF_REPLAY_ISSUE_WORKER_LIMIT": "33",
            "BFF_REPLAY_ISSUE_WINDOW_SECONDS": "44",
            "BFF_REPLAY_LOCAL_MAX_TOKENS": "555",
            "BFF_REPLAY_LOCAL_MAX_TOKENS_PER_SESSION": "111",
            "BFF_RESULT_MAX_BYTES": "65536",
            "BFF_RESULT_MAX_DEPTH": "12",
            "BFF_RESULT_MAX_ITEMS": "200",
            "BFF_EXECUTION_MODE": "isolated-process",
            "BFF_ASYNC_EXECUTION_MODE": "worker-thread",
            "BFF_ISOLATED_MAX_CONCURRENCY": "3",
            "BFF_ISOLATED_MAX_PER_USER": "1",
            "BFF_ISOLATED_CPU_SECONDS": "2.5",
            "BFF_ISOLATED_MEMORY_BYTES": "536870912",
            "APP_SPECIFIC_VALUE": "kept",
            "REMOTE_STORE_MAX_CONCURRENCY": "6",
            "REMOTE_STORE_MAX_QUEUE": "12",
            "REMOTE_STORE_QUEUE_TIMEOUT_SECONDS": "0.5",
            "READINESS_CACHE_TTL_SECONDS": "2.5",
            "ENABLE_BROWSER_LOGS": "false",
            "ALLOW_NOAUTH_BROWSER_LOGS": "true",
            "BROWSER_LOG_MAX_BYTES": "2048",
            "BROWSER_LOG_RATE_LIMIT_ATTEMPTS": "12",
            "BROWSER_LOG_RATE_LIMIT_WINDOW_SECONDS": "34",
            "PYTINCTURE_API_DOCS_MODE": "authenticated",
            "PYTINCTURE_UVICORN_ACCESS_LOG": "true",
        },
        bff_call_timeout_seconds=8.0,
    )

    assert config.modules_path == str(tmp_path.resolve())
    assert config.enable_user_login is True
    assert config.bff_call_timeout_seconds == 8.0
    assert config.bff_max_concurrency == 9
    assert config.bff_request_ingress_timeout_seconds == 6.5
    assert config.bff_request_max_bytes == 524288
    assert config.bff_request_max_depth == 24
    assert config.bff_request_max_items == 5000
    assert config.bff_stream_idle_timeout_seconds == 4.5
    assert config.appcode_max_files == 80
    assert config.appcode_cache_max_bytes == 67108864
    assert config.remote_store_timeout_seconds == 1.25
    assert config.mcp_allowed_hosts == ("mcp.example.test",)
    assert config.mcp_allowed_origins == ("https://mcp.example.test",)
    assert config.allowed_hosts == ("app.example.test", "api.example.test")
    assert config.canonical_origin == "https://app.example.test"
    assert config.dev_wheel_version == "42.0.dev1"
    assert config.public_widget_wheel_max_bytes == 33554432
    assert config.public_widget_wheel_digest_cache_entries == 9
    assert config.public_widget_wheel_max_concurrency == 3
    assert config.public_widget_wheel_max_queue == 5
    assert config.public_widget_wheel_queue_timeout_seconds == 0.25
    assert config.public_widget_wheel_rate_limit_attempts == 40
    assert config.public_widget_wheel_rate_limit_window_seconds == 20
    assert config.widget_public_index_allowlist == ("corp-widget==1.2.3",)
    assert config.trusted_proxy_headers is False
    assert config.saml_response_max_bytes == 262144
    assert config.saml_transaction_ttl_seconds == 480
    assert config.saml_acs_rate_limit_attempts == 30
    assert config.saml_acs_rate_limit_window_seconds == 45
    assert config.saml_validation_max_concurrency == 3
    assert config.saml_validation_max_queue == 7
    assert config.saml_validation_queue_timeout_seconds == 0.75
    assert config.saml_validation_timeout_seconds == 8.5
    assert config.bff_replay_issue_session_limit == 11
    assert config.bff_replay_issue_peer_limit == 22
    assert config.bff_replay_issue_worker_limit == 33
    assert config.bff_replay_issue_window_seconds == 44
    assert config.bff_replay_local_max_tokens == 555
    assert config.bff_replay_local_max_tokens_per_session == 111
    assert config.bff_result_max_bytes == 65536
    assert config.bff_result_max_depth == 12
    assert config.bff_result_max_items == 200
    assert config.bff_execution_mode == "isolated-process"
    assert config.bff_async_execution_mode == "worker-thread"
    assert config.bff_isolated_max_concurrency == 3
    assert config.bff_isolated_max_per_user == 1
    assert config.bff_isolated_cpu_seconds == 2.5
    assert config.bff_isolated_memory_bytes == 536870912
    assert config.remote_store_max_concurrency == 6
    assert config.remote_store_max_queue == 12
    assert config.remote_store_queue_timeout_seconds == 0.5
    assert config.readiness_cache_ttl_seconds == 2.5
    assert config.enable_browser_logs is False
    assert config.allow_noauth_browser_logs is True
    assert config.browser_log_max_bytes == 2048
    assert config.browser_log_rate_limit_attempts == 12
    assert config.browser_log_rate_limit_window_seconds == 34
    assert config.api_docs_mode == "authenticated"
    assert config.uvicorn_access_log is True
    assert config.environment == {"APP_SPECIFIC_VALUE": "kept"}
    assert config.to_environ()["ENABLE_USER_LOGIN"] == "true"
    assert config.to_environ()["BFF_ASYNC_EXECUTION_MODE"] == "worker-thread"
    assert config.to_environ()["PYTINCTURE_DEV_WHEEL_VERSION"] == "42.0.dev1"
    assert json.loads(
        config.to_environ()["PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST"]
    ) == ["corp-widget==1.2.3"]


def test_replay_proofs_remain_optional_and_do_not_require_redis(tmp_path):
    config = PytinctureConfig(modules_path=str(tmp_path))

    assert config.enable_bff_replay_tokens is False
    assert config.bff_replay_require_shared_store is False
    assert config.use_redis_instance is False


def test_strict_shared_replay_requires_the_optional_feature(tmp_path):
    with pytest.raises(ValueError, match="requires enable_bff_replay_tokens"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            bff_replay_require_shared_store=True,
        )


def test_strict_replay_fails_startup_with_only_a_local_store(tmp_path):
    application = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_bff_replay_tokens=True,
            bff_replay_require_shared_store=True,
        )
    )

    with (
        pytest.raises(RuntimeError, match="shared by every worker"),
        TestClient(application),
    ):
        pass


def test_strict_replay_accepts_a_custom_shared_atomic_store_without_redis(tmp_path):
    class SharedAtomicStore:
        shared_across_workers = True

        def __init__(self):
            self.values = {}

        def issue_batch(self, subject, records, ttl_seconds):
            self.values.update(records)

        def consume(self, key, default=None):
            return self.values.pop(key, default)

    config = PytinctureConfig(
        modules_path=str(tmp_path),
        enable_bff_replay_tokens=True,
        bff_replay_require_shared_store=True,
    )
    first = create_app(config)
    second = create_app(config)
    shared = SharedAtomicStore()
    first.state.pytincture_backend.set_bff_replay_token_store(shared)
    second.state.pytincture_backend.set_bff_replay_token_store(shared)

    with TestClient(first), TestClient(second):
        shared.issue_batch("session", {"proof": {"session_id": "session"}}, 60)
        assert second.state.pytincture_backend.BFF_REPLAY_TOKEN_STORE.consume(
            "proof"
        ) == {"session_id": "session"}
        assert first.state.pytincture_backend.BFF_REPLAY_TOKEN_STORE.consume(
            "proof"
        ) is None


@pytest.mark.parametrize(
    "overrides",
    (
        {"bff_replay_issue_session_limit": 0},
        {"bff_replay_issue_peer_limit": 0},
        {"bff_replay_issue_worker_limit": 0},
        {"bff_replay_issue_window_seconds": 0},
        {"bff_replay_local_max_tokens": 0},
        {"bff_replay_local_max_tokens_per_session": 0},
        {
            "bff_replay_local_max_tokens": 10,
            "bff_replay_local_max_tokens_per_session": 11,
        },
    ),
)
def test_replay_proof_limits_fail_closed(tmp_path, overrides):
    with pytest.raises(ValueError, match="BFF replay|replay"):
        PytinctureConfig(modules_path=str(tmp_path), **overrides)


def test_trusted_bff_execution_remains_the_simple_default(tmp_path):
    config = PytinctureConfig(modules_path=str(tmp_path))

    assert config.bff_execution_mode == "trusted-thread"
    assert config.bff_async_execution_mode == "event-loop"


@pytest.mark.parametrize(
    "overrides",
    (
        {"bff_execution_mode": "container"},
        {"bff_async_execution_mode": "subinterpreter"},
        {"bff_result_max_bytes": 0},
        {"bff_result_max_depth": 0},
        {"bff_result_max_items": 0},
        {"bff_isolated_max_concurrency": 1, "bff_isolated_max_per_user": 2},
        {"bff_isolated_cpu_seconds": 0},
        {"bff_isolated_memory_bytes": 0},
    ),
)
def test_bff_result_and_isolation_limits_fail_closed(tmp_path, overrides):
    with pytest.raises(ValueError, match="BFF|bff|greater than zero"):
        PytinctureConfig(modules_path=str(tmp_path), **overrides)


def test_cors_origins_use_the_backend_csv_format(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        cors_allowed_origins=("https://one.example", "https://two.example"),
    )

    assert config.to_environ()["CORS_ALLOWED_ORIGINS"] == (
        "https://one.example,https://two.example"
    )


def test_browser_connect_origins_are_typed_canonical_and_environment_backed(
    tmp_path,
):
    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "PYTINCTURE_BROWSER_CONNECT_ORIGINS": json.dumps(
                [
                    "HTTPS://API.EXAMPLE.TEST",
                    "wss://events.example.test:8443",
                ]
            ),
        }
    )

    assert config.browser_connect_origins == (
        "https://api.example.test",
        "wss://events.example.test:8443",
    )
    assert json.loads(
        config.to_environ()["PYTINCTURE_BROWSER_CONNECT_ORIGINS"]
    ) == [
        "https://api.example.test",
        "wss://events.example.test:8443",
    ]


@pytest.mark.parametrize(
    "origin",
    (
        "http://api.example.test",
        "ws://events.example.test",
        "https://user:password@api.example.test",
        "https://*.example.test",
        "https://api.example.test/",
        "https://api.example.test/path",
        "https://api.example.test?query=yes",
        "https://api.example.test#fragment",
        "https://api.example.test;script-src-*",
        "https://api.example.test https://evil.example",
    ),
)
def test_browser_connect_origins_reject_ambiguous_or_unsafe_values(
    tmp_path,
    origin,
):
    with pytest.raises(ValueError, match="browser connect origin"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            browser_connect_origins=(origin,),
        )


def test_browser_connect_origins_reject_canonical_duplicates(tmp_path):
    with pytest.raises(ValueError, match="must not contain duplicates"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            browser_connect_origins=(
                "https://api.example.test",
                "HTTPS://API.EXAMPLE.TEST",
            ),
        )


def test_service_csp_uses_exact_default_and_opt_in_connect_origins(tmp_path):
    default_app = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    configured_app = create_app(PytinctureConfig(
        modules_path=str(tmp_path),
        browser_connect_origins=(
            "https://api.example.test",
            "wss://events.example.test:8443",
        ),
    ))

    with TestClient(default_app) as default_client:
        default_csp = default_client.get("/healthz").headers[
            "content-security-policy"
        ]
    with TestClient(configured_app) as configured_client:
        configured_csp = configured_client.get("/healthz").headers[
            "content-security-policy"
        ]

    def directive(policy, name):
        return next(
            item.strip()
            for item in policy.split(";")
            if item.strip().startswith(f"{name} ")
        )

    assert directive(default_csp, "connect-src") == (
        "connect-src 'self' https://pypi.org https://files.pythonhosted.org"
    )
    assert directive(configured_csp, "connect-src") == (
        "connect-src 'self' https://pypi.org https://files.pythonhosted.org "
        "https://api.example.test wss://events.example.test:8443"
    )
    script_sources = directive(configured_csp, "script-src")
    assert script_sources == "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:"
    assert "pypi.org" not in script_sources
    assert "api.example.test" not in script_sources


def test_legacy_app_rejects_credentialed_wildcard_cors(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "CORS_ALLOWED_ORIGINS": "*",
            "MODULES_PATH": str(tmp_path),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "cannot use '*' with credentialed requests" in result.stderr


@pytest.mark.parametrize(
    ("environment_override", "message"),
    [
        ({"PYTINCTURE_TRUST_PROXY_HEADERS": "true"}, "cannot trust proxy headers"),
        (
            {"PYTINCTURE_ALLOWED_HOSTS": "public.example.test"},
            "literal loopback allowed_hosts",
        ),
        (
            {"PYTINCTURE_CANONICAL_ORIGIN": "https://public.example.test"},
            "literal loopback canonical_origin",
        ),
        (
            {"ENABLE_GOOGLE_AUTH": "true"},
            "cannot be combined with production authentication providers",
        ),
    ],
)
def test_legacy_app_rejects_unsafe_development_email_configuration(
    tmp_path, environment_override, message
):
    environment = os.environ.copy()
    environment.update(
        {
            "ENABLE_DEV_EMAIL_LOGIN": "true",
            "ENABLE_USER_LOGIN": "true",
            "MODULES_PATH": str(tmp_path),
            **environment_override,
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_saml_limits_do_not_constrain_services_with_saml_disabled(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        max_request_body_bytes=1024,
        saml_response_max_bytes=2048,
    )
    assert config.enable_saml_auth is False


@pytest.mark.parametrize("field_name", _FLOAT_CONFIG_FIELDS)
@pytest.mark.parametrize("encoded_value", ("nan", "inf", "-inf"))
def test_every_floating_point_environment_setting_rejects_non_finite_values(
    tmp_path, field_name, encoded_value
):
    definitions = {definition.name: definition for definition in fields(PytinctureConfig)}
    environment_name = definitions[field_name].metadata["env"]

    with pytest.raises(ValueError, match="must be finite"):
        PytinctureConfig.from_env(
            {
                "MODULES_PATH": str(tmp_path),
                environment_name: encoded_value,
            }
        )


@pytest.mark.parametrize("encoded_value", ("nan", "inf", "-inf"))
def test_legacy_raw_asgi_import_uses_typed_non_finite_validation(
    tmp_path, encoded_value
):
    environment = os.environ.copy()
    environment.update(
        {
            "MODULES_PATH": str(tmp_path),
            "BFF_CALL_TIMEOUT_SECONDS": encoded_value,
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", "import pytincture.backend.app"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "floating-point resource limits must be finite" in result.stderr


def test_development_widget_version_is_validated_and_rendered(tmp_path):
    (tmp_path / "demo.py").write_text(
        'import demo_widget\nAPP_TITLE = "Demo"\n', encoding="utf-8"
    )
    (tmp_path / "demo_widget.py").write_text(
        '__widgetset__ = "demo-widget"\n__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    declared_wheel = "demo_widget-1.2.3-py3-none-any.whl"
    development_wheel = "demo_widget-42.0.dev1-py3-none-any.whl"
    default_development_wheel = "demo_widget-99.99.99-py3-none-any.whl"
    for wheel in (declared_wheel, development_wheel, default_development_wheel):
        (tmp_path / wheel).write_bytes(wheel.encode("ascii"))
    with pytest.raises(ValueError, match="dev_wheel_version"):
        PytinctureConfig(modules_path=str(tmp_path), dev_wheel_version="not a version")

    application = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            default_application="demo",
            dev_wheel_version="42.0.dev1",
            widget_public_index_allowlist=("Demo_Widget==1.2.3",),
        )
    )
    with TestClient(application) as client:
        response = client.get("/demo")
        declared = client.get(f"/demo/appcode/{declared_wheel}")
        development = client.get(f"/demo/appcode/{development_wheel}")
        rejected_default = client.get(f"/demo/appcode/{default_development_wheel}")

    assert response.status_code == 200
    assert 'devWheelVersion: "42.0.dev1"' in response.text
    assert "allowPublicWidgetIndex: true" in response.text
    assert (
        'backendWidgetSources: ["/demo/appcode/demo_widget-1.2.3-py3-none-any.whl",'
        '"/demo/appcode/demo_widget-42.0.dev1-py3-none-any.whl"]'
        in response.text
    )
    assert "***DEV_WHEEL_VERSION***" not in response.text
    assert declared.status_code == 200
    assert development.status_code == 200
    assert rejected_default.status_code == 404


def test_backend_widget_source_preserves_local_version_separator(tmp_path):
    (tmp_path / "demo.py").write_text(
        'import demo_widget\nAPP_TITLE = "Demo"\n', encoding="utf-8"
    )
    (tmp_path / "demo_widget.py").write_text(
        '__widgetset__ = "demo-widget"\n__version__ = "1.2.3+backend"\n',
        encoding="utf-8",
    )
    wheel = "demo_widget-1.2.3+backend-py3-none-any.whl"
    (tmp_path / wheel).write_bytes(b"backend wheel")

    application = create_app(
        PytinctureConfig(modules_path=str(tmp_path), default_application="demo")
    )
    with TestClient(application) as client:
        response = client.get("/demo")

    assert response.status_code == 200
    assert f'backendWidgetSources: ["/demo/appcode/{wheel}"]' in response.text


def test_service_widget_public_index_is_deny_by_default_and_exactly_allowlisted(
    tmp_path,
):
    (tmp_path / "demo.py").write_text("import demo_widget\n", encoding="utf-8")
    (tmp_path / "demo_widget.py").write_text(
        '__widgetset__ = "demo-widget"\n__version__ = "1.2.3"\n',
        encoding="utf-8",
    )

    with TestClient(create_app(PytinctureConfig(modules_path=str(tmp_path)))) as client:
        denied = client.get("/demo")
    with TestClient(create_app(PytinctureConfig(
        modules_path=str(tmp_path),
        widget_public_index_allowlist=("demo-widget==1.2.3",),
    ))) as client:
        allowed = client.get("/demo")

    assert "allowPublicWidgetIndex: false" in denied.text
    assert "allowPublicWidgetIndex: true" in allowed.text


@pytest.mark.parametrize(
    "spec",
    (
        "demo-widget",
        "demo-widget>=1.2.3",
        "demo-widget==",
        "demo-widget==1.2.3 ",
        "demo widget==1.2.3",
    ),
)
def test_widget_public_index_allowlist_rejects_nonexact_specs(tmp_path, spec):
    with pytest.raises(ValueError, match="exact name==version"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            widget_public_index_allowlist=(spec,),
        )


def test_deployment_widget_trust_policy_overrides_manifest_and_escapes_html(tmp_path):
    (tmp_path / "demo.py").write_text("import demo_widget\n", encoding="utf-8")
    (tmp_path / "demo_widget.py").write_text(
        '__widgetset__ = "demo-widget"\n__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    declared_wheel = "demo_widget-1.2.3-py3-none-any.whl"
    development_wheel = "demo_widget-99.99.99-py3-none-any.whl"
    (tmp_path / declared_wheel).write_bytes(b"declared")
    (tmp_path / development_wheel).write_bytes(b"development")
    policy_path = tmp_path / "widget-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "widgetsets": [
                    {
                        "distribution": "demo-widget",
                        "version": "1.2.3",
                        "assets": [
                            {
                                "path": "demo_widget/</script><script>alert.js",
                                "type": "javascript",
                                "sha256": "a" * 64,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        default_application="demo",
        widget_trust_policy=str(policy_path),
    )

    assert config.widget_trust_policy.startswith('{"schema":1,"widgetsets":[')
    assert config.to_environ()["PYTINCTURE_WIDGET_TRUST_POLICY"] == config.widget_trust_policy
    with TestClient(create_app(config)) as client:
        response = client.get("/demo")
        declared = client.get(f"/demo/appcode/{declared_wheel}")
        development = client.get(f"/demo/appcode/{development_wheel}")

    assert response.status_code == 200
    assert "widgetAssetManifest:" in response.text
    assert "</script><script>alert.js" not in response.text
    assert "\\u003c/script\\u003e\\u003cscript\\u003ealert.js" in response.text
    assert declared.status_code == 200
    assert development.status_code == 404


def test_deployment_widget_trust_policy_fails_closed_for_unlisted_version(tmp_path):
    (tmp_path / "demo.py").write_text("import demo_widget\n", encoding="utf-8")
    (tmp_path / "demo_widget.py").write_text(
        '__widgetset__ = "demo-widget"\n__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    policy = json.dumps(
        {
            "schema": 1,
            "widgetsets": [
                {
                    "distribution": "demo-widget",
                    "version": "1.2.4",
                    "assets": [
                        {
                            "path": "demo_widget/widget.js",
                            "type": "javascript",
                            "sha256": "b" * 64,
                        }
                    ],
                }
            ],
        }
    )
    application = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            default_application="demo",
            widget_trust_policy=policy,
        )
    )

    with TestClient(application) as client:
        response = client.get("/demo")

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["correlation_id"]


def test_widget_trust_policy_rejects_ambiguous_or_unhashed_entries(tmp_path):
    invalid_policy = json.dumps(
        {
            "schema": 1,
            "widgetsets": [
                {
                    "distribution": "demo-widget",
                    "version": "1.2.3",
                    "assets": [
                        {
                            "path": "demo_widget/widget.js",
                            "type": "javascript",
                            "sha256": "not-a-digest",
                        }
                    ],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="invalid widget_trust_policy"):
        PytinctureConfig(
            modules_path=str(tmp_path), widget_trust_policy=invalid_policy
        )


def test_legacy_secret_key_is_an_environment_fallback(tmp_path):
    secret = "a-strong-legacy-secret-with-many-characters"
    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "ENABLE_GOOGLE_AUTH": "true",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "PYTINCTURE_ALLOWED_HOSTS": "app.example.test",
            "PYTINCTURE_CANONICAL_ORIGIN": "https://app.example.test",
            "SECRET_KEY": secret,
        }
    )

    assert config.session_secret == secret
    assert config.to_environ()["SAML_SECRET_KEY"] == secret


def test_secret_configuration_repr_is_redacted_and_previous_keys_are_strong(tmp_path):
    secret = "0123456789abcdef" * 2
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        google_client_secret="google-secret-value",
        microsoft_client_secret="microsoft-secret-value",
        session_secret=secret,
        previous_session_secrets=("abcdef0123456789" * 2,),
        redis_token="redis-secret-value",
        mcp_jwt_public_key="public-key-material",
        saml_providers='{"private_key":"saml-secret-value"}',
    )
    rendered = repr(config)
    for sensitive in (
        "google-secret-value", "microsoft-secret-value", secret,
        "redis-secret-value", "public-key-material", "saml-secret-value",
    ):
        assert sensitive not in rendered
    with pytest.raises(ValueError, match="strong keys"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            previous_session_secrets=("a" * 32,),
        )


def test_microsoft_auth_requires_explicit_tenant(tmp_path):
    with pytest.raises(ValueError, match="tenant id"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_microsoft_auth=True,
            microsoft_client_id="client",
            microsoft_client_secret="secret",
            session_secret="0123456789abcdef" * 2,
        )

    with pytest.raises(ValueError, match="one explicit tenant"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_microsoft_auth=True,
            microsoft_client_id="client",
            microsoft_client_secret="secret",
            microsoft_tenant_id="common",
            session_secret="0123456789abcdef" * 2,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_request_body_bytes": 0}, "max_request_body_bytes"),
        ({"default_application": "bad-name"}, "Python identifier"),
        ({"default_application": "classcall"}, "Python identifier"),
        ({"bff_max_queue": -1}, "bff_max_queue"),
        ({"bff_request_max_depth": 0}, "resource limits"),
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


@pytest.mark.parametrize(
    "redis_url",
    (
        "https://example.upstash.io",
        "http://127.0.0.1:16379",
        "http://[::1]:16379",
    ),
)
def test_optional_redis_accepts_https_and_literal_loopback_http(tmp_path, redis_url):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        use_redis_instance=True,
        redis_url=f"  {redis_url}  ",
        redis_token="token",
    )

    assert config.redis_url == redis_url


@pytest.mark.parametrize(
    "redis_url",
    (
        "http://redis.internal:8079",
        "http://localhost:8079",
        "http://127.0.0.1.example:8079",
    ),
)
def test_optional_redis_rejects_cleartext_nonliteral_endpoints(tmp_path, redis_url):
    with pytest.raises(ValueError, match="HTTPS.*literal loopback"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            use_redis_instance=True,
            redis_url=redis_url,
            redis_token="token",
        )


def test_redis_remains_disabled_and_unrequired_by_default(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        redis_url="http://remote.example:8079",
    )

    assert config.use_redis_instance is False
    assert config.redis_token == ""


def test_production_authentication_requires_exact_https_origin_controls(tmp_path):
    base = {
        "modules_path": str(tmp_path),
        "enable_user_login": True,
        "session_secret": "0123456789abcdef" * 2,
    }

    with pytest.raises(ValueError, match="exact allowed_hosts"):
        PytinctureConfig(**base)
    with pytest.raises(ValueError, match="canonical_origin"):
        PytinctureConfig(**base, allowed_hosts=("app.example.test",))
    with pytest.raises(ValueError, match="without wildcards"):
        PytinctureConfig(
            **base,
            allowed_hosts=("*.example.test",),
            canonical_origin="https://app.example.test",
        )
    with pytest.raises(ValueError, match="HTTPS canonical_origin"):
        PytinctureConfig(
            **base,
            allowed_hosts=("app.example.test",),
            canonical_origin="http://app.example.test",
        )

    config = PytinctureConfig(
        **base,
        allowed_hosts=("app.example.test",),
        canonical_origin="https://app.example.test",
    )
    assert config.allowed_hosts == ("app.example.test",)
    assert config.canonical_origin == "https://app.example.test"
    assert config.session_https_only is True

    with pytest.raises(ValueError, match="session_https_only=true"):
        PytinctureConfig(
            **base,
            allowed_hosts=("app.example.test",),
            canonical_origin="https://app.example.test",
            session_https_only=False,
        )


def test_dynamic_auth_origin_is_explicit_loopback_development_only(tmp_path):
    base = {
        "modules_path": str(tmp_path),
        "enable_user_login": True,
        "session_secret": "0123456789abcdef" * 2,
        "allow_development_auth_origin": True,
    }

    with pytest.raises(ValueError, match="session_https_only=false"):
        PytinctureConfig(**base)
    with pytest.raises(ValueError, match="cannot trust proxy headers"):
        PytinctureConfig(
            **base,
            session_https_only=False,
            trusted_proxy_headers=True,
        )

    config = PytinctureConfig(**base, session_https_only=False)
    assert config.allow_development_auth_origin is True
    assert config.allowed_hosts == ()
    assert config.canonical_origin is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"trusted_proxy_headers": True}, "cannot trust proxy headers"),
        ({"allowed_hosts": ("app.example.test",)}, "literal loopback allowed_hosts"),
        (
            {"canonical_origin": "https://app.example.test"},
            "literal loopback canonical_origin",
        ),
        (
            {"enable_google_auth": True},
            "cannot be combined with production authentication providers",
        ),
    ],
)
def test_development_email_login_rejects_proxy_and_public_auth_configuration(
    tmp_path, overrides, message
):
    with pytest.raises(ValueError, match=message):
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_user_login=True,
            enable_dev_email_login=True,
            **overrides,
        )


def test_development_email_login_allows_explicit_literal_loopback_controls(tmp_path):
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        enable_user_login=True,
        enable_dev_email_login=True,
        allowed_hosts=("127.0.0.1",),
        canonical_origin="http://127.0.0.1",
    )

    assert config.allowed_hosts == ("127.0.0.1",)
    assert config.canonical_origin == "http://127.0.0.1"


def test_dynamic_auth_origin_rejects_non_loopback_requests(tmp_path):
    (tmp_path / "demo.py").write_text('APP_TITLE = "Demo"\n', encoding="utf-8")
    application = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            enable_user_login=True,
            session_secret="0123456789abcdef" * 2,
            session_https_only=False,
            allow_development_auth_origin=True,
        )
    )

    with TestClient(
        application,
        base_url="http://127.0.0.1",
        client=("203.0.113.7", 50000),
    ) as remote_client:
        rejected = remote_client.get("/healthz")
    with TestClient(
        application,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as local_client:
        accepted = local_client.get("/healthz", headers={"Origin": "null"})

    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "Development authentication is loopback-only"}
    assert accepted.status_code == 200


def test_trusted_proxy_headers_require_fixed_host_and_origin(tmp_path):
    with pytest.raises(
        ValueError,
        match="trusted_proxy_headers requires allowed_hosts and canonical_origin",
    ):
        PytinctureConfig(
            modules_path=str(tmp_path),
            trusted_proxy_headers=True,
        )


def test_missing_modules_path_fails_before_application_creation(tmp_path):
    with pytest.raises(ValueError, match="modules_path is not a directory"):
        create_app(PytinctureConfig(modules_path=str(tmp_path / "missing")))


def test_readonly_modules_path_enforcement_is_opt_in(monkeypatch, tmp_path):
    import pytincture.configuration as configuration

    monkeypatch.setattr(
        configuration,
        "modules_path_appears_writable",
        lambda path: True,
    )

    compatible = PytinctureConfig(modules_path=str(tmp_path))
    assert compatible.require_readonly_modules_path is False

    with pytest.raises(ValueError, match="modules_path is writable"):
        PytinctureConfig(
            modules_path=str(tmp_path),
            require_readonly_modules_path=True,
        )


def test_readonly_modules_path_enforcement_accepts_readonly_root(
    monkeypatch,
    tmp_path,
):
    import pytincture.configuration as configuration

    monkeypatch.setattr(
        configuration,
        "modules_path_appears_writable",
        lambda path: False,
    )

    config = PytinctureConfig(
        modules_path=str(tmp_path),
        require_readonly_modules_path=True,
    )

    assert config.require_readonly_modules_path is True
    assert config.to_environ()["PYTINCTURE_REQUIRE_READONLY_MODULES_PATH"] == (
        "true"
    )
    with TestClient(create_app(config)) as client:
        assert client.get("/healthz").status_code == 200


def test_readonly_modules_path_enforcement_parses_environment(
    monkeypatch,
    tmp_path,
):
    import pytincture.configuration as configuration

    monkeypatch.setattr(
        configuration,
        "modules_path_appears_writable",
        lambda path: False,
    )

    config = PytinctureConfig.from_env(
        {
            "MODULES_PATH": str(tmp_path),
            "PYTINCTURE_REQUIRE_READONLY_MODULES_PATH": "true",
        }
    )

    assert config.require_readonly_modules_path is True


def test_writable_modules_path_emits_one_structured_startup_warning(
    monkeypatch,
    caplog,
    tmp_path,
):
    import pytincture.configuration as configuration

    monkeypatch.setattr(
        configuration,
        "modules_path_appears_writable",
        lambda path: True,
    )
    configured_app = create_app(PytinctureConfig(modules_path=str(tmp_path)))

    with caplog.at_level("WARNING", logger="pytincture.security"):
        with TestClient(configured_app) as client:
            assert client.get("/healthz").status_code == 200

    warnings = [
        json.loads(record.message)
        for record in caplog.records
        if '"event":"security.modules_path_writable"' in record.message
    ]
    assert warnings == [
        {
            "enforcement": False,
            "event": "security.modules_path_writable",
            "modules_path": str(tmp_path.resolve()),
            "production_control": (
                "mount application source read-only and run as a non-root service user"
            ),
        }
    ]


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
        PytinctureConfig(
            modules_path=str(second_root),
            trusted_proxy_headers=True,
            allowed_hosts=("public.example",),
            canonical_origin="https://public.example",
        )
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

    with TestClient(first) as first_client, TestClient(
        second, base_url="https://public.example"
    ) as second_client:
        assert first_client.get("/alpha/appcode/appcode.pyt").status_code == 200
        assert first_client.get("/beta/appcode/appcode.pyt").status_code == 404
        assert second_client.get("/alpha/appcode/appcode.pyt").status_code == 404
        assert second_client.get("/beta/appcode/appcode.pyt").status_code == 200


def test_concurrent_create_app_never_exposes_instance_environment(
    tmp_path, monkeypatch
):
    import importlib

    factory_module = importlib.import_module("pytincture.factory")
    first_root = tmp_path / "first-concurrent"
    second_root = tmp_path / "second-concurrent"
    first_root.mkdir()
    second_root.mkdir()
    _write_application(first_root, "alpha", "first")
    _write_application(second_root, "beta", "second")
    monkeypatch.setenv("MODULES_PATH", "process-modules")
    monkeypatch.setenv("PYTINCTURE_FACTORY_SENTINEL", "process-value")

    entered_loader = threading.Event()
    release_loader = threading.Event()
    original_exec_module = factory_module.SourceFileLoader.exec_module
    pause_lock = threading.Lock()
    paused = False

    def observed_exec_module(loader, module):
        nonlocal paused
        should_pause = False
        with pause_lock:
            if not paused and module.__name__.startswith("pytincture.backend._instance_"):
                paused = True
                should_pause = True
        if should_pause:
            entered_loader.set()
            assert release_loader.wait(timeout=10)
        return original_exec_module(loader, module)

    monkeypatch.setattr(
        factory_module.SourceFileLoader,
        "exec_module",
        observed_exec_module,
    )
    created = {}
    errors = []

    def build(name, root, private_value):
        try:
            created[name] = create_app(
                PytinctureConfig(
                    modules_path=str(root),
                    environment={"PYTINCTURE_FACTORY_SENTINEL": private_value},
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first_thread = threading.Thread(
        target=build,
        args=("first", first_root, "private-first"),
    )
    second_thread = threading.Thread(
        target=build,
        args=("second", second_root, "private-second"),
    )
    first_thread.start()
    assert entered_loader.wait(timeout=10)
    second_thread.start()

    # While one factory is paused inside backend execution and another is
    # concurrently waiting, only the process owner's values remain observable.
    assert os.environ["MODULES_PATH"] == "process-modules"
    assert os.environ["PYTINCTURE_FACTORY_SENTINEL"] == "process-value"

    release_loader.set()
    first_thread.join(timeout=30)
    second_thread.join(timeout=30)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert created["first"].state.pytincture_backend.BFF_REGISTRY_ROOT == str(
        first_root.resolve()
    )
    assert created["second"].state.pytincture_backend.BFF_REGISTRY_ROOT == str(
        second_root.resolve()
    )
    assert os.environ["MODULES_PATH"] == "process-modules"
    assert os.environ["PYTINCTURE_FACTORY_SENTINEL"] == "process-value"


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


def test_bff_documentation_is_per_app_and_redacts_defaults(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "first.py").write_text("from service import FirstService\n")
    (first_root / "service.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class FirstService:\n"
        "    def read(self, token='must-not-appear'): return token\n"
    )
    (second_root / "second.py").write_text("from worker import SecondService\n")
    (second_root / "worker.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class SecondService:\n"
        "    def read(self): return True\n"
    )
    first = create_app(PytinctureConfig(modules_path=str(first_root)))
    second = create_app(PytinctureConfig(modules_path=str(second_root)))
    with TestClient(first) as first_client, TestClient(second) as second_client:
        first_schema = first_client.get("/bff-docs/openapi.json").text
        second_schema = second_client.get("/bff-docs/openapi.json").text
        docs_response = first_client.get("/bff-docs")
        swagger_bundle = first_client.get(
            "/frontend/vendor/swagger-ui/swagger-ui-bundle.js?uuid=test"
        )
        swagger_styles = first_client.get(
            "/frontend/vendor/swagger-ui/swagger-ui.css?uuid=test"
        )
        docs_runtime = first_client.get("/frontend/bff-docs.js?uuid=test")
        hidden_license = first_client.get("/frontend/vendor/swagger-ui/LICENSE")
    assert "FirstService" in first_schema and "SecondService" not in first_schema
    assert "SecondService" in second_schema and "FirstService" not in second_schema
    assert "must-not-appear" not in first_schema
    assert docs_response.status_code == 200
    assert not docs_response.history
    assert "cdn.jsdelivr.net" not in docs_response.text
    assert "https://" not in docs_response.text
    docs_uuid_values = re.findall(r"[?&]uuid=([a-f0-9]{32})", docs_response.text)
    assert len(docs_uuid_values) == 4
    assert len(set(docs_uuid_values)) == 1
    assert docs_response.headers["cache-control"] == "private, no-store, max-age=0"
    docs_csp = docs_response.headers["content-security-policy"]
    assert "script-src 'self'" in docs_csp
    assert "https:" not in docs_csp
    assert "'unsafe-eval'" not in docs_csp
    assert swagger_bundle.status_code == 200
    assert "SwaggerUIBundle" in swagger_bundle.text
    assert swagger_styles.status_code == 200
    assert ".swagger-ui" in swagger_styles.text
    assert docs_runtime.status_code == 200
    assert "data-openapi-url" not in docs_runtime.text
    assert hidden_license.status_code == 404


def test_bff_documentation_can_require_authentication_or_be_disabled(tmp_path):
    (tmp_path / "demo.py").write_text('APP_TITLE = "Docs mode"\n')

    authenticated = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            api_docs_mode="authenticated",
        )
    )
    authenticated_backend = authenticated.state.pytincture_backend
    with TestClient(authenticated) as client:
        for path in (
            "/bff-docs",
            "/bff-docs/openapi.json",
            "/docs",
            "/openapi.json",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "private, no-store, max-age=0"
            assert set(response.headers["vary"].split(", ")) == {
                "Cookie",
                "Authorization",
            }

        authenticated_backend.require_auth = lambda _request: {
            "is_authenticated": True,
            "email": "docs@example.test",
        }
        assert client.get("/bff-docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    disabled = create_app(
        PytinctureConfig(
            modules_path=str(tmp_path),
            api_docs_mode="disabled",
        )
    )
    with TestClient(disabled) as client:
        for path in (
            "/bff-docs",
            "/bff-docs/openapi.json",
            "/docs",
            "/redoc",
            "/openapi.json",
        ):
            assert client.get(path, follow_redirects=False).status_code == 404


def test_invalid_python_file_cannot_disable_unrelated_bff_application(tmp_path):
    (tmp_path / "healthy.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class Healthy:\n"
        "    def ping(self): return {'healthy': True}\n",
        encoding="utf-8",
    )
    (tmp_path / "partially_written.py").write_text(
        "def deploy_in_progress(\n", encoding="utf-8"
    )

    configured = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    backend = configured.state.pytincture_backend
    assert backend.BFF_REGISTRY_FAILURES == {
        "partially_written.py": "invalid_python_syntax"
    }

    with TestClient(configured) as client:
        healthy = client.post(
            "/healthy/classcall/healthy.py/Healthy/ping", json={"args": [], "kwargs": {}}
        )
        rejected = client.post(
            "/partially_written/classcall/partially_written.py/Unknown/ping",
            json={"args": [], "kwargs": {}},
        )
    assert healthy.status_code == 200
    assert healthy.json() == {"healthy": True}
    assert rejected.status_code == 404


def test_configuration_reference_document_matches_typed_model():
    documentation = (
        Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
    ).read_text(encoding="utf-8")

    for field_name, environment_name, description in PytinctureConfig.reference():
        expected_row = f"| `{field_name}` | `{environment_name}` | {description} |"
        assert expected_row in documentation


def test_browser_diagnostic_and_documentation_controls_fail_closed(tmp_path):
    defaults = PytinctureConfig(modules_path=str(tmp_path))
    assert defaults.enable_browser_logs is True
    assert defaults.allow_noauth_browser_logs is False
    assert defaults.api_docs_mode == "public"
    assert defaults.uvicorn_access_log is False

    for field in (
        "browser_log_max_bytes",
        "browser_log_rate_limit_attempts",
        "browser_log_rate_limit_window_seconds",
    ):
        with pytest.raises(ValueError, match="resource limits"):
            PytinctureConfig(modules_path=str(tmp_path), **{field: 0})

    with pytest.raises(ValueError, match="api_docs_mode"):
        PytinctureConfig(modules_path=str(tmp_path), api_docs_mode="sometimes")


def test_log_level_is_normalized_and_validated(tmp_path):
    assert PytinctureConfig(modules_path=str(tmp_path), log_level="warning").log_level == "WARNING"
    with pytest.raises(ValueError, match="log_level"):
        PytinctureConfig(modules_path=str(tmp_path), log_level="verbose")
