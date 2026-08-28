import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

from pytincture.backend.auth import (
    allowed_email,
    local_user_claims,
    normalize_roles,
    verify_password,
)
from pytincture.backend.bff import BFFRegistry
from pytincture.backend.browser_packages import (
    browser_package_files,
    discover_widgetset,
)
from pytincture.backend.diagnostics import (
    internal_error_payload,
    request_correlation_id,
    sanitized_validation_errors,
)
from pytincture.backend.mcp import FilteredFastAPIApp, exposed_operation_ids
from pytincture.backend.pages import find_app_string_setting, normalize_app_asset_path
from pytincture.backend.saml import SAMLProviderCatalog, allowed_roles
from pytincture.backend.source_loading import build_dynamic_module_name
from pytincture.backend.storage import RedisDict
from pytincture.backend.streaming import limited_async_stream, limited_sync_stream


def test_auth_primitives_are_configuration_free():
    assert allowed_email("user@example.com", "ADMIN@example.com, user@example.com")
    assert not allowed_email("other@example.com", "user@example.com")
    assert normalize_roles(["Admin", " admin ", "Reader"]) == ["admin", "reader"]
    claims = local_user_claims(
        "user@example.com",
        json.dumps(
            {
                "USER@example.com": {
                    "name": "User",
                    "password": "must-not-leak",
                    "access_token": "must-not-leak",
                }
            }
        ),
        "AUTH_USER_CLAIMS",
    )
    assert claims == {"email": "user@example.com", "name": "User"}


def test_password_extra_has_actionable_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "argon2", None)
    with pytest.raises(RuntimeError, match=r"pytincture\[password\]"):
        verify_password(
            "user@example.com",
            "password",
            json.dumps({"user@example.com": "$argon2id$placeholder"}),
        )


def test_redis_extra_has_actionable_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "upstash_redis", None)
    with pytest.raises(RuntimeError, match=r"pytincture\[redis\]"):
        RedisDict(redis_url="https://example.invalid", redis_token="token")


def test_bff_registry_owns_root_and_reload_state(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "alpha.py").write_text("# first", encoding="utf-8")
    (second / "beta.py").write_text("# second", encoding="utf-8")

    def manifest(path):
        return {("Data", "read"): {"source": Path(path).name}}

    registry = BFFRegistry(str(first), manifest)
    assert registry.operation(str(first), "alpha.py", "Data", "read") == {
        "source": "alpha.py"
    }
    assert registry.operation(str(second), "beta.py", "Data", "read") == {
        "source": "beta.py"
    }
    assert registry.root == str(second.resolve())


def test_bff_registry_can_defer_filesystem_scanning_until_first_use(tmp_path: Path):
    (tmp_path / "data.py").write_text("# data", encoding="utf-8")
    calls = []

    def manifest(path):
        calls.append(path)
        return {("Data", "read"): {}}

    registry = BFFRegistry(str(tmp_path), manifest, autoload=False)
    assert calls == []
    assert registry.operation(str(tmp_path), "data.py", "Data", "read") == {}
    assert calls == [str(tmp_path / "data.py")]


