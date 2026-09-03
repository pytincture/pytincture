import ast
import asyncio
import os
import sys
import textwrap
import types
import pytest
from os import sep
from pathlib import Path

# Import functions to test from dataclass.py
from pytincture.dataclass import (
    backend_for_frontend,
    bff_http_methods,
    bff_stream,
    get_bff_manifest,
    get_imports_used_in_class,
    generate_stub_classes,
    get_parsed_output,
    bff_routes,
)

# --------------------------------------------
# Tests for backend_for_frontend decorator
# --------------------------------------------

def test_backend_for_frontend_decorator():
    """Test that the decorator wraps a class and captures _user."""
    
    @backend_for_frontend
    class Dummy:
        def __init__(self, x):
            self.x = x
        def get_x(self):
            return self.x

    # Create an instance with a _user parameter.
    instance = Dummy(10, _user="tester")
    # The wrapper should have stored _user in the wrapper instance
    # and proxied attribute access to the real instance.
    assert hasattr(instance, "_user")
    assert instance._user == "tester"
    # Access the underlying method via proxy.
    assert instance.get_x() == 10
    # Also, the real instance should have _user set.
    assert hasattr(instance._real_instance, "_user")
    assert instance._real_instance._user == "tester"


def test_backend_for_frontend_passes_user_into_constructor():
    """Classes that explicitly accept _user should receive it during construction."""

    @backend_for_frontend
    class NeedsUser:
        def __init__(self, _user):
            self.user = _user

    instance = NeedsUser(_user={"email": "tester@example.com"})

    assert instance.user == {"email": "tester@example.com"}
    assert instance._real_instance.user == {"email": "tester@example.com"}


def test_backend_for_frontend_stream_registration():
    """Ensure streaming metadata is recorded for documentation."""
    previous_routes = dict(bff_routes)
    bff_routes.clear()
    
    @backend_for_frontend
    class StreamDummy:
        @bff_stream()
        def stream(self):
            yield {"value": 1}

    try:
        assert bff_routes, "Streaming route should be registered"
        route_spec = next(iter(bff_routes.values()))
        assert route_spec.get("x-bff-streaming") is True
        responses = route_spec.get("responses", {})
        assert "text/event-stream" in responses.get("200", {}).get("content", {})
    finally:
        bff_routes.clear()
        bff_routes.update(previous_routes)


def test_bff_http_methods_and_static_manifest(tmp_path):
    file_path = tmp_path / "reports.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_http_methods, bff_policy

        @backend_for_frontend
        @bff_policy(tenant="acme")
        class Reports:
            @bff_http_methods("GET")
            @bff_policy(role="reader")
            def status(self):
                return {"ready": True}

            def refresh(self):
                return {"ready": True}
    """))

    manifest = get_bff_manifest(str(file_path))
    operation = manifest[("Reports", "status")]
    assert operation["policy"] == {"tenant": "acme", "role": "reader"}
    assert operation["http_methods"] == ("GET",)
    assert operation["kind"] == "method"
    assert operation["parameters"] == ()
    assert operation["_class_definition"]["start_line"] == 4
    assert operation["_member_definition"]["start_line"] == 7
    assert len(operation["_class_definition"]["sha256"]) == 64
    assert len(operation["_member_definition"]["sha256"]) == 64
    assert manifest[("Reports", "refresh")]["http_methods"] == ("POST",)


def test_bff_http_methods_rejects_unsupported_method():
    with pytest.raises(ValueError):
        bff_http_methods("TRACE")


def test_static_manifest_defines_get_as_parameterless_read_only_contract(tmp_path):
    file_path = tmp_path / "unsafe_get.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_http_methods

        @backend_for_frontend
        class UnsafeGet:
            @bff_http_methods("GET")
            def read(self, value: int = 1):
                return value
    """))
    with pytest.raises(ValueError, match="parameterless and read-only"):
        get_bff_manifest(str(file_path))


