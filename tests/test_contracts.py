import importlib
import base64
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "public-api-v1.json"


@pytest.fixture(scope="module")
def contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_public_python_symbols_match_contract(contract):
    for module_name, symbol_names in contract["python"]["modules"].items():
        module = importlib.import_module(module_name)
        missing = [name for name in symbol_names if not hasattr(module, name)]
        assert not missing, f"{module_name} is missing public symbols: {missing}"


def test_ci_actions_are_commit_pinned_and_generated_bundles_are_tracked():
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in workflow.read_text().splitlines():
            if "uses:" not in line:
                continue
            reference = line.split("uses:", 1)[1].strip().split()[0]
            assert re.search(r"@[0-9a-f]{40}$", reference), (
                f"floating action reference in {workflow.name}: {reference}"
            )
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text()
    assert "/pytincture/backend/" in codeowners
    assert (ROOT / "pytincture" / "frontend" / "dist" / "pytincture.js").exists()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "pytincture/frontend/dist/pytincture.js"],
        cwd=ROOT,
        check=False,
    )
    assert ignored.returncode == 1


def test_vendored_pyodide_sbom_and_checksums_validate():
    script = ROOT / "scripts" / "validate_vendored_pyodide.py"
    namespace = {"__name__": "validation_test", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), namespace)
    namespace["main"]()


