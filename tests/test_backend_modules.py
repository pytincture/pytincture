import asyncio
import base64
import json
import sys
import time
import io
import zipfile
from importlib.metadata import version
from pathlib import Path

import pytest
from fastapi import HTTPException

from pytincture.backend.auth import (
    allowed_email,
    local_user_claims,
    normalize_roles,
    verify_password,
)
from pytincture.backend.bff import BFFRegistry
from pytincture.backend.browser_packages import (
    AppcodeArchiveCache,
    browser_package_files,
    configured_browser_asset_path_selected,
    create_appcode_archive,
    discover_widgetset,
    local_python_imports,
)
from pytincture.backend.diagnostics import (
    internal_error_payload,
    request_correlation_id,
    sanitized_validation_errors,
)
from pytincture.backend.mcp import parse_tool_specs
from pytincture.backend.pages import (
    EntryPointDiscoveryError,
    find_app_string_setting,
    find_main_window_subclass,
    normalize_app_asset_path,
)
from pytincture.backend.saml import (
    ALLOWED_XML_SIGNATURE_TRANSFORMS,
    SAMLProviderCatalog,
    SlidingWindowRateLimiter,
    allowed_roles,
    validate_saml_response_xml,
)
from pytincture.backend.safe_paths import (
    UnsafePath,
    normalize_relative_path,
    read_contained_file,
    validate_application_name,
)
from pytincture.backend.source_loading import build_dynamic_module_name, load_source_module
from pytincture.backend.storage import RedisDict
from pytincture.backend.streaming import (
    as_streaming_response,
    limited_async_stream,
    limited_sync_stream,
)
from pytincture.backend.limits import AdmissionRejected, AsyncAdmissionGate, CircuitOpen


def test_local_python_imports_honors_relative_import_levels(tmp_path):
    package = tmp_path / "pkg" / "nested"
    package.mkdir(parents=True)
    (tmp_path / "pkg" / "shared.py").write_text("VALUE = 1\n")
    (package / "helper.py").write_text("VALUE = 2\n")
    source = package / "screen.py"
    source.write_text("from . import helper\nfrom ..shared import VALUE\n")

    discovered = {
        Path(path).relative_to(tmp_path).as_posix()
        for path in local_python_imports(str(source), str(tmp_path))
    }
    assert discovered == {"pkg/nested/helper.py", "pkg/shared.py"}


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


@pytest.mark.parametrize(
    "redis_url",
    (
        "http://redis.internal:8079",
        "http://localhost:8079",
        "redis://127.0.0.1:6379",
        "https://",
    ),
)
def test_redis_client_rejects_unsafe_or_invalid_urls_before_connecting(redis_url):
    with pytest.raises(ValueError, match="redis_url"):
        RedisDict(redis_url=redis_url, redis_token="token")


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
    first_operation = registry.operation(str(first), "alpha.py", "Data", "read")
    assert first_operation["source"] == "alpha.py"
    assert first_operation["_source_path"] == str((first / "alpha.py").resolve())
    assert len(first_operation["_source_digest"]) == 64
    second_operation = registry.operation(str(second), "beta.py", "Data", "read")
    assert second_operation["source"] == "beta.py"
    assert registry.root == str(second.resolve())


def test_bff_registry_can_defer_filesystem_scanning_until_first_use(tmp_path: Path):
    (tmp_path / "data.py").write_text("# data", encoding="utf-8")
    calls = []

    def manifest(path):
        calls.append(path)
        return {("Data", "read"): {}}

    registry = BFFRegistry(str(tmp_path), manifest, autoload=False)
    assert calls == []
    operation = registry.operation(str(tmp_path), "data.py", "Data", "read")
    assert operation["_source_path"] == str((tmp_path / "data.py").resolve())
    assert calls == [str(tmp_path / "data.py")]


@pytest.mark.parametrize(
    "application",
    (
        "bad-name",
        "bad.name",
        "9bad",
        "class",
        "naïve",
        "classcall",
        "__init__",
        "CON",
        "frontend",
        "..",
        "bad\\name",
    ),
)
def test_application_names_are_strict_non_reserved_identifiers(application):
    with pytest.raises(ValueError, match="Python identifier"):
        validate_application_name(application)


