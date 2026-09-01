import asyncio
import base64
import hashlib
import json
import sys
import threading
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
    browser_asset_path_is_safe,
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
from pytincture.backend.execution import (
    IsolatedBFFInvocation,
    IsolatedExecutionFailed,
    IsolatedExecutionRejected,
    IsolatedExecutionTimeout,
    ProcessIsolatedBFFExecutor,
)
from pytincture.backend.mcp import parse_tool_specs
from pytincture.backend.pages import (
    EntryPointDiscoveryError,
    find_app_string_setting,
    find_main_window_subclass,
    normalize_app_asset_path,
)
from pytincture.backend.replay import (
    LocalReplayStore,
    ReplayAdmissionRejected,
    SharedReplayStoreAdapter,
    validate_atomic_replay_store,
)
from pytincture.backend.results import BFFResultLimitExceeded, encode_bff_result
from pytincture.backend.saml import (
    ALLOWED_XML_DIGEST_METHODS,
    ALLOWED_XML_SIGNATURE_METHODS,
    ALLOWED_XML_SIGNATURE_TRANSFORMS,
    SAMLProviderCatalog,
    SlidingWindowRateLimiter,
    allowed_roles,
    saml_assertion_expirations,
    validate_authenticated_saml_correlation,
    validate_saml_response_xml,
)
from pytincture.backend.safe_paths import (
    UnsafePath,
    normalize_relative_path,
    read_contained_file,
    stat_contained_file,
    validate_application_name,
)
from pytincture.backend.source_loading import build_dynamic_module_name, load_source_module
from pytincture.dataclass import get_parsed_output
from pytincture.dataclass import get_bff_manifest
from pytincture.backend.storage import RedisDict
from pytincture.backend.streaming import (
    as_streaming_response,
    limited_async_stream,
    limited_sync_stream,
)
from pytincture.backend.limits import AdmissionRejected, AsyncAdmissionGate, CircuitOpen
from pytincture.backend.widget_trust import (
    WidgetTrustPolicyError,
    canonical_widget_trust_policy,
    trusted_widget_manifest,
)


def test_bff_result_encoder_stops_at_byte_depth_and_item_limits():
    assert encode_bff_result({"ok": True}, max_bytes=32) == b'{"ok":true}'
    with pytest.raises(BFFResultLimitExceeded, match="byte"):
        encode_bff_result("x" * 33, max_bytes=32)
    with pytest.raises(BFFResultLimitExceeded, match="nesting"):
        encode_bff_result([[[1]]], max_bytes=100, max_depth=2)
    with pytest.raises(BFFResultLimitExceeded, match="item"):
        encode_bff_result([1, 2, 3], max_bytes=100, max_items=2)


def _isolated_test_invocation(path, operation, member, *, wall_time=2.0):
    return IsolatedBFFInvocation(
        module_path=str(path),
        modules_root=str(path.parent),
        class_name="Worker",
        member_name=member,
        source_digest=hashlib.sha256(path.read_bytes()).hexdigest(),
        operation=operation,
        user={"email": "user@example.test"},
        args=(),
        kwargs={},
        subject="session-one",
        wall_time_seconds=wall_time,
    )


def test_optional_process_executor_kills_cpu_work_and_recovers(tmp_path):
    source = """
from pytincture.dataclass import backend_for_frontend

@backend_for_frontend
class Worker:
    def __init__(self, _user):
        self.user = _user

    def spin(self):
        while True:
            pass

    def ping(self):
        return {"ready": True}
"""
    module_path = tmp_path / "isolated_worker.py"
    module_path.write_text(source, encoding="utf-8")
    manifest = get_bff_manifest(str(module_path))
    executor = ProcessIsolatedBFFExecutor(
        max_concurrency=1,
        max_per_user=1,
        cpu_seconds=5,
        memory_bytes=64 * 1024 * 1024 * 1024,
        result_max_bytes=1024,
        result_max_depth=8,
        result_max_items=100,
    )

    with pytest.raises(IsolatedExecutionTimeout):
        executor.execute(
            _isolated_test_invocation(
                module_path,
                manifest[("Worker", "spin")],
                "spin",
                wall_time=0.2,
            )
        )
    assert executor.active_users == {}
    assert json.loads(
        executor.execute(
            _isolated_test_invocation(
                module_path,
                manifest[("Worker", "ping")],
                "ping",
            )
        )
    ) == {"ready": True}


def test_optional_process_executor_enforces_per_user_and_output_limits(tmp_path):
    module_path = tmp_path / "isolated_output.py"
    module_path.write_text(
        """
from pytincture.dataclass import backend_for_frontend

@backend_for_frontend
class Worker:
    def __init__(self, _user): pass
    def large(self): return "x" * 100
""",
        encoding="utf-8",
    )
    operation = get_bff_manifest(str(module_path))[("Worker", "large")]
    executor = ProcessIsolatedBFFExecutor(
        max_concurrency=1,
        max_per_user=1,
        cpu_seconds=2,
        memory_bytes=64 * 1024 * 1024 * 1024,
        result_max_bytes=16,
        result_max_depth=8,
        result_max_items=100,
    )

    executor._acquire("session-one")
    try:
        with pytest.raises(IsolatedExecutionRejected, match="capacity"):
            executor._acquire("session-one")
    finally:
        executor._release("session-one")
    with pytest.raises(BFFResultLimitExceeded):
        executor.execute(
            _isolated_test_invocation(module_path, operation, "large")
        )