def test_static_manifest_retains_safe_annotation_contracts(tmp_path):
    file_path = tmp_path / "typed.py"
    file_path.write_text(textwrap.dedent("""
        from typing import Optional
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Typed:
            def run(self, count: int, labels: list[str], enabled: Optional[bool] = None):
                return count
    """))
    parameters = get_bff_manifest(str(file_path))[("Typed", "run")]["parameters"]
    assert [parameter["annotation"] for parameter in parameters] == [
        "int",
        "list[str]",
        "Optional[bool]",
    ]


@pytest.mark.parametrize(
    "declaration",
    [
        "def backend_for_frontend(cls): return cls",
        "from unrelated import backend_for_frontend",
        (
            "from pytincture.dataclass import backend_for_frontend\n"
            "backend_for_frontend = lambda cls: cls"
        ),
        (
            "import pytincture.dataclass as dc\n"
            "dc.backend_for_frontend = lambda cls: cls"
        ),
    ],
)
def test_static_manifest_rejects_spoofed_or_rebound_export_decorators(declaration):
    source = declaration + textwrap.dedent("""

        @backend_for_frontend
        class Accidental:
            def secret(self):
                return True
    """)
    if " as dc" in declaration:
        source = source.replace("@backend_for_frontend", "@dc.backend_for_frontend")

    assert get_bff_manifest("accidental.py", source=source) == {}


@pytest.mark.parametrize(
    ("decorator_name", "declaration", "expected"),
    [
        (
            "bff_policy",
            "def bff_policy(**metadata): return lambda target: target",
            {"policy": {}, "http_methods": ("POST",)},
        ),
        (
            "bff_policy",
            "from unrelated import bff_policy",
            {"policy": {}, "http_methods": ("POST",)},
        ),
        (
            "bff_policy",
            (
                "from pytincture.dataclass import bff_policy\n"
                "bff_policy = lambda **metadata: (lambda target: target)"
            ),
            {"policy": {}, "http_methods": ("POST",)},
        ),
        (
            "bff_http_methods",
            "def bff_http_methods(*methods): return lambda target: target",
            {"policy": {}, "http_methods": ("POST",)},
        ),
        (
            "bff_http_methods",
            "from unrelated import bff_http_methods",
            {"policy": {}, "http_methods": ("POST",)},
        ),
        (
            "bff_http_methods",
            (
                "from pytincture.dataclass import bff_http_methods\n"
                "bff_http_methods = lambda *methods: (lambda target: target)"
            ),
            {"policy": {}, "http_methods": ("POST",)},
        ),
    ],
)
def test_static_manifest_rejects_spoofed_or_rebound_security_metadata(
    decorator_name, declaration, expected
):
    argument = 'role="admin"' if decorator_name == "bff_policy" else '"GET"'
    source = (
        "from pytincture.dataclass import backend_for_frontend\n"
        + declaration
        + textwrap.dedent(f"""

            @backend_for_frontend
            class Reports:
                @{decorator_name}({argument})
                def status(self):
                    return True
        """)
    )

    operation = get_bff_manifest("reports.py", source=source)[("Reports", "status")]
    assert operation["policy"] == expected["policy"]
    assert operation["http_methods"] == expected["http_methods"]


def test_static_manifest_accepts_proven_direct_and_module_aliases():
    source = textwrap.dedent("""
        from pytincture.dataclass import (
            backend_for_frontend as expose,
            bff_http_methods as methods,
            bff_policy as policy,
        )

        @expose
        @policy(tenant="acme")
        class Reports:
            @methods("GET")
            @policy(role="reader")
            def status(self):
                return True
    """)

    operation = get_bff_manifest("reports.py", source=source)[("Reports", "status")]
    assert operation["policy"] == {"tenant": "acme", "role": "reader"}
    assert operation["http_methods"] == ("GET",)