def test_application_name_accepts_python_identifier():
    assert validate_application_name("reports_v2") == "reports_v2"


@pytest.mark.parametrize(
    "path",
    ("../outside.py", "pkg\\worker.py", "C:/outside.py", "/absolute.py"),
)
def test_relative_paths_reject_cross_platform_traversal(path):
    with pytest.raises(UnsafePath):
        normalize_relative_path(path)


def test_secure_read_supports_nested_packages(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    nested = package / "worker.py"
    nested.write_text("value = 42\n", encoding="utf-8")

    secure = read_contained_file(str(tmp_path), "package/worker.py")
    assert secure.content == b"value = 42\n"
    assert secure.path == str(nested.resolve())
    assert len(secure.digest) == 64


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="symlinks unavailable")
def test_secure_read_rejects_file_and_directory_symlinks(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(outside)
    outside_directory = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside_directory.mkdir()
    (outside_directory / "worker.py").write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "linked_dir").symlink_to(
        outside_directory, target_is_directory=True
    )

    with pytest.raises(UnsafePath, match="symlinks"):
        read_contained_file(str(tmp_path), "linked.py")
    with pytest.raises(UnsafePath, match="symlinks"):
        read_contained_file(str(tmp_path), "linked_dir/worker.py")


@pytest.mark.skipif(
    not hasattr(__import__("os"), "O_NOFOLLOW"),
    reason="no-follow opens unavailable",
)
def test_secure_read_rejects_swap_to_symlink_during_open(tmp_path, monkeypatch):
    import pytincture.backend.safe_paths as safe_paths

    target = tmp_path / "target.py"
    outside = tmp_path.parent / f"{tmp_path.name}-race.py"
    target.write_text("safe = True\n", encoding="utf-8")
    outside.write_text("secret = True\n", encoding="utf-8")
    original_open = safe_paths._open_relative_nofollow

    def swap_then_open(root, relative_path):
        target.unlink()
        target.symlink_to(outside)
        return original_open(root, relative_path)

    monkeypatch.setattr(safe_paths, "_open_relative_nofollow", swap_then_open)
    with pytest.raises(UnsafePath):
        read_contained_file(str(tmp_path), "target.py")


def test_registry_digest_is_revalidated_before_source_execution(tmp_path):
    source = tmp_path / "worker.py"
    source.write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class Worker:\n"
        "    def ping(self): return 'first'\n",
        encoding="utf-8",
    )
    registry = BFFRegistry(str(tmp_path))
    first = registry.operation(str(tmp_path), "worker.py", "Worker", "ping")

    source.write_text(source.read_text().replace("first", "second"), encoding="utf-8")
    second = registry.operation(str(tmp_path), "worker.py", "Worker", "ping")
    assert second["_source_digest"] != first["_source_digest"]

    source.write_text(source.read_text().replace("second", "third"), encoding="utf-8")
    with pytest.raises(ImportError, match="changed after registry discovery"):
        load_source_module(
            str(source),
            "Worker",
            str(tmp_path),
            expected_digest=second["_source_digest"],
        )


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


def test_configured_browser_asset_selection_applies_direct_serving_exclusions():
    patterns = '["assets/*.png", "build/*.js", ".private/*"]'

    assert configured_browser_asset_path_selected("assets/logo.png", patterns)
    assert not configured_browser_asset_path_selected("assets/logo.svg", patterns)
    assert not configured_browser_asset_path_selected("build/runtime.js", patterns)
    assert not configured_browser_asset_path_selected(".private/secret.txt", patterns)
    assert not configured_browser_asset_path_selected("../logo.png", patterns)