def test_isolated_process_never_unpickles_child_controlled_messages(tmp_path):
    marker_path = tmp_path / "parent-unpickle-proof"
    module_path = tmp_path / "isolated_pickle_attack.py"
    module_path.write_text(
        f'''
import inspect
from pathlib import Path
from pytincture.dataclass import backend_for_frontend

class ParentPayload:
    def __reduce__(self):
        return (Path.write_text, (Path({str(marker_path)!r}), "unsafe"))

@backend_for_frontend
class Worker:
    def __init__(self, _user): pass
    def attack(self):
        frame = inspect.currentframe()
        while frame is not None:
            connection = frame.f_locals.get("connection")
            if connection is not None:
                connection.send(ParentPayload())
                return {{"sent": True}}
            frame = frame.f_back
        return {{"sent": False}}
''',
        encoding="utf-8",
    )
    operation = get_bff_manifest(str(module_path))[("Worker", "attack")]
    executor = ProcessIsolatedBFFExecutor(
        max_concurrency=1,
        max_per_user=1,
        cpu_seconds=2,
        memory_bytes=64 * 1024 * 1024 * 1024,
        result_max_bytes=1024,
        result_max_depth=8,
        result_max_items=100,
    )

    with pytest.raises(IsolatedExecutionFailed, match="invalid"):
        executor.execute(
            _isolated_test_invocation(module_path, operation, "attack")
        )

    assert marker_path.exists() is False
    assert executor.active_users == {}


@pytest.mark.parametrize(
    "message",
    [
        b"not-pytincture",
        b"PTB1O{\"duplicate\":1,\"duplicate\":2}",
        b"PTB1ONaN",
        b"PTB1O{ \"noncanonical\": true }",
        b"PTB1Funexpected",
    ],
)
def test_isolated_process_rejects_malformed_child_messages(message):
    from pytincture.backend.execution import _decode_isolated_message

    with pytest.raises(IsolatedExecutionFailed, match="invalid"):
        _decode_isolated_message(
            message,
            result_max_bytes=1024,
            result_max_depth=8,
            result_max_items=100,
        )


def test_local_replay_store_is_bounded_expiration_indexed_and_single_use():
    now = [100.0]
    store = LocalReplayStore(3, 2, clock=lambda: now[0])

    store.issue_batch(
        "session-a",
        {"one": {"value": 1}, "two": {"value": 2}},
        10,
    )
    assert len(store) == 2
    with pytest.raises(ReplayAdmissionRejected, match="session"):
        store.issue_batch("session-a", {"three": {"value": 3}}, 10)

    assert store.consume("one") == {"value": 1}
    assert store.consume("one") is None
    store.issue_batch("session-b", {"three": {"value": 3}}, 10)
    assert len(store) == 2

    now[0] = 111.0
    assert len(store) == 0
    assert store.consume("two") is None

    for index in range(20):
        key = f"rapid-{index}"
        store.issue_batch("session-a", {key: {"value": index}}, 10)
        assert store.consume(key) == {"value": index}
    assert store.expiration_index_size <= store.max_entries * 2


def test_local_replay_store_rejects_worker_capacity_without_partial_batch():
    store = LocalReplayStore(2, 2)

    with pytest.raises(ReplayAdmissionRejected, match="worker"):
        store.issue_batch(
            "session-a",
            {"one": {"value": 1}, "two": {"value": 2}, "three": {"value": 3}},
            60,
        )

    assert len(store) == 0


def test_shared_replay_adapter_exposes_vendor_neutral_atomic_contract():
    class AtomicTTLMapping:
        def __init__(self):
            self.values = {}

        def set_with_ttl(self, key, value, ttl):
            self.values[key] = value

        def pop_atomic(self, key, default=None):
            return self.values.pop(key, default)

    backend = AtomicTTLMapping()
    store = SharedReplayStoreAdapter(backend)
    assert validate_atomic_replay_store(store) is store
    assert store.shared_across_workers is True

    store.issue_batch("session", {"proof": {"session_id": "session"}}, 60)
    assert store.consume("proof") == {"session_id": "session"}
    assert store.consume("proof") is None


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


def test_bff_registry_isolates_invalid_source_files_and_recovers(tmp_path: Path):
    healthy = tmp_path / "healthy.py"
    healthy.write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class Healthy:\n"
        "    def ping(self): return True\n",
        encoding="utf-8",
    )
    broken = tmp_path / "broken.py"
    broken.write_text("def incomplete(\n", encoding="utf-8")
    invalid_encoding = tmp_path / "invalid_encoding.py"
    invalid_encoding.write_bytes(b"# coding: utf-8\n\xff\n")

    registry = BFFRegistry(str(tmp_path))

    assert registry.operation(
        str(tmp_path), "healthy.py", "Healthy", "ping"
    ) is not None
    assert registry.operation(
        str(tmp_path), "broken.py", "Broken", "ping"
    ) is None
    assert registry.failures == {
        "broken.py": "invalid_python_syntax",
        "invalid_encoding.py": "invalid_python_encoding",
    }

    broken.write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\n"
        "class Broken:\n"
        "    def ping(self): return 'recovered'\n",
        encoding="utf-8",
    )
    assert registry.operation(
        str(tmp_path), "broken.py", "Broken", "ping"
    ) is None
    registry.reload()
    recovered = registry.operation(
        str(tmp_path), "broken.py", "Broken", "ping"
    )
    assert recovered is not None
    assert "broken.py" not in registry.failures
    assert registry.failures == {
        "invalid_encoding.py": "invalid_python_encoding"
    }


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