def test_static_manifest_tracks_rebinding_in_source_order():
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Intended:
            def status(self):
                return True

        backend_for_frontend = lambda cls: cls

        @backend_for_frontend
        class Accidental:
            def secret(self):
                return True
    """)

    assert set(get_bff_manifest("ordered.py", source=source)) == {
        ("Intended", "status")
    }


@pytest.mark.parametrize(
    "rebind_statement",
    [
        "match value:\n    case backend_for_frontend:\n        pass",
        (
            "@(backend_for_frontend := (lambda target: target))\n"
            "def helper():\n"
            "    pass"
        ),
    ],
)
def test_static_manifest_rejects_less_common_rebindings(rebind_statement):
    source = (
        "from pytincture.dataclass import backend_for_frontend\n"
        "value = object()\n"
        + rebind_statement
        + textwrap.dedent("""

            @backend_for_frontend
            class Accidental:
                def secret(self):
                    return True
        """)
    )

    assert get_bff_manifest("accidental.py", source=source) == {}


def test_static_manifest_tracks_named_expression_rebinding_in_comprehensions():
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        [(backend_for_frontend := (lambda target: target)) for _ in (0,)]

        @backend_for_frontend
        class Accidental:
            def secret(self):
                return True
    """)

    assert get_bff_manifest("accidental.py", source=source) == {}


def test_static_manifest_does_not_treat_comprehension_targets_as_module_rebinding():
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        [None for backend_for_frontend in ()]

        @backend_for_frontend
        class Intended:
            def status(self):
                return True
    """)

    assert set(get_bff_manifest("intended.py", source=source)) == {
        ("Intended", "status")
    }


@pytest.mark.parametrize(
    "replacement",
    [
        textwrap.dedent("""
            @backend_for_frontend
            class API:
                def read(self):
                    return "public"

            class API:
                def read(self):
                    return "internal"
        """),
        textwrap.dedent("""
            @backend_for_frontend
            class API:
                def read(self):
                    return "public"

            API = Internal
        """),
        textwrap.dedent("""
            @backend_for_frontend
            class API:
                def read(self):
                    return "public"

            API.read = Internal.read
        """),
        textwrap.dedent("""
            @backend_for_frontend
            class API:
                def read(self):
                    return "public"

            from unrelated import *
        """),
    ],
)
def test_static_manifest_rejects_exported_class_or_member_replacement(replacement):
    source = (
        "from pytincture.dataclass import backend_for_frontend\n"
        "class Internal:\n"
        "    def read(self): return 'internal'\n\n"
        + replacement
    )

    with pytest.raises(ValueError, match="BFF class 'API'"):
        get_bff_manifest("replacement.py", source=source)


def test_static_manifest_requires_bff_export_decorator_to_be_outermost():
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        def replace(target):
            return target

        @replace
        @backend_for_frontend
        class API:
            def read(self):
                return "public"
    """)

    with pytest.raises(ValueError, match="single outermost"):
        get_bff_manifest("replacement.py", source=source)


def test_static_manifest_rejects_class_scope_security_decorator_rebinding():
    source = textwrap.dedent("""
        from pytincture.dataclass import (
            backend_for_frontend,
            bff_http_methods,
            bff_policy,
        )

        @backend_for_frontend
        class Reports:
            bff_policy = lambda **metadata: (lambda target: target)

            @bff_policy(role="admin")
            def private_status(self):
                return True

            bff_http_methods = lambda *methods: (lambda target: target)

            @bff_http_methods("GET")
            def mutate(self):
                return True
    """)

    manifest = get_bff_manifest("reports.py", source=source)
    assert manifest[("Reports", "private_status")]["policy"] == {}
    assert manifest[("Reports", "mutate")]["http_methods"] == ("POST",)