def test_widgetset_discovery_reads_local_metadata_without_importing(tmp_path: Path):
    (tmp_path / "demo.py").write_text("import custom_widget\n", encoding="utf-8")
    (tmp_path / "custom_widget.py").write_text(
        '__widgetset__ = "custom-widget"\n__version__ = "2.3.4"\n'
        'raise RuntimeError("must not import")\n',
        encoding="utf-8",
    )
    assert discover_widgetset("demo", str(tmp_path)) == "custom-widget==2.3.4"


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "from dhxpyt.layout import MainWindow\n"
            "class Dashboard(MainWindow): pass\n",
            "Dashboard",
        ),
        (
            "from dhxpyt.layout import MainWindow as Window\n"
            "class Dashboard(Window): pass\n",
            "Dashboard",
        ),
        (
            "import dhxpyt.layout as layout\n"
            "class Dashboard(layout.MainWindow): pass\n",
            "Dashboard",
        ),
        (
            "from dhxpyt import layout as ui\n"
            "class Dashboard(ui.MainWindow): pass\n",
            "Dashboard",
        ),
        (
            "import dhxpyt\n"
            "class Dashboard(dhxpyt.layout.MainWindow): pass\n",
            "Dashboard",
        ),
        (
            "from dhxpyt.layout import MainWindow\n"
            "Window = MainWindow\n"
            "class Dashboard(Window): pass\n",
            "Dashboard",
        ),
    ),
)
def test_entrypoint_discovery_supports_static_main_window_aliases(source, expected):
    assert (
        find_main_window_subclass("dashboard.py", source_code=source) == expected
    )


def test_entrypoint_discovery_uses_source_order_not_alphabetical_order():
    source = (
        "from dhxpyt.layout import MainWindow\n"
        "class Zebra(MainWindow): pass\n"
        "class Alpha(MainWindow): pass\n"
    )
    assert find_main_window_subclass("dashboard.py", source_code=source) == "Zebra"


@pytest.mark.parametrize(
    "source",
    (
        'APP_ENTRYPOINT = "Dashboard"\nclass Dashboard: pass\n',
        'APP_CONFIG = {"entrypoint": "Dashboard"}\nclass Dashboard: pass\n',
        'APP_ENTRYPOINT = "start"\ndef start(): pass\n',
    ),
)
def test_entrypoint_discovery_supports_literal_explicit_metadata(source):
    assert find_main_window_subclass("dashboard.py", source_code=source) in {
        "Dashboard",
        "start",
    }


def test_entrypoint_discovery_supports_conventional_indirect_subclass():
    source = "from shared import BrowserWindow\nclass dashboard(BrowserWindow): pass\n"
    assert find_main_window_subclass("dashboard.py", source_code=source) == "dashboard"


def test_entrypoint_discovery_never_invokes_legacy_loader():
    def forbidden_loader(*_args):
        raise AssertionError("browser source was executed")

    source = "from dhxpyt.layout import MainWindow\nclass Dashboard(MainWindow): pass\n"
    assert (
        find_main_window_subclass(
            "dashboard.py", forbidden_loader, source_code=source
        )
        == "Dashboard"
    )


def test_entrypoint_discovery_requires_metadata_for_unresolved_custom_base():
    source = "from shared import BrowserWindow\nclass Dashboard(BrowserWindow): pass\n"
    with pytest.raises(EntryPointDiscoveryError, match="APP_ENTRYPOINT"):
        find_main_window_subclass("dashboard.py", source_code=source)


@pytest.mark.parametrize(
    ("source", "message"),
    (
        ("APP_ENTRYPOINT = choose_entrypoint()\n", "must be a literal"),
        ('APP_ENTRYPOINT = "missing"\n', "is not a top-level"),
        ('APP_ENTRYPOINT = "bad-name"\nclass Dashboard: pass\n', "Python identifier"),
        ("class Broken(\n", "Unable to parse"),
    ),
)
def test_entrypoint_discovery_rejects_unsafe_dynamic_patterns(source, message):
    with pytest.raises(EntryPointDiscoveryError, match=message):
        find_main_window_subclass("dashboard.py", source_code=source)


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


def test_mcp_policy_accepts_only_exact_purpose_bound_tools():
    specs = parse_tool_specs(json.dumps([{
        "name": "read_status", "application": "demoapp", "module": "status.py",
        "class": "Status", "method": "read", "scopes": ["demo:status:read"],
    }]))
    assert specs[0].module == "status.py"
    assert specs[0].scopes == ("demo:status:read",)

    with pytest.raises(RuntimeError, match="must contain only"):
        parse_tool_specs('[{"name":"dispatch","operation":"postClassCall"}]')


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


