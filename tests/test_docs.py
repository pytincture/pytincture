import ast
import importlib.util
import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
ROOT = Path(__file__).resolve().parents[1]


class PythonScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_python = False
        self.python = []
        self.runtime_sources = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "text/python":
            self.in_python = True
        if tag == "script" and attributes.get("src"):
            self.runtime_sources.append(attributes["src"])

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_python = False

    def handle_data(self, data):
        if self.in_python:
            self.python.append(data)


def test_service_quickstart_is_an_executable_packaged_application():
    service_path = ROOT / "examples" / "quickstart" / "service" / "service.py"
    spec = importlib.util.spec_from_file_location("pytincture_quickstart_service", service_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with TestClient(module.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        response = client.get("/hello/appcode/appcode.pyt")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"hello.py", "widget.py"}.issubset(archive.namelist())


def test_standalone_quickstart_contains_compilable_browser_python():
    page = (ROOT / "examples" / "quickstart" / "standalone" / "index.html").read_text()
    parser = PythonScriptParser()
    parser.feed(page)
    assert parser.python
    compile("\n".join(parser.python), "standalone-quickstart", "exec")
    assert parser.runtime_sources == ["./frontend/dist/pytincture.min.js"]
    assert not (
        ROOT / "examples" / "quickstart" / "standalone" / "pytincture.min.js"
    ).exists()
    assert "python -m pytincture.assets ./frontend" in (
        ROOT / "docs" / "quickstart.md"
    ).read_text(encoding="utf-8")


def test_configuration_reference_covers_every_backend_environment_setting():
    configured = set()
    for path in (ROOT / "pytincture").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "getenv"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            value = node.args[0].value
            if value == value.upper() and any(character.isalpha() for character in value):
                configured.add(value)

    documentation = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    missing = sorted(name for name in configured if f"`{name}`" not in documentation)
    assert missing == []


def test_required_user_documentation_is_present_and_linked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required = (
        "docs/quickstart.md",
        "docs/service-mode.md",
        "docs/standalone-mode.md",
        "docs/bff-guide.md",
        "docs/browser-packaging.md",
        "docs/configuration.md",
        "docs/authentication.md",
        "docs/production-deployment.md",
        "docs/performance.md",
        "docs/troubleshooting.md",
        "docs/migrations/0.9-to-0.10.md",
        "docs/migrations/0.10-to-1.0.md",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "docs/releasing.md",
        "docs/release-qualification.md",
    )
    for relative_path in required:
        assert (ROOT / relative_path).is_file()
        assert relative_path in readme


def test_local_markdown_links_resolve():
    markdown_files = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "CONTRIBUTING.md"]
    markdown_files.extend((ROOT / "docs").rglob("*.md"))
    failures = []
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert failures == []