def test_secure_stat_reads_metadata_without_reading_file_contents(tmp_path, monkeypatch):
    import pytincture.backend.safe_paths as safe_paths

    target = tmp_path / "large.bin"
    target.write_bytes(b"x" * (2 * 1024 * 1024))

    def reject_body_read(*_args, **_kwargs):
        raise AssertionError("metadata lookup read file contents")

    monkeypatch.setattr(safe_paths.os, "read", reject_body_read)
    metadata = stat_contained_file(str(tmp_path), "large.bin")

    assert metadata.size == target.stat().st_size
    assert metadata.path == str(target.resolve())


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


def test_browser_package_conservatively_includes_conditional_browser_imports(
    tmp_path: Path,
):
    (tmp_path / "demo.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import typing_model\n"
        "if False:\n"
        "    import conditional_plugin\n",
        encoding="utf-8",
    )
    (tmp_path / "typing_model.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "conditional_plugin.py").write_text("VALUE = 2\n", encoding="utf-8")

    files = {Path(path).name for path in browser_package_files("demo", str(tmp_path))}
    assert files == {"conditional_plugin.py", "demo.py", "typing_model.py"}


def test_browser_package_stops_at_bff_server_import_boundary(tmp_path: Path):
    (tmp_path / "demo.py").write_text(
        "import browser_helper\nimport bff_data\n", encoding="utf-8"
    )
    (tmp_path / "browser_helper.py").write_text(
        "BROWSER_VALUE = 1\n", encoding="utf-8"
    )
    (tmp_path / "bff_data.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "from server_secret import DATABASE_PASSWORD\n"
        "@backend_for_frontend\n"
        "class Data:\n"
        "    def read(self): return DATABASE_PASSWORD\n",
        encoding="utf-8",
    )
    (tmp_path / "server_secret.py").write_text(
        "DATABASE_PASSWORD = 'must-not-enter-browser'\n", encoding="utf-8"
    )

    files = browser_package_files("demo", str(tmp_path))
    assert {Path(path).name for path in files} == {
        "demo.py",
        "browser_helper.py",
        "bff_data.py",
    }

    archive = create_appcode_archive(
        "", "", "demo", str(tmp_path), get_parsed_output
    )
    with zipfile.ZipFile(io.BytesIO(archive.getvalue())) as package:
        assert set(package.namelist()) == {
            "bff_data.py",
            "browser_helper.py",
            "demo.py",
        }
        assert "must-not-enter-browser" not in "\n".join(
            package.read(name).decode("utf-8")
            for name in package.namelist()
        )


def test_browser_package_keeps_independently_selected_browser_imports(tmp_path: Path):
    (tmp_path / "demo.py").write_text(
        "import bff_data\nimport shared_model\n", encoding="utf-8"
    )
    (tmp_path / "bff_data.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "import shared_model\n"
        "import server_secret\n"
        "@backend_for_frontend\n"
        "class Data:\n"
        "    def read(self): return True\n",
        encoding="utf-8",
    )
    (tmp_path / "shared_model.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "server_secret.py").write_text("SECRET = True\n", encoding="utf-8")

    files = browser_package_files("demo", str(tmp_path))
    assert {Path(path).name for path in files} == {
        "demo.py",
        "bff_data.py",
        "shared_model.py",
    }


def test_bff_only_package_uses_namespace_without_server_initializer(tmp_path: Path):
    server_package = tmp_path / "server_package"
    server_package.mkdir()
    (tmp_path / "demo.py").write_text("import server_package.bff\n", encoding="utf-8")
    (server_package / "__init__.py").write_text(
        "PACKAGE_SECRET = 'must-not-enter-browser'\nimport server_secret\n",
        encoding="utf-8",
    )
    (server_package / "bff.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "import server_secret\n"
        "@backend_for_frontend\n"
        "class Data:\n"
        "    def read(self): return True\n",
        encoding="utf-8",
    )
    (tmp_path / "server_secret.py").write_text("SECRET = True\n", encoding="utf-8")

    files = {
        Path(path).relative_to(tmp_path).as_posix()
        for path in browser_package_files("demo", str(tmp_path))
    }
    assert files == {"demo.py", "server_package/bff.py"}


def test_browser_package_initializer_remains_for_browser_module(tmp_path: Path):
    browser_package = tmp_path / "browser_package"
    browser_package.mkdir()
    (tmp_path / "demo.py").write_text("import browser_package.ui\n", encoding="utf-8")
    (browser_package / "__init__.py").write_text(
        "from . import shared\n", encoding="utf-8"
    )
    (browser_package / "ui.py").write_text("VALUE = 1\n", encoding="utf-8")
    (browser_package / "shared.py").write_text("SHARED = 2\n", encoding="utf-8")

    files = {
        Path(path).relative_to(tmp_path).as_posix()
        for path in browser_package_files("demo", str(tmp_path))
    }
    assert files == {
        "browser_package/__init__.py",
        "browser_package/shared.py",
        "browser_package/ui.py",
        "demo.py",
    }


@pytest.mark.parametrize(
    "relative_path",
    (
        ".env",
        ".env.production",
        "config/credentials.json",
        "config/server_secret.py",
        "keys/id_rsa",
        "keys/private.key",
        "data/app.sqlite3",
        "backup/settings.bak",
    ),
)
def test_sensitive_browser_file_paths_are_rejected(relative_path):
    assert not browser_asset_path_is_safe(relative_path)


def test_configured_browser_files_fail_closed_on_sensitive_match(tmp_path: Path):
    (tmp_path / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=must-not-leak\n", encoding="utf-8")
    (tmp_path / "theme.css").write_text("body {}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="hidden or sensitive"):
        browser_package_files("demo", str(tmp_path), '["*"]')


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


def test_widgetset_discovery_skips_distribution_scan_for_stdlib_imports(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "demo.py").write_text(
        "import asyncio\nimport json\nimport custom_widget\n", encoding="utf-8"
    )
    (tmp_path / "custom_widget.py").write_text(
        '__widgetset__ = "custom-widget"\n__version__ = "2.3.4"\n',
        encoding="utf-8",
    )

    def reject_distribution_scan():
        raise AssertionError("stdlib imports must not scan installed distributions")

    monkeypatch.setattr(
        "pytincture.backend.browser_packages.importlib_metadata.packages_distributions",
        reject_distribution_scan,
    )

    assert discover_widgetset("demo", str(tmp_path)) == "custom-widget==2.3.4"


def test_widgetset_discovery_reads_installed_distribution_without_importing(
    tmp_path: Path, monkeypatch
):
    application_root = tmp_path / "applications"
    application_root.mkdir()
    (application_root / "demo.py").write_text(
        "import external_widget\n", encoding="utf-8"
    )

    package_root = tmp_path / "site-packages"
    package = package_root / "external_widget"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '__widgetset__ = "external-widget"\n'
        '__version__ = "999.0"\n'
        'raise RuntimeError("server must not import browser package")\n',
        encoding="utf-8",
    )
    dist_info = package_root / "external_widget-4.5.6.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: external-widget\nVersion: 4.5.6\n",
        encoding="utf-8",
    )
    (dist_info / "top_level.txt").write_text("external_widget\n", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "external_widget/__init__.py,,\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(package_root))

    assert "external_widget" not in sys.modules
    assert discover_widgetset("demo", str(application_root)) == "external-widget==4.5.6"
    assert "external_widget" not in sys.modules


def test_widget_trust_policy_is_an_exact_administrator_allowlist():
    policy = canonical_widget_trust_policy(
        json.dumps(
            {
                "schema": 1,
                "widgetsets": [
                    {
                        "distribution": "Demo_Widget",
                        "version": "1.2.3",
                        "assets": [
                            {
                                "path": "demo_widget/widget.js",
                                "type": "javascript",
                                "sha256": "a" * 64,
                            }
                        ],
                    }
                ],
            }
        )
    )

    assert trusted_widget_manifest(policy, "demo-widget==1.2.3") == {
        "schema": 1,
        "package": "demo-widget",
        "version": "1.2.3",
        "assets": [
            {
                "path": "demo_widget/widget.js",
                "type": "javascript",
                "sha256": "a" * 64,
            }
        ],
    }
    with pytest.raises(WidgetTrustPolicyError, match="not allowed"):
        trusted_widget_manifest(policy, "demo-widget==1.2.4")


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
    assert first_name.endswith(
        hashlib.sha256(str(first.resolve()).encode("utf-8")).hexdigest()[:12]
    )


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
        <ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>
        <ds:Reference><ds:Transforms>
          <ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
          <ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
        </ds:Transforms>
        <ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>
        </ds:Reference>
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

    encrypted = (
        Path(__file__).parent / "fixtures" / "saml" / "encrypted-assertion.xml"
    ).read_bytes()
    with pytest.raises(ValueError, match="encrypted SAML assertions"):
        validate_saml_response_xml(
            base64.b64encode(encrypted).decode("ascii"),
            len(encrypted),
        )


@pytest.mark.parametrize(
    ("element", "algorithm", "message"),
    (
        ("SignatureMethod", "http://www.w3.org/2000/09/xmldsig#rsa-sha1", "signature"),
        ("DigestMethod", "http://www.w3.org/2000/09/xmldsig#sha1", "digest"),
        ("SignatureMethod", "urn:example:unknown-signature", "signature"),
        ("DigestMethod", "urn:example:unknown-digest", "digest"),
    ),
)
def test_saml_response_rejects_deprecated_or_unknown_signature_algorithms(
    element,
    algorithm,
    message,
):
    response = (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
        f'<ds:{element} Algorithm="{algorithm}"/>'
        "</samlp:Response>"
    ).encode()
    with pytest.raises(ValueError, match=f"disallowed {message} algorithm"):
        validate_saml_response_xml(
            base64.b64encode(response).decode("ascii"),
            len(response),
        )


def test_saml_algorithm_policy_requires_sha256_or_stronger():
    assert all("sha1" not in value.casefold() for value in ALLOWED_XML_SIGNATURE_METHODS)
    assert all("sha1" not in value.casefold() for value in ALLOWED_XML_DIGEST_METHODS)
    assert any("sha256" in value.casefold() for value in ALLOWED_XML_SIGNATURE_METHODS)
    assert any("sha256" in value.casefold() for value in ALLOWED_XML_DIGEST_METHODS)


def test_saml_correlation_accepts_response_or_assertion_signed_evidence():
    response_signed = b"""<samlp:Response
      xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
      xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
      xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
      InResponseTo="request-one">
      <ds:Signature/>
      <saml:Assertion/>
    </samlp:Response>"""
    assertion_signed = b"""<samlp:Response
      xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
      xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
      xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
      InResponseTo="request-one">
      <saml:Assertion>
        <ds:Signature/>
        <saml:Subject><saml:SubjectConfirmation>
          <saml:SubjectConfirmationData InResponseTo="request-one"/>
        </saml:SubjectConfirmation></saml:Subject>
      </saml:Assertion>
    </samlp:Response>"""

    validate_authenticated_saml_correlation(response_signed, "request-one")
    validate_authenticated_saml_correlation(assertion_signed, "request-one")


@pytest.mark.parametrize(
    "assertion_correlation",
    ("", 'InResponseTo="different-request"'),
)
def test_assertion_only_saml_correlation_rejects_unsigned_response_rewrapping(
    assertion_correlation,
):
    rewrapped = f"""<samlp:Response
      xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
      xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
      xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
      InResponseTo="request-one">
      <saml:Assertion>
        <ds:Signature/>
        <saml:Subject><saml:SubjectConfirmation>
          <saml:SubjectConfirmationData {assertion_correlation}/>
        </saml:SubjectConfirmation></saml:Subject>
      </saml:Assertion>
    </samlp:Response>"""
    with pytest.raises(ValueError, match="not covered"):
        validate_authenticated_saml_correlation(rewrapped, "request-one")


def test_saml_assertion_expirations_include_conditions_confirmation_and_session():
    response = b"""<samlp:Response
      xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
      xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
      <saml:Assertion>
        <saml:Conditions NotOnOrAfter="2030-01-01T00:00:03Z"/>
        <saml:Subject><saml:SubjectConfirmation>
          <saml:SubjectConfirmationData NotOnOrAfter="2030-01-01T00:00:02Z"/>
        </saml:SubjectConfirmation></saml:Subject>
        <saml:AuthnStatement SessionNotOnOrAfter="2030-01-01T00:00:01Z"/>
      </saml:Assertion>
    </samlp:Response>"""
    assert saml_assertion_expirations(response) == [
        1893456003.0,
        1893456002.0,
        1893456001.0,
    ]


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
    assert evidence["mitigations"]["encrypted_assertions_supported"] is False
    assert (
        evidence["mitigations"]["encrypted_assertions_rejected_before_toolkit"]
        is True
    )
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
    assert evidence["active_review_tracking_document"] == (
        "security/review-2026-09-01.json"
    )
    active_review = json.loads(
        (root / evidence["active_review_tracking_document"]).read_text()
    )
    assert active_review["status"] == "remediation_in_progress"
    assert len(active_review["findings"]) == 12
    statuses = {item["id"]: item["status"] for item in active_review["findings"]}
    assert statuses["REVIEW-2026-09-01-H4"] == "remediated"
    assert statuses["REVIEW-2026-09-01-H5"] == "remediated"
    assert list(statuses.values()).count("open") == 10
    assert all(
        item["issue"].startswith("https://github.com/pytincture/")
        for item in active_review["findings"]
    )
    assert (
        active_review["architecture_constraints"][
            "class_level_bff_export_preserved"
        ]
        is True
    )
    assert active_review["architecture_constraints"]["redis_required"] is False
    dispositions = {item["id"]: item for item in evidence["dispositions"]}
    assert set(dispositions) == {
        "F-01",
        "F-02",
        "FOLLOWUP-F-01",
        "FOLLOWUP-F-02",
        "FOLLOWUP-F-03",
        "FOLLOWUP-F-04",
        "FOLLOWUP-F-05",
        "OBS-BFF-REGISTRY-BLAST-RADIUS",
        "REVIEW-2026-08-31-H-01",
        "REVIEW-2026-08-31-H-02",
        "REVIEW-2026-08-31-H-03",
        "REVIEW-2026-08-31-H-05",
        "REVIEW-2026-08-31-H-08-M-25",
        "REVIEW-2026-08-31-H-09",
        "REVIEW-2026-08-31-M-02-M-03",
        "REVIEW-2026-08-31-M-06-GET",
        "REVIEW-2026-08-31-M-07-POLICY",
        "REVIEW-2026-08-31-M-08-REPLAY-ISSUANCE",
        "REVIEW-2026-08-31-M-09-BFF-EXECUTION",
        "REVIEW-2026-08-31-M-10-PUBLIC-FILE-APPCODE-CACHE",
        "REVIEW-2026-08-31-M-11-M-12-BLOCKING-SHARED-STORE",
        "REVIEW-2026-08-31-M-13-M-14-LOGS-DOCS-HSTS",
        "REVIEW-2026-08-31-M-19-M-21-M-23-SECRET-SCAN",
        "REVIEW-2026-08-31-M-17-WIDGET-TRUST",
        "REVIEW-2026-08-31-M-15-M-16-M-18-PREAUTH-CSRF",
        "REVIEW-2026-08-31-M-24-CONTAINER-GUIDANCE",
        "REVIEW-2026-08-31-CONFIG-FACTORY-ISOLATION",
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
    registry_controls = dispositions["OBS-BFF-REGISTRY-BLAST-RADIUS"]["controls"]
    assert registry_controls["invalid_file_operations_denied"] is True
    assert registry_controls["unrelated_valid_operations_preserved"] is True
    assert registry_controls["redis_required"] is False
    logging_controls = dispositions[
        "REVIEW-2026-08-31-M-13-M-14-LOGS-DOCS-HSTS"
    ]["controls"]
    assert logging_controls["uvicorn_access_log_default"] is False
    assert logging_controls["browser_log_noauth_default"] is False
    assert logging_controls["production_edge_evidence_required_for_final"] is True
    assert logging_controls["redis_required"] is False
    assert dispositions["FOLLOWUP-F-01"]["controls"][
        "long_lived_pypi_credential_used"
    ] is False
    assert dispositions["FOLLOWUP-F-03"]["controls"]["redis_required"] is False
    assert dispositions["FOLLOWUP-F-04"]["controls"][
        "pre_transmission_redaction"
    ] is True
    archive_controls = dispositions["REVIEW-2026-08-31-H-01"]["controls"]
    assert archive_controls["bff_server_import_traversal_stops"] is True
    assert archive_controls["independent_browser_imports_preserved"] is True
    assert archive_controls["redis_required"] is False
    identity_controls = dispositions["REVIEW-2026-08-31-H-02"]["controls"]
    assert identity_controls["class_level_export_preserved"] is True
    assert identity_controls[
        "runtime_wrapper_and_member_verified_before_construction"
    ] is True
    assert identity_controls["redis_required"] is False
    scope_controls = dispositions["REVIEW-2026-08-31-H-03"]["controls"]
    assert scope_controls["unscoped_bff_routes_available"] is False
    assert scope_controls["exact_application_graph_membership_required"] is True
    assert scope_controls["redundant_method_allowlist_required"] is False
    assert scope_controls["redis_required"] is False
    admission_controls = dispositions["REVIEW-2026-08-31-H-05"]["controls"]
    assert admission_controls["configured_applications_fail_closed"] is True
    assert admission_controls["checked_before_session_issuance"] is True
    assert admission_controls["sticky_sessions_required"] is False
    assert admission_controls["redis_required"] is False
    npm_controls = dispositions["REVIEW-2026-08-31-H-09"]["controls"]
    assert npm_controls["direct_local_registry_publish_available"] is False
    assert npm_controls["protected_oidc_workflow_is_sole_publish_path"] is True
    asset_controls = dispositions["REVIEW-2026-08-31-H-08-M-25"]["controls"]
    assert asset_controls["self_hosted_standalone_production_path"] is True
    assert asset_controls["material_icons_feature_preserved"] is True
    assert asset_controls["external_pyodide_script_sri_required"] is True
    assert asset_controls["redis_required"] is False
    saml_correlation_controls = dispositions["REVIEW-2026-08-31-M-02-M-03"][
        "controls"
    ]
    assert saml_correlation_controls["unsigned_outer_response_rewrapping_rejected"] is True
    assert saml_correlation_controls["sha1_signature_and_digest_rejected"] is True
    assert saml_correlation_controls["session_capped_to_earliest_saml_expiry"] is True
    assert saml_correlation_controls["redis_required"] is False
    request_controls = dispositions["REVIEW-2026-08-31-M-06-GET"]["controls"]
    assert request_controls["single_canonical_json_representation"] is True
    assert request_controls["double_encoded_json_accepted"] is False
    assert request_controls["static_signature_bound_before_import"] is True
    assert request_controls["get_browser_metadata_validated"] is True
    assert request_controls["redis_required"] is False
    proxy_controls = dispositions["REVIEW-2026-08-31-M-07-POLICY"]["controls"]
    assert proxy_controls["all_proxy_styles_require_2xx"] is True
    assert proxy_controls["error_response_body_exposed"] is False
    assert proxy_controls["policy_false_denies"] is True
    assert proxy_controls["login_redirect_401_preserved"] is True
    assert proxy_controls["redis_required"] is False
    replay_controls = dispositions[
        "REVIEW-2026-08-31-M-08-REPLAY-ISSUANCE"
    ]["controls"]
    assert replay_controls["feature_enabled_by_default"] is False
    assert replay_controls["expiration_indexed_cleanup"] is True
    assert replay_controls["strict_shared_mode_fails_closed"] is True
    assert replay_controls["redis_required"] is False
    execution_controls = dispositions[
        "REVIEW-2026-08-31-M-09-BFF-EXECUTION"
    ]["controls"]
    assert execution_controls["ordinary_result_byte_limit"] is True
    assert execution_controls["timed_out_thread_slot_retained"] is True
    assert execution_controls["trusted_execution_default"] is True
    assert execution_controls["process_isolation_mandatory"] is False
    assert execution_controls["redis_required"] is False
    public_file_controls = dispositions[
        "REVIEW-2026-08-31-M-10-PUBLIC-FILE-APPCODE-CACHE"
    ]["controls"]
    assert public_file_controls["public_asset_buffered_in_memory"] is False
    assert public_file_controls["head_reads_asset_body"] is False
    assert public_file_controls["warm_archive_source_reread"] is False
    assert public_file_controls["cache_aggregate_byte_limit"] is True
    assert public_file_controls["redis_required"] is False
    blocking_controls = dispositions[
        "REVIEW-2026-08-31-M-11-M-12-BLOCKING-SHARED-STORE"
    ]["controls"]
    assert blocking_controls["saml_signature_off_event_loop"] is True
    assert blocking_controls["timed_out_saml_slot_retained"] is True
    assert blocking_controls["readiness_refresh_coalesced"] is True
    assert blocking_controls["remote_revocation_lookup_off_event_loop"] is True
    assert blocking_controls["redis_read_cache_default"] is False
    assert blocking_controls["negative_misses_cached"] is False
    assert blocking_controls["redis_required"] is False
    assert blocking_controls["normal_sessions_server_side"] is False
    release_controls = dispositions[
        "REVIEW-2026-08-31-M-19-M-21-M-23-SECRET-SCAN"
    ]["controls"]
    assert release_controls["uv_lock_frozen"] is True
    assert release_controls["complete_pyodide_catalog_sbom"] is True
    assert release_controls["official_pyodide_archive_verified"] is True
    assert release_controls["repository_secret_scan"] is True
    assert release_controls["redis_required"] is False
    assert release_controls["runtime_state_added"] is False
    widget_controls = dispositions[
        "REVIEW-2026-08-31-M-17-WIDGET-TRUST"
    ]["controls"]
    assert widget_controls["browser_package_imported_for_discovery"] is False
    assert widget_controls["pluggable_widgetsets_preserved"] is True
    assert widget_controls["package_manifest_is_independent_authorization_root"] is False
    assert widget_controls["configured_allowlist_fails_closed"] is True
    assert widget_controls["html_script_metadata_context_safe"] is True
    assert widget_controls["redis_required"] is False
    assert widget_controls["runtime_state_added"] is False
    session_controls = dispositions[
        "REVIEW-2026-08-31-M-15-M-16-M-18-PREAUTH-CSRF"
    ]["controls"]
    assert session_controls["production_secure_cookie_forced"] is True
    assert session_controls["mcp_json_login_one_time_csrf"] is True
    assert session_controls["total_claim_count_limit"] is True
    assert session_controls["signed_cookie_byte_limit"] is True
    assert session_controls["signed_browser_session_preserved"] is True
    assert session_controls["redis_required"] is False
    assert session_controls["process_memory_required"] is False
    assert session_controls["sticky_routing_required"] is False
    container_controls = dispositions[
        "REVIEW-2026-08-31-M-24-CONTAINER-GUIDANCE"
    ]["controls"]
    assert container_controls["official_container_currently_published"] is False
    assert container_controls["unsupported_dockerhub_image_recommended"] is False
    assert container_controls["mutable_container_tag_recommended"] is False
    assert container_controls["future_digest_reference_required"] is True
    assert container_controls["future_sbom_required"] is True
    assert container_controls["future_signature_or_attestation_required"] is True
    factory_controls = dispositions[
        "REVIEW-2026-08-31-CONFIG-FACTORY-ISOLATION"
    ]["controls"]
    assert factory_controls["typed_validation_all_startup_paths"] is True
    assert factory_controls["non_finite_limits_rejected"] is True
    assert factory_controls["factory_environment_process_global_mutation"] is False
    assert factory_controls["concurrent_factories_isolated"] is True
    assert factory_controls["redis_required"] is False

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


def test_redis_read_cache_is_opt_in_positive_ttl_bounded_and_invalidated():
    class FakeRedis:
        def __init__(self):
            self.values = {"one": "1", "two": "2", "three": "3"}
            self.get_calls = 0

        def get(self, key):
            self.get_calls += 1
            return self.values.get(key)

        def set(self, key, value, ex=None):
            self.values[key] = value

        def delete(self, key):
            return int(self.values.pop(key, None) is not None)

    uncached_redis = FakeRedis()
    uncached = RedisDict(redis_client=uncached_redis)
    assert uncached.get("one") == "1"
    assert uncached.get("one") == "1"
    assert uncached_redis.get_calls == 2

    redis = FakeRedis()
    cached = RedisDict(
        redis_client=redis,
        cache_reads=True,
        cache_max_entries=2,
        cache_ttl_seconds=0.02,
    )
    assert cached.get("missing") is None
    assert cached.get("missing") is None
    assert redis.get_calls == 2  # Negative misses are never cached.

    assert cached.get("one") == "1"
    assert cached.get("one") == "1"
    assert redis.get_calls == 3
    assert cached.get("two") == "2"
    assert cached.get("three") == "3"
    assert len(cached) == 2
    assert cached.get("one") == "1"  # LRU eviction forces a remote read.
    assert redis.get_calls == 6

    cached["one"] = "updated"
    assert cached.get("one") == "updated"
    del cached["one"]
    assert cached.get("one") is None
    assert cached.get("three") == "3"
    redis.values.pop("three")
    with pytest.raises(KeyError):
        del cached["three"]
    assert cached.get("three") is None
    time.sleep(0.03)
    assert len(cached) == 0


def test_bounded_thread_stage_keeps_event_loop_responsive_and_holds_timed_out_slot():
    import pytincture.backend.app as backend_app

    def exercise_responsiveness():
        gate = AsyncAdmissionGate(1, 1, 0.1)

        async def run():
            event_loop_thread = threading.get_ident()
            work = asyncio.create_task(
                backend_app._run_bounded_thread_stage(
                    gate,
                    lambda: (time.sleep(0.2), threading.get_ident())[1],
                    timeout_seconds=1.0,
                    unavailable_detail="test unavailable",
                )
            )
            started = time.monotonic()
            await asyncio.sleep(0.02)
            assert time.monotonic() - started < 0.15
            worker_thread = await work
            assert worker_thread != event_loop_thread

        asyncio.run(run())

    def exercise_timeout_capacity():
        gate = AsyncAdmissionGate(1, 0, 0.01)
        release = threading.Event()

        async def run():
            with pytest.raises(HTTPException) as timed_out:
                await backend_app._run_bounded_thread_stage(
                    gate,
                    release.wait,
                    1.0,
                    timeout_seconds=0.01,
                    unavailable_detail="test unavailable",
                )
            assert timed_out.value.status_code == 503

            with pytest.raises(HTTPException) as saturated:
                await backend_app._run_bounded_thread_stage(
                    gate,
                    lambda: None,
                    timeout_seconds=0.01,
                    unavailable_detail="test unavailable",
                )
            assert saturated.value.status_code == 503
            release.set()
            await asyncio.sleep(0.05)
            assert await backend_app._run_bounded_thread_stage(
                gate,
                lambda: "recovered",
                timeout_seconds=0.1,
                unavailable_detail="test unavailable",
            ) == "recovered"

        asyncio.run(run())

    exercise_responsiveness()
    exercise_timeout_capacity()


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
    # The rejected second item is never retained as serialized output.
    assert reasons == [("byte-limit", 6)]


def test_stream_rejects_one_oversized_item_before_retaining_serialized_bytes():
    reasons = []
    chunks = list(
        limited_sync_stream(
            iter([{"payload": "x" * 100_000}]),
            raw=False,
            max_seconds=10,
            max_bytes=64,
            on_finish=lambda reason, size: reasons.append((reason, size)),
        )
    )

    assert chunks == []
    assert reasons == [("byte-limit", 0)]


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


def test_appcode_warm_cache_uses_metadata_without_rereading_sources(
    tmp_path, monkeypatch
):
    import pytincture.backend.browser_packages as browser_packages

    (tmp_path / "demo.py").write_text("import helper\nVALUE = 1\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("HELPER = 2\n", encoding="utf-8")
    reads = []
    parses = []
    original_read = browser_packages.read_contained_file

    def counted_read(root, relative_path, **kwargs):
        reads.append(relative_path)
        return original_read(root, relative_path, **kwargs)

    def parser(path, host, protocol, *, source_code, **kwargs):
        parses.append(path)
        return source_code

    monkeypatch.setattr(browser_packages, "read_contained_file", counted_read)
    cache = AppcodeArchiveCache(4, 1024 * 1024)

    cold = create_appcode_archive("", "", "demo", str(tmp_path), parser, cache=cache)
    cold_reads = len(reads)
    warm = create_appcode_archive("", "", "demo", str(tmp_path), parser, cache=cache)

    assert cold.getvalue() == warm.getvalue()
    assert cold_reads == 2
    assert len(reads) == cold_reads
    assert len(parses) == 2

    (tmp_path / "helper.py").write_text("HELPER = 3\n", encoding="utf-8")
    changed = create_appcode_archive("", "", "demo", str(tmp_path), parser, cache=cache)
    assert changed.getvalue() != warm.getvalue()
    assert len(reads) > cold_reads


def test_appcode_cache_enforces_aggregate_byte_budget(tmp_path):
    (tmp_path / "alpha.py").write_text("VALUE = 'alpha'\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("VALUE = 'beta'\n", encoding="utf-8")
    parse_calls = []

    def parser(path, host, protocol, *, source_code, **kwargs):
        parse_calls.append(Path(path).name)
        return source_code

    sizing_cache = AppcodeArchiveCache(4, 1024 * 1024)
    alpha = create_appcode_archive(
        "", "", "alpha", str(tmp_path), parser, cache=sizing_cache
    )
    byte_limited = AppcodeArchiveCache(4, len(alpha.getvalue()) + 16)
    parse_calls.clear()

    create_appcode_archive("", "", "alpha", str(tmp_path), parser, cache=byte_limited)
    create_appcode_archive("", "", "beta", str(tmp_path), parser, cache=byte_limited)
    calls_before_retry = len(parse_calls)

    assert 0 < byte_limited.current_bytes <= byte_limited.max_bytes
    create_appcode_archive("", "", "alpha", str(tmp_path), parser, cache=byte_limited)
    assert len(parse_calls) > calls_before_retry

    too_small = AppcodeArchiveCache(4, 1)
    parse_calls.clear()
    create_appcode_archive("", "", "alpha", str(tmp_path), parser, cache=too_small)
    create_appcode_archive("", "", "alpha", str(tmp_path), parser, cache=too_small)
    assert too_small.current_bytes == 0
    assert len(parse_calls) == 2


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