def test_saml_response_transform_guard_accepts_only_bounded_safe_algorithms():
    safe_response = b"""<samlp:Response
        xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
        xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
      <ds:Signature><ds:SignedInfo>
        <ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        <ds:Reference><ds:Transforms>
          <ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
          <ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        </ds:Transforms></ds:Reference>
      </ds:SignedInfo></ds:Signature>
    </samlp:Response>"""

    encoded = base64.b64encode(safe_response).decode("ascii")
    assert validate_saml_response_xml(encoded, len(safe_response)) == safe_response

    disallowed = (
        Path(__file__).parent / "fixtures" / "saml" / "disallowed-xslt-transform.xml"
    ).read_bytes()
    with pytest.raises(ValueError, match="disallowed signature transform"):
        validate_saml_response_xml(
            base64.b64encode(disallowed).decode("ascii"),
            len(disallowed),
        )


def test_saml_response_transform_guard_rejects_unsafe_or_oversized_xml():
    with pytest.raises(ValueError, match="DTD or entity"):
        validate_saml_response_xml(
            base64.b64encode(b"<!DOCTYPE Response><Response/>").decode("ascii"),
            1024,
        )
    with pytest.raises(ValueError, match="decoded size limit"):
        validate_saml_response_xml(
            base64.b64encode(b"<Response>too large</Response>").decode("ascii"),
            8,
        )
    with pytest.raises(ValueError, match="valid base64"):
        validate_saml_response_xml("not base64!", 1024)


def test_saml_rate_limiter_is_windowed_and_memory_bounded():
    now = [10.0]
    limiter = SlidingWindowRateLimiter(
        2,
        5,
        max_keys=2,
        clock=lambda: now[0],
    )
    assert limiter.allow("first") == (True, 0)
    assert limiter.allow("first") == (True, 0)
    allowed, retry_after = limiter.allow("first")
    assert allowed is False
    assert retry_after == 5
    limiter.allow("second")
    limiter.allow("third")
    assert len(limiter._entries) == 2
    now[0] = 16.0
    assert limiter.allow("first") == (True, 0)


def test_saml_mitigation_evidence_matches_the_enforced_runtime():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "security" / "saml-transform-mitigation.json").read_text()
    )
    assert evidence["status"] == "passed"
    assert evidence["upstream_status"] == "open"
    assert evidence["dependency"] == f"python3-saml=={version('python3-saml')}"
    assert evidence["mitigations"]["strict_transform_allowlist"] is True
    assert evidence["mitigations"]["guard_runs_before_toolkit_signature_processing"] is True
    assert evidence["safe_fixture"] == (
        "tests/fixtures/saml/disallowed-xslt-transform.xml"
    )
    assert "http://www.w3.org/TR/1999/REC-xslt-19991116" not in (
        ALLOWED_XML_SIGNATURE_TRANSFORMS
    )


def test_saml_replay_mitigation_evidence_matches_the_enforced_runtime():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "security" / "saml-replay-mitigation.json").read_text()
    )
    assert evidence["status"] == "passed"
    assert evidence["architecture"]["server_side_transaction_state"] is False
    assert evidence["architecture"]["redis_required"] is False
    assert evidence["architecture"]["process_memory_required"] is False
    assert (
        "tests/test_production.py::test_saml_handshake_is_portable_between_workers_without_redis"
        in evidence["regression_tests"]
    )


def test_security_review_dispositions_map_contracts_to_regressions():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads(
        (root / "security" / "review-dispositions.json").read_text()
    )
    assert evidence["status"] == "passed"
    assert evidence["review_response"] == "accepted"
    dispositions = {item["id"]: item for item in evidence["dispositions"]}
    assert set(dispositions) == {
        "F-01",
        "F-02",
        "SAML-STATELESS-REPLAY-BOUNDARY",
    }
    assert dispositions["F-01"]["controls"]["class_level_export_preserved"] is True
    assert dispositions["F-01"]["controls"]["method_level_export_required"] is False
    assert dispositions["F-02"]["controls"][
        "same_origin_browser_execution_intentional"
    ] is True
    saml_controls = dispositions["SAML-STATELESS-REPLAY-BOUNDARY"]["controls"]
    assert saml_controls["redis_required"] is False
    assert saml_controls["process_memory_required"] is False

    for disposition in dispositions.values():
        for relative_path in disposition["implementation"]:
            assert (root / relative_path).is_file()
        for test_reference in disposition["regression_tests"]:
            relative_path, separator, test_name = test_reference.partition("::")
            test_path = root / relative_path
            assert test_path.is_file()
            assert separator and test_name in test_path.read_text()


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