def test_stub_generation_rejects_a_spoofed_stream_decorator(tmp_path, monkeypatch):
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        def bff_stream(**metadata):
            return lambda target: target

        @backend_for_frontend
        class Reports:
            @bff_stream(raw=True)
            async def status(self):
                return {"ready": True}
    """)
    file_path = tmp_path / "reports.py"
    file_path.write_text(source)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path),
        "example.test",
        "https",
        application="reports",
        source_code=source,
    )

    assert "async def fetch_stream" not in stub
    assert "async def status(self, *args, **kwargs):" in stub

# --------------------------------------------
# Tests for get_imports_used_in_class
# --------------------------------------------

def test_get_imports_used_in_class(tmp_path):
    """
    Create a temporary Python file with some import statements and a dummy class.
    Verify that get_imports_used_in_class returns the correct import lines and
    the set of imports used in the class.
    """
    code = textwrap.dedent("""
        import os
        import sys
        from math import sqrt, pi
        from collections import defaultdict
        
        class MyClass:
            def method(self):
                print(os.getcwd())
                x = sqrt(4)
    """)
    file_path = tmp_path / "dummy.py"
    file_path.write_text(code)
    
    import_lines, imports_used = get_imports_used_in_class(str(file_path), "MyClass")
    # Expect import_lines to include these four lines (order might differ)
    expected_import_lines = {
        "import os",
        "import sys",
        "from math import sqrt",
        "from math import pi",
        "from collections import defaultdict"
    }
    # We allow extra spaces or order variations.
    for line in expected_import_lines:
        assert any(line in imp for imp in import_lines), f"Missing import line: {line}"
    
    # In MyClass, "os" and "sqrt" are used.
    assert "os" in imports_used
    assert "sqrt" in imports_used
    # "sys", "pi", and "defaultdict" are not used in the class body.
    assert "sys" not in imports_used
    assert "pi" not in imports_used
    assert "defaultdict" not in imports_used

# --------------------------------------------
# Tests for generate_stub_classes and get_parsed_output
# --------------------------------------------

def test_generate_stub_classes_returns_stub(tmp_path, monkeypatch):
    """
    Create a dummy file that contains a class decorated with @backend_for_frontend.
    Verify that generate_stub_classes returns a stub with the fetch method and generated URL.
    """
    # Note: The marker '@backend_for_frontend' must appear in the code.
    dummy_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class MyService:
            def foo(self, a):
                return a * 2
    """)
    file_path = tmp_path / "service.py"
    file_path.write_text(dummy_code)

    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    
    # Call generate_stub_classes with dummy return values.
    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    # Sync backend methods should keep synchronous stubs.
    assert "class MyService:" in stub
    assert "async def fetch(self, url, payload=None, method='GET', _replay_retry=True):" in stub
    assert "def foo(self, *args, **kwargs):" in stub
    assert "response = self.fetch_sync(url, payload, 'POST')" in stub
    assert "async def foo_async(self, *args, **kwargs):" in stub
    assert "response = await self.fetch(url, payload, 'POST')" in stub
    assert "async def foo(self, *args, **kwargs):" not in stub
    expected_url = "/demoapp/classcall/service.py/MyService/foo"
    assert expected_url in stub
    # Also check that required imports are added.
    assert "import json" in stub
    assert "from js import XMLHttpRequest, document" in stub
    assert "JSON.stringify(json.dumps" not in stub
    assert "json.dumps(payload, allow_nan=False)" in stub
    assert "from io import StringIO" in stub