def test_vendored_swagger_ui_is_exactly_pinned_and_hash_locked():
    manifest = json.loads(
        (ROOT / "security" / "swagger-ui-assets.json").read_text(encoding="utf-8")
    )
    package = json.loads(
        (ROOT / "pytincture" / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema"] == 1
    assert manifest["package"] == "swagger-ui-dist"
    assert manifest["version"] == package["devDependencies"]["swagger-ui-dist"]
    assert manifest["source"].endswith(
        f"swagger-ui-dist-{manifest['version']}.tgz"
    )
    assert manifest["npm_integrity"].startswith("sha512-")
    assert {entry["path"] for entry in manifest["assets"]} == {
        "pytincture/frontend/vendor/swagger-ui/LICENSE",
        "pytincture/frontend/vendor/swagger-ui/swagger-ui-bundle.js",
        "pytincture/frontend/vendor/swagger-ui/swagger-ui.css",
    }
    for entry in manifest["assets"]:
        content = (ROOT / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_browser_asset_integrity_manifest_covers_runtime_dependencies():
    import pytincture

    frontend = ROOT / "pytincture" / "frontend"
    manifest = json.loads(
        (frontend / "integrity" / f"pytincture-{pytincture.__version__}.json").read_text(
            encoding="utf-8"
        )
    )
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    mdi_lock = lock["packages"]["node_modules/@mdi/font"]
    assert manifest["schema"] == 1
    assert manifest["framework_version"] == pytincture.__version__
    assert manifest["npm_version"] == package["version"]
    assert manifest["pyodide_version"] == "0.29.3"
    assert manifest["material_design_icons_version"] == "7.4.47"
    assert manifest["material_design_icons_source"] == {
        "package": "@mdi/font",
        "resolved": mdi_lock["resolved"],
        "npm_integrity": mdi_lock["integrity"],
    }
    expected = {
        "pytincture.js",
        "sw.js",
        "dist/pytincture.js",
        "dist/pytincture.js.map",
        "dist/pytincture.esm.js",
        "dist/pytincture.esm.js.map",
        "dist/pytincture.min.js",
        "dist/pytincture.min.js.map",
        "vendor/materialdesignicons/LICENSE",
        "vendor/materialdesignicons/materialdesignicons.css",
        "vendor/materialdesignicons/fonts/materialdesignicons-webfont.woff2",
        "pyodide/0.29.3/sbom.json",
        "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl",
        "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl.metadata",
        "pyodide/0.29.3/full/pyodide-lock.json",
        "pyodide/0.29.3/full/pyodide.asm.js",
        "pyodide/0.29.3/full/pyodide.asm.wasm",
        "pyodide/0.29.3/full/pyodide.js",
        "pyodide/0.29.3/full/python_stdlib.zip",
    }
    assert {entry["path"] for entry in manifest["assets"]} == expected
    for entry in manifest["assets"]:
        content = (frontend / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        expected_sri = base64.b64encode(hashlib.sha384(content).digest()).decode()
        assert entry["sri"] == f"sha384-{expected_sri}"


def test_javascript_globals_and_config_keys_match_contract(contract):
    runtime_source = (ROOT / "pytincture" / "frontend" / "pytincture.js").read_text(
        encoding="utf-8"
    )
    config_match = re.search(
        r"const DEFAULT_CONFIG = \{(?P<body>.*?)^\};",
        runtime_source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert config_match, "DEFAULT_CONFIG is part of the checked runtime contract"
    actual_config_keys = sorted(
        re.findall(r"^\s{4}([A-Za-z][A-Za-z0-9]*):", config_match.group("body"), re.MULTILINE)
    )
    assert actual_config_keys == contract["javascript"]["run_tincture_app_config"]

    assert "window.runTinctureApp = runTinctureApp" in runtime_source
    assert "window.PytinctureLifecycleError = PytinctureLifecycleError" in runtime_source
    assert "window.pytinctureAutoStartConfig" in runtime_source
    assert "window.pytinctureAutoStartDisabled" in runtime_source


def test_bff_route_methods_and_generated_stub_match_contract(contract, tmp_path):
    import pytincture
    import pytincture.backend.app as backend_app
    from pytincture import set_modules_path
    from pytincture.dataclass import generate_stub_classes

    bff_contract = contract["bff"]
    route_methods = set()
    for route in backend_app.app.routes:
        if getattr(route, "path", None) == bff_contract["route"]:
            route_methods.update(getattr(route, "methods", set()))
    assert sorted(route_methods) == bff_contract["http_methods"]
    assert not any(
        str(getattr(route, "path", "")).startswith("/classcall/")
        for route in backend_app.app.routes
    )

    module_file = tmp_path / "contract_widget.py"
    module_file.write_text(
        "from pytincture.dataclass import backend_for_frontend, bff_stream\n\n"
        "@backend_for_frontend\n"
        "class ContractWidget:\n"
        "    def query(self, value):\n"
        "        return value\n\n"
        "    @bff_stream()\n"
        "    def events(self):\n"
        "        yield {'ready': True}\n",
        encoding="utf-8",
    )
    previous_modules_path = pytincture.MODULES_PATH
    try:
        set_modules_path(str(tmp_path))
        stub = generate_stub_classes(
            str(module_file),
            "example.test",
            "https",
            application="contractapp",
        )
    finally:
        set_modules_path(previous_modules_path)

    rendered_route = bff_contract["route"].replace("{application}", "contractapp")
    rendered_route = rendered_route.replace("{file_path:path}", "contract_widget.py")
    rendered_route = rendered_route.replace("{class_name}", "ContractWidget")
    assert rendered_route.replace("{function_name}", "query") in stub
    for key in bff_contract["request_body_keys"]:
        assert f"'{key}'" in stub
    for header in bff_contract["request_headers"]:
        assert header in stub
    assert bff_contract["default_stream_media_type"] == "text/event-stream"
    assert "while '\\n' in buffer" in stub

    backend_source = (ROOT / "pytincture" / "backend" / "app.py").read_text(
        encoding="utf-8"
    )
    for header in bff_contract["response_headers"]:
        assert header in backend_source


def test_appcode_archive_layout_matches_contract(contract, monkeypatch, tmp_path):
    import pytincture
    import pytincture.backend.app as backend_app
    from pytincture import set_modules_path

    appcode_contract = contract["appcode"]
    appcode_routes = [
        route
        for route in backend_app.app.routes
        if getattr(route, "path", None) == appcode_contract["route"]
    ]
    assert appcode_routes
    assert any("GET" in getattr(route, "methods", set()) for route in appcode_routes)
    backend_source = (ROOT / "pytincture" / "backend" / "app.py").read_text(
        encoding="utf-8"
    )
    assert f'media_type="{appcode_contract["media_type"]}"' in backend_source
    assert f'filename={appcode_contract["download_filename"]}' in backend_source

    application = "contract_app"
    (tmp_path / f"{application}.py").write_text(
        "import helper\n\nclass ContractApp:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text('{"enabled": true}\n', encoding="utf-8")
    monkeypatch.setenv("PYTINCTURE_BROWSER_FILES", '["runtime.json"]')

    previous_modules_path = pytincture.MODULES_PATH
    try:
        set_modules_path(str(tmp_path))
        archive = backend_app.create_appcode_pkg_in_memory(
            "example.test", "https", application
        )
    finally:
        set_modules_path(previous_modules_path)

    assert isinstance(archive, io.BytesIO)
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()

    expected_entrypoint = appcode_contract["entrypoint"].replace(
        "{application}", application
    )
    assert expected_entrypoint in names
    assert {"helper.py", "runtime.json"}.issubset(names)
    for name in names:
        member = PurePosixPath(name)
        assert not member.is_absolute()
        assert ".." not in member.parts
        assert "\\" not in name


def test_contract_documentation_is_linked_and_present():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_documents = (
        "docs/public-api.md",
        "docs/compatibility.md",
        "docs/contracts/bff-v1.md",
        "docs/contracts/appcode-v1.md",
        "contracts/public-api-v1.json",
    )
    for relative_path in required_documents:
        assert (ROOT / relative_path).is_file()
        assert relative_path in readme