def test_admission_gate_rejects_saturation_then_recovers():
    async def exercise():
        gate = AsyncAdmissionGate(1, 0, 0.01)
        await gate.acquire()
        with pytest.raises(AdmissionRejected):
            await gate.acquire()
        gate.release()
        await gate.acquire()
        gate.release()

    asyncio.run(exercise())


def test_async_stream_enforces_item_and_idle_limits():
    reasons = []

    async def many_items():
        for value in range(4):
            yield value

    async def idle_item():
        await asyncio.sleep(0.03)
        yield "late"

    async def collect(source, **limits):
        return [
            item
            async for item in limited_async_stream(
                source,
                raw=False,
                max_seconds=1,
                max_bytes=100,
                on_finish=lambda reason, size: reasons.append((reason, size)),
                **limits,
            )
        ]

    assert asyncio.run(collect(many_items(), max_items=2, idle_timeout_seconds=1)) == [
        "0\n",
        "1\n",
    ]
    assert reasons[-1][0] == "item-limit"
    assert asyncio.run(collect(idle_item(), max_items=2, idle_timeout_seconds=0.001)) == []
    assert reasons[-1] == ("idle-timeout", 0)


def test_existing_streaming_response_is_wrapped_by_limits():
    from fastapi.responses import StreamingResponse

    original = StreamingResponse(iter([b"one", b"two", b"three"]), status_code=206)
    bounded = as_streaming_response(
        original,
        raw=False,
        media_type="text/plain",
        max_seconds=1,
        max_bytes=100,
        max_items=2,
        idle_timeout_seconds=1,
    )

    async def collect():
        return [chunk async for chunk in bounded.body_iterator]

    assert bounded is not original
    assert bounded.status_code == 206
    assert asyncio.run(collect()) == [b"one", b"two"]


def test_appcode_archive_limits_and_cache(tmp_path):
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    calls = []

    def parser(path, host, protocol, **kwargs):
        calls.append(path)
        return Path(path).read_text(encoding="utf-8")

    cache = AppcodeArchiveCache(1)
    first = create_appcode_archive(
        "", "", "demo", str(tmp_path), parser, cache=cache
    )
    second = create_appcode_archive(
        "", "", "demo", str(tmp_path), parser, cache=cache
    )
    assert zipfile.ZipFile(io.BytesIO(first.getvalue())).read("demo.py") == b"value = 1\n"
    assert second.getvalue() == first.getvalue()
    assert len(calls) == 1

    with pytest.raises(HTTPException, match="file-size limit"):
        create_appcode_archive(
            "", "", "demo", str(tmp_path), parser, max_file_bytes=2
        )

    (tmp_path / "helper.py").write_text("helper = 2\n", encoding="utf-8")
    (tmp_path / "demo.py").write_text("import helper\nvalue = 1\n", encoding="utf-8")
    with pytest.raises(HTTPException, match="file-count limit"):
        create_appcode_archive(
            "", "", "demo", str(tmp_path), parser, max_files=1
        )
    with pytest.raises(HTTPException, match="aggregate-size limit"):
        create_appcode_archive(
            "", "", "demo", str(tmp_path), parser, max_total_bytes=20
        )


def test_remote_store_circuit_opens_and_recovers():
    class FailingRedis:
        def __init__(self):
            self.calls = 0

        def get(self, key):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("remote deadline")
            return "ok"

    redis = FailingRedis()
    store = RedisDict(
        redis_client=redis,
        cache_reads=False,
        failure_threshold=1,
        cooldown_seconds=0.01,
    )
    with pytest.raises(TimeoutError):
        store.get("key")
    with pytest.raises(CircuitOpen):
        store.get("key")
    assert redis.calls == 1
    time.sleep(0.02)
    assert store.get("key") == "ok"