def test_generated_async_bff_transport_is_bounded_and_replay_refill_is_single_flight(
    tmp_path,
    monkeypatch,
):
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Service:
            def mutate(self):
                return True
    """)
    file_path = tmp_path / "service.py"
    file_path.write_text(source)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    stub = generate_stub_classes(
        str(file_path),
        "example.com",
        "https",
        application="demoapp",
        replay_client={"capsule": "opaque", "key": bytes(range(32))},
    )

    class Headers:
        @staticmethod
        def get(name):
            return None

    class Response:
        status = 200
        headers = Headers()

        @staticmethod
        async def text():
            return '{"ok": true}'

    browser_calls = []

    async def fetch(url, options=None):
        browser_calls.append((url, options))
        return Response()

    warnings_sent = []
    js_module = types.ModuleType("js")
    js_module.XMLHttpRequest = types.SimpleNamespace(new=lambda: None)
    js_module.document = types.SimpleNamespace(
        cookie="__Host-pytincture-csrf=test"
    )
    js_module.fetch = fetch
    js_module.console = types.SimpleNamespace(warn=warnings_sent.append)
    js_module.window = types.SimpleNamespace(
        location=types.SimpleNamespace(href="https://example.test/demoapp")
    )
    pyodide_module = types.ModuleType("pyodide")
    pyodide_ffi_module = types.ModuleType("pyodide.ffi")
    pyodide_ffi_module.to_js = lambda value: value
    monkeypatch.setitem(sys.modules, "js", js_module)
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", pyodide_ffi_module)

    namespace = {}
    exec(compile(stub, str(file_path), "exec"), namespace)
    service = namespace["Service"]()

    async def exercise():
        refill_calls = 0

        async def refill_once():
            nonlocal refill_calls
            refill_calls += 1
            await asyncio.sleep(0.01)
            service._pytincture_replay_pool.extend(["one", "two"])

        service._request_pytincture_state = refill_once
        tokens = await asyncio.gather(
            service._take_pytincture_state(),
            service._take_pytincture_state(),
        )
        assert refill_calls == 1
        assert set(tokens) == {"one", "two"}

        service._pytincture_replay_pool[:] = ["mutation-token"]

        async def failed_prefetch():
            raise RuntimeError("prefetch unavailable")

        service._refill_pytincture_state = failed_prefetch
        result = await service.mutate_async()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert result == {"ok": True}
        assert len(browser_calls) == 1
        assert warnings_sent == [
            "Pytincture replay-token prefetch failed; the next BFF call will retry."
        ]

        service._pytincture_replay_enabled = False
        service._pytincture_browser_timeout = 0.001

        async def never_returns(url, options=None):
            await asyncio.Event().wait()

        js_module.fetch = never_returns
        with pytest.raises(TimeoutError):
            await service.mutate_async()

    asyncio.run(exercise())


def test_generated_bff_proxies_raise_safe_typed_errors_for_non_2xx(
    tmp_path,
    monkeypatch,
):
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class Service:
            def sync_call(self):
                return True

            async def async_call(self):
                return True

            @bff_stream()
            async def stream_call(self):
                yield True
    """)
    file_path = tmp_path / "service.py"
    file_path.write_text(source)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    stub = generate_stub_classes(
        str(file_path),
        "example.com",
        "https",
        application="demoapp",
    )

    class Headers:
        def __init__(self, values):
            self.values = values

        def get(self, name):
            return self.values.get(name)

    class BrowserResponse:
        def __init__(self, status):
            self.status = status
            self.headers = Headers({"X-Request-ID": f"request-{status}"})
            self.body = types.SimpleNamespace(
                getReader=lambda: pytest.fail("error response body was accessed")
            )

        async def text(self):
            pytest.fail("error response body was accessed")

    class XhrResponse:
        def __init__(self, status):
            self.status = status

        @property
        def response(self):
            pytest.fail("error response body was accessed")

        @property
        def responseText(self):
            pytest.fail("error response body was accessed")

        def open(self, *args):
            return None

        def setRequestHeader(self, *args):
            return None

        def send(self, *args):
            return None

        def getResponseHeader(self, name):
            if name == "X-Request-ID":
                return f"request-{self.status}"
            if name == "X-Pytincture-Replay":
                return "rejected" if self.status == 409 else None
            return None

    browser_state = {"status": 400}

    class XMLHttpRequest:
        @staticmethod
        def new():
            return XhrResponse(browser_state["status"])

    async def fetch(url, options=None):
        return BrowserResponse(browser_state["status"])

    window = types.SimpleNamespace(
        location=types.SimpleNamespace(href="https://example.test/demoapp")
    )
    js_module = types.ModuleType("js")
    js_module.XMLHttpRequest = XMLHttpRequest
    js_module.TextDecoder = object()
    js_module.document = types.SimpleNamespace(
        cookie="__Host-pytincture-csrf=test"
    )
    js_module.fetch = fetch
    js_module.window = window
    pyodide_module = types.ModuleType("pyodide")
    pyodide_ffi_module = types.ModuleType("pyodide.ffi")
    pyodide_ffi_module.to_js = lambda value: value
    monkeypatch.setitem(sys.modules, "js", js_module)
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", pyodide_ffi_module)

    namespace = {}
    exec(compile(stub, str(file_path), "exec"), namespace)
    service = namespace["Service"]()
    error_type = namespace["PytinctureBFFError"]
    statuses = (400, 403, 404, 405, 409, 413, 429, 500, 503, 504)

    async def assert_async_and_stream_errors(status):
        with pytest.raises(error_type) as async_error:
            await service.fetch(
                "/demoapp/classcall/service.py/Service/async_call",
                {"args": [], "kwargs": {}},
                "POST",
            )
        with pytest.raises(error_type) as stream_error:
            async for _ in service.fetch_stream(
                "/demoapp/classcall/service.py/Service/stream_call",
                {"args": [], "kwargs": {}},
                "POST",
            ):
                pass
        return async_error.value, stream_error.value

    for status in statuses:
        browser_state["status"] = status
        with pytest.raises(error_type) as sync_error:
            service.fetch_sync(
                "/demoapp/classcall/service.py/Service/sync_call",
                {"args": [], "kwargs": {}},
                "POST",
            )
        async_error, stream_error = asyncio.run(assert_async_and_stream_errors(status))
        for error, operation in (
            (sync_error.value, "Service.sync_call"),
            (async_error, "Service.async_call"),
            (stream_error, "Service.stream_call"),
        ):
            assert error.status_code == status
            assert error.operation == operation
            assert error.correlation_id == f"request-{status}"
            assert "server-secret" not in str(error)


