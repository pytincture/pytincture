"""Static server for qualifying the runtime installed from a Python wheel."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytincture


HOST = "127.0.0.1"
PORT = 8082
TEST_ROOT = Path(__file__).resolve().parent
FIXTURE = TEST_ROOT / "standalone_apps" / "index.html"
PACKAGE_ROOT = Path(pytincture.__file__).resolve().parent
FRONTEND_ROOT = PACKAGE_ROOT / "frontend"
RUNTIME_FILE = FRONTEND_ROOT / "dist" / "pytincture.min.js"
INSTANCE_UUID = str(uuid.uuid4())
WIDGET_WHEEL = Path(
    os.environ.get("PYTINCTURE_STANDALONE_WIDGET_WHEEL", "")
).resolve()


def _validate_wheel_install() -> metadata.Distribution:
    distribution = metadata.distribution("pytincture")
    direct_url = distribution.read_text("direct_url.json") or ""
    if '"editable": true' in direct_url:
        raise RuntimeError("Standalone acceptance requires a non-editable wheel install")
    if not RUNTIME_FILE.is_file():
        raise RuntimeError(f"Wheel is missing bundled runtime: {RUNTIME_FILE}")
    pyodide = FRONTEND_ROOT / "pyodide" / "0.29.3" / "full" / "pyodide.js"
    if not pyodide.is_file():
        raise RuntimeError(f"Wheel is missing bundled Pyodide: {pyodide}")
    if not WIDGET_WHEEL.is_file():
        raise RuntimeError(
            "PYTINCTURE_STANDALONE_WIDGET_WHEEL must identify the fallback wheel"
        )
    return distribution


DISTRIBUTION = _validate_wheel_install()


def _inside(root: Path, relative: str) -> Path | None:
    candidate = (root / unquote(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class StandaloneHandler(BaseHTTPRequestHandler):
    server_version = "PytinctureStandaloneAcceptance/1"

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def _serve(self, *, send_body: bool) -> None:
        path = urlsplit(self.path).path
        if path in {"/standalone", "/standalone/"}:
            body = FIXTURE.read_text(encoding="utf-8").replace(
                "__INSTANCE_UUID__", INSTANCE_UUID
            ).encode()
            self._respond(200, body, "text/html; charset=utf-8", send_body)
            return
        if path == "/healthz":
            body = json.dumps(
                {
                    "status": "ok",
                    "version": pytincture.__version__,
                    "distribution_version": DISTRIBUTION.version,
                    "runtime_sha256": hashlib.sha256(RUNTIME_FILE.read_bytes()).hexdigest(),
                    "instance_uuid": INSTANCE_UUID,
                    "runtime_source": "installed-python-wheel",
                }
            ).encode()
            self._respond(200, body, "application/json", send_body)
            return
        if path.startswith("/runtime/"):
            target = _inside(FRONTEND_ROOT, path.removeprefix("/runtime/"))
            self._serve_file(target, send_body)
            return
        wheel_route = (
            "/standalone_fixture/appcode/"
            "dhxpyt-0.9.17+backend-py3-none-any.whl"
        )
        if path == wheel_route:
            self._serve_file(WIDGET_WHEEL, send_body, "application/zip")
            return
        self._respond(404, b"Not found", "text/plain; charset=utf-8", send_body)

    def _serve_file(
        self,
        target: Path | None,
        send_body: bool,
        content_type: str | None = None,
    ) -> None:
        if target is None or not target.is_file():
            self._respond(404, b"Not found", "text/plain; charset=utf-8", send_body)
            return
        guessed_type = content_type or mimetypes.guess_type(target.name)[0]
        body = target.read_bytes()
        response_headers = {}
        if target.name.lower().endswith(".whl"):
            response_headers["X-Pytincture-SHA256"] = hashlib.sha256(body).hexdigest()
        self._respond(
            200,
            body,
            guessed_type or "application/octet-stream",
            send_body,
            headers=response_headers,
        )

    def _respond(
        self,
        status: int,
        body: bytes,
        content_type: str,
        send_body: bool,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"standalone-server: {format % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), StandaloneHandler).serve_forever()