def test_browser_package_discovery_is_transitive_and_explicit(tmp_path: Path):
    (tmp_path / "demo.py").write_text("import helper\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("import nested\n", encoding="utf-8")
    (tmp_path / "nested.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "theme.css").write_text("body {}\n", encoding="utf-8")

    files = browser_package_files("demo", str(tmp_path), '["theme.css"]')
    assert {Path(path).name for path in files} == {
        "demo.py",
        "helper.py",
        "nested.py",
        "theme.css",
    }


def test_widgetset_discovery_reads_local_metadata_without_importing(tmp_path: Path):
    (tmp_path / "demo.py").write_text("import custom_widget\n", encoding="utf-8")
    (tmp_path / "custom_widget.py").write_text(
        '__widgetset__ = "custom-widget"\n__version__ = "2.3.4"\n'
        'raise RuntimeError("must not import")\n',
        encoding="utf-8",
    )
    assert discover_widgetset("demo", str(tmp_path)) == "custom-widget==2.3.4"


def test_source_names_are_stable_and_collision_resistant(tmp_path: Path):
    first = tmp_path / "one" / "data.py"
    second = tmp_path / "two" / "data.py"
    first.parent.mkdir()
    second.parent.mkdir()
    first.touch()
    second.touch()
    first_name = build_dynamic_module_name(str(first), "Data", str(tmp_path))
    assert first_name == build_dynamic_module_name(str(first), "Data", str(tmp_path))
    assert first_name != build_dynamic_module_name(str(second), "Data", str(tmp_path))


def test_mcp_policy_rejects_session_routes_and_filters_schema():
    assert exposed_operation_ids(False, "invalid JSON") == set()
    with pytest.raises(RuntimeError, match="session/login/application"):
        exposed_operation_ids(True, '["logoutUser"]')

    app = FastAPI()

    @app.get("/public", operation_id="publicRead")
    def public_read():
        return {}

    @app.post("/private", operation_id="privateWrite")
    def private_write():
        return {}

    schema = FilteredFastAPIApp(app, {"publicRead"}).openapi()
    assert set(schema["paths"]) == {"/public"}


def test_page_metadata_is_read_without_importing_app(tmp_path: Path):
    app_file = tmp_path / "demo.py"
    app_file.write_text(
        'APP_CONFIG = {"loading_title": "Demo title", "favicon": "icons/app.svg"}\n',
        encoding="utf-8",
    )
    assert (
        find_app_string_setting(str(app_file), ("APP_TITLE",), ("loading_title",))
        == "Demo title"
    )
    assert normalize_app_asset_path("/appcode/icons/app.svg") == "icons/app.svg"
    assert normalize_app_asset_path("../secret") is None


def test_diagnostics_never_echo_submitted_validation_values():
    errors = sanitized_validation_errors(
        [
            {
                "loc": ("body", "password"),
                "msg": "invalid",
                "type": "value",
                "input": "secret",
            }
        ]
    )
    assert errors == [{"loc": ("body", "password"), "msg": "invalid", "type": "value"}]
    assert internal_error_payload("request-1") == {
        "detail": "Internal server error",
        "correlation_id": "request-1",
    }
    assert request_correlation_id("edge.request-1") == "edge.request-1"
    assert request_correlation_id("invalid\nheader") != "invalid\nheader"


def test_saml_provider_catalog_is_independent_and_deterministic():
    catalog = SAMLProviderCatalog(
        {
            "Corporate Login": {
                "label": "Contoso",
                "logoUrl": "/contoso.svg",
                "allowedRoles": "Admin, Reader",
            }
        }
    )
    provider = catalog.get("corporate-login")
    assert provider["id"] == "corporate-login"
    assert catalog.login_buttons() == [
        {
            "href": "auth/saml/corporate-login/login",
            "label": "Contoso",
            "logo_url": "/contoso.svg",
        }
    ]
    assert allowed_roles(provider, []) == ["admin", "reader"]


def test_redis_store_accepts_an_injected_client():
    class FakeRedis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

        def delete(self, key):
            return int(self.values.pop(key, None) is not None)

        def getdel(self, key):
            return self.values.pop(key, None)

        def exists(self, key):
            return int(key in self.values)

        def scan(self, cursor, match, count):
            prefix = match.removesuffix("*")
            return "0", [key for key in self.values if key.startswith(prefix)]

        def ping(self):
            return True

    redis = FakeRedis()
    store = RedisDict(key_prefix="session:", redis_client=redis)
    store.set_with_ttl("one", {"email": "user@example.com"}, 60)
    assert store["one"] == {"email": "user@example.com"}
    assert store.pop_atomic("one") == {"email": "user@example.com"}
    assert store.get("one") is None
    assert store.ping() is True

    first_replay_store = RedisDict(
        key_prefix="replay:", redis_client=redis, cache_reads=False
    )
    second_replay_store = RedisDict(
        key_prefix="replay:", redis_client=redis, cache_reads=False
    )
    first_replay_store["token"] = {"session_id": "one"}
    assert second_replay_store.pop_atomic("token") == {"session_id": "one"}
    assert first_replay_store.pop_atomic("token") is None


def test_sync_stream_closes_source_at_byte_limit():
    closed = []

    def source():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    reasons = []
    assert list(
        limited_sync_stream(
            source(),
            raw=False,
            max_seconds=10,
            max_bytes=6,
            on_finish=lambda reason, size: reasons.append((reason, size)),
        )
    ) == ["first\n"]
    assert closed == [True]
    assert reasons == [("byte-limit", 13)]


def test_async_stream_timeout_closes_source():
    closed = []

    async def source():
        try:
            await __import__("asyncio").sleep(0.05)
            yield "late"
        finally:
            closed.append(True)

    reasons = []

    async def collect():
        return [
            chunk
            async for chunk in limited_async_stream(
                source(),
                raw=False,
                max_seconds=0.001,
                max_bytes=100,
                on_finish=lambda reason, size: reasons.append((reason, size)),
            )
        ]

    chunks = asyncio.run(collect())
    assert chunks == []
    assert closed == [True]
    assert reasons == [("timeout", 0)]


def test_async_stream_disconnect_closes_source():
    closed = []
    reasons = []

    async def source():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    async def consume_one_then_disconnect():
        stream = limited_async_stream(
            source(),
            raw=False,
            max_seconds=10,
            max_bytes=100,
            on_finish=lambda reason, size: reasons.append((reason, size)),
        )
        assert await anext(stream) == "first\n"
        await stream.aclose()

    asyncio.run(consume_one_then_disconnect())
    assert closed == [True]
    assert reasons == [("disconnect", 6)]