def test_generated_bff_proxies_preserve_401_login_redirect(tmp_path, monkeypatch):
    source = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class Service:
            def sync_call(self):
                return True

            async def async_call(self):
                return True

            @bff_stream()
            async def stream_call(self):
                yield True
    """)
    file_path = tmp_path / "service.py"
    file_path.write_text(source)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )

    class Unauthorized:
        status = 401
        response = "private response"
        responseText = response
        headers = types.SimpleNamespace(get=lambda name: "request-401")
        body = None

        def open(self, *args):
            return None

        def setRequestHeader(self, *args):
            return None

        def send(self, *args):
            return None

        def getResponseHeader(self, name):
            return "request-401"

    async def fetch(url, options=None):
        return Unauthorized()

    window = types.SimpleNamespace(
        location=types.SimpleNamespace(href="https://example.test/demoapp")
    )
    js_module = types.ModuleType("js")
    js_module.XMLHttpRequest = types.SimpleNamespace(new=lambda: Unauthorized())
    js_module.TextDecoder = object()
    js_module.document = types.SimpleNamespace(cookie="")
    js_module.fetch = fetch
    js_module.window = window
    pyodide_module = types.ModuleType("pyodide")
    pyodide_ffi_module = types.ModuleType("pyodide.ffi")
    pyodide_ffi_module.to_js = lambda value: value
    monkeypatch.setitem(sys.modules, "js", js_module)
    monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", pyodide_ffi_module)
    namespace = {}
    exec(compile(stub, str(file_path), "exec"), namespace)
    service = namespace["Service"]()

    assert service.sync_call() is None

    async def exercise_async_proxies():
        assert await service.async_call() is None
        assert [item async for item in service.stream_call()] == []

    asyncio.run(exercise_async_proxies())
    assert window.location.href.endswith("/login")


def test_generate_stub_classes_requires_application_for_bff_clients(
    tmp_path, monkeypatch
):
    file_path = tmp_path / "service.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Service:
            def read(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    with pytest.raises(ValueError, match="application is required"):
        generate_stub_classes(str(file_path), "example.com", "https")


def test_generate_stub_classes_streaming(tmp_path, monkeypatch):
    """
    Streaming methods should generate async stub methods that iterate over the stream.
    """
    dummy_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class StreamService:
            @bff_stream()
            async def ticker(self):
                yield {"value": 1}
    """)
    file_path = tmp_path / "stream_service.py"
    file_path.write_text(dummy_code)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    assert "class StreamService:" in stub
    assert "async def fetch_stream" in stub
    assert "async def ticker" in stub
    assert "async for chunk in stream_iter" in stub
    assert "yield json.loads(line)" in stub


def test_static_manifest_records_streaming_without_changing_decorator_forms(tmp_path):
    file_path = tmp_path / "stream_manifest.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_stream

        @backend_for_frontend
        class StreamService:
            @bff_stream
            async def default_stream(self):
                yield 1

            @bff_stream(raw=True, media_type="application/octet-stream")
            async def raw_stream(self):
                yield b"x"
    """))

    manifest = get_bff_manifest(str(file_path))

    assert manifest[("StreamService", "default_stream")]["stream"] == {
        "enabled": True,
        "raw": False,
        "media_type": "text/event-stream",
    }
    assert manifest[("StreamService", "raw_stream")]["stream"] == {
        "enabled": True,
        "raw": True,
        "media_type": "application/octet-stream",
    }


def test_generate_stub_classes_supports_decorator_aliases_and_async_methods(tmp_path, monkeypatch):
    """
    Stub generation should work with module-qualified decorators and async methods.
    """
    dummy_code = textwrap.dedent("""
        import pytincture.dataclass as dc

        @dc.backend_for_frontend
        class AsyncService:
            @dc.bff_stream()
            async def ticker(self):
                yield {"value": 1}

            async def ping(self):
                return {"status": "ok"}
    """)
    file_path = tmp_path / "async_service.py"
    file_path.write_text(dummy_code)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    assert "class AsyncService:" in stub
    assert "async def fetch(self, url, payload=None, method='GET', _replay_retry=True):" in stub
    assert "async def ticker(self, *args, **kwargs):" in stub
    assert "async def ping(self, *args, **kwargs):" in stub
    assert "response = await self.fetch(url, payload, 'POST')" in stub
    assert "stream_iter = self.fetch_stream(url, payload, 'POST')" in stub


def test_generate_stub_classes_keeps_sync_alias_methods_sync(tmp_path, monkeypatch):
    """
    Module-qualified decorators should still preserve sync stubs for sync methods.
    """
    dummy_code = textwrap.dedent("""
        import pytincture.dataclass as dc

        @dc.backend_for_frontend
        class AliasSyncService:
            def ping(self):
                return {"status": "ok"}
    """)
    file_path = tmp_path / "alias_sync_service.py"
    file_path.write_text(dummy_code)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    assert "def ping(self, *args, **kwargs):" in stub
    assert "response = self.fetch_sync(url, payload, 'POST')" in stub
    assert "async def ping(self, *args, **kwargs):" not in stub


def test_generate_stub_classes_nested_path(tmp_path, monkeypatch):
    """
    Stubs should reference the relative folder structure when files live in subdirectories.
    """
    nested_dir = tmp_path / "api" / "v1"
    nested_dir.mkdir(parents=True)
    dummy_code = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class NestedService:
            def ping(self):
                return "pong"
    """)
    file_path = nested_dir / "service.py"
    file_path.write_text(dummy_code)
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    expected_url = "/demoapp/classcall/api/v1/service.py/NestedService/ping"
    assert expected_url in stub


def test_generated_stub_sends_csrf_and_declared_http_method(tmp_path, monkeypatch):
    file_path = tmp_path / "status.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend, bff_http_methods

        @backend_for_frontend
        class Status:
            @bff_http_methods("GET")
            def read(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))

    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )
    assert "X-CSRF-Token" in stub
    assert "__Host-pytincture-csrf" in stub
    assert "pytincture-dev-csrf" in stub
    assert "name == cookie_name" in stub
    assert "name in {'__Host-pytincture-csrf', 'pytincture-dev-csrf'}" not in stub
    assert "response = self.fetch_sync(url, payload, 'GET')" in stub


def test_generated_stub_uses_only_the_runtime_selected_csrf_cookie(
    tmp_path, monkeypatch
):
    file_path = tmp_path / "status.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Status:
            def mutate(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    stub = generate_stub_classes(
        str(file_path), "example.com", "https", application="demoapp"
    )

    js_module = types.ModuleType("js")
    js_module.XMLHttpRequest = types.SimpleNamespace(new=lambda: None)
    js_module.document = types.SimpleNamespace(
        cookie=(
            "pytincture-dev-csrf=sibling-value; "
            "__Host-pytincture-csrf=production-value"
        )
    )
    js_module.window = types.SimpleNamespace(
        __pytinctureCsrfCookieName="__Host-pytincture-csrf",
        location=types.SimpleNamespace(href="https://example.com/demoapp"),
    )
    monkeypatch.setitem(sys.modules, "js", js_module)

    namespace = {}
    exec(compile(stub, str(file_path), "exec"), namespace)
    service = namespace["Status"]()
    assert service._csrf_token() == "production-value"

    js_module.window.__pytinctureCsrfCookieName = "pytincture-dev-csrf"
    assert service._csrf_token() == "sibling-value"


def test_generated_stub_injects_opaque_replay_state_client(tmp_path, monkeypatch):
    file_path = tmp_path / "service.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Service:
            def read(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    replay_client = {"capsule": "opaque-capsule", "key": bytes(range(32))}

    stub = generate_stub_classes(
        str(file_path),
        "example.com",
        "https",
        application="demoapp",
        replay_client=replay_client,
    )

    assert "_pytincture_replay_enabled = True" in stub
    assert "_pytincture_replay_capsule = 'opaque-capsule'" in stub
    assert "'/_pytincture/state'" in stub
    assert "X-Pytincture-BFF-Token" in stub
    assert "X-Pytincture-Client" in stub
    assert "_decode_pytincture_state" in stub
    compile(stub, str(file_path), "exec")


def test_generated_stub_never_embeds_request_origin(tmp_path, monkeypatch):
    file_path = tmp_path / "service.py"
    file_path.write_text(textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class Service:
            def read(self):
                return True
    """))
    monkeypatch.setenv("MODULES_PATH", str(tmp_path))
    hostile_host = "host-with-'quote.example"

    stub = generate_stub_classes(
        str(file_path),
        hostile_host,
        "protocol-with-'quote",
        application="demoapp",
        replay_client={"capsule": "opaque", "key": bytes(range(32))},
    )

    assert hostile_host not in stub
    assert "protocol-with-" not in stub
    assert "url = '/demoapp/classcall/service.py/Service/read'" in stub
    assert "'/_pytincture/state'" in stub
    compile(stub, str(file_path), "exec")

def test_get_parsed_output_returns_stub(tmp_path):
    """
    When the file contains '@backend_for_frontend', get_parsed_output should return stub code.
    Otherwise, it should return None (or the original code if no marker is found).
    """
    # Case 1: File contains the marker.
    dummy_code_with_marker = textwrap.dedent("""
        from pytincture.dataclass import backend_for_frontend

        @backend_for_frontend
        class MyService:
            def foo(self, a):
                return a * 2
    """)
    file_path = tmp_path / "service_with_marker.py"
    file_path.write_text(dummy_code_with_marker)
    
    parsed_output = get_parsed_output(
        str(file_path), "example.com", "https", application="demoapp"
    )
    assert parsed_output is not None
    assert "class MyService:" in parsed_output
    assert "async def fetch(" in parsed_output
    
    # Case 2: File does NOT contain the marker.
    dummy_code_without_marker = textwrap.dedent("""
        class PlainService:
            def foo(self, a):
                return a * 3
    """)
    file_path2 = tmp_path / "service_without_marker.py"
    file_path2.write_text(dummy_code_without_marker)
    parsed_output2 = get_parsed_output(
        str(file_path2), "example.com", "https", application="demoapp"
    )
    # In this case, our function returns the original code.
    # (Your code returns stub_code only if stub_code is truthy.)
    assert parsed_output2 is not None
    assert "class PlainService:" in parsed_output2
    # It should not contain any fetch method.
    assert "async def fetch(" not in parsed_output2
