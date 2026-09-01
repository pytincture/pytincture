# Five-minute quickstart

Pytincture has two supported modes. Service mode packages Python application
files and exposes authenticated BFF calls. Standalone mode runs inline Python
from a static HTML page and has no Pytincture backend.

## Service mode

Requirements: Python 3.13 or 3.14.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install pytincture
git clone https://github.com/pytincture/pytincture.git
cd pytincture/examples/quickstart/service
python -m uvicorn service:app --port 8070
```

Open <http://127.0.0.1:8070/>. The root redirects to `/hello` without adding a
cache UUID to the visible URL. Pytincture downloads the application package,
starts bundled Pyodide, installs `dhxpyt==0.9.18`, and calls `hello.load_ui()`.

The example has three files: [`service.py`](../examples/quickstart/service/service.py)
creates the ASGI app, [`hello.py`](../examples/quickstart/service/hello.py) runs
in the browser, and [`widget.py`](../examples/quickstart/service/widget.py)
declares browser widget metadata. Continue with [service mode](service-mode.md).

## Standalone mode

Requirements: Pytincture installed while preparing the static site. The
deployed host itself only needs a static HTTP server.

```bash
python -m pip install 'pytincture==1.0.0rc4'
cd pytincture/examples/quickstart/standalone
python -m pytincture.assets ./frontend
python3 -m http.server 8000
```

Open <http://127.0.0.1:8000/>. Do not open the HTML through `file://`; browsers
restrict module, worker, and network behavior for local files. The runnable
example contains [`index.html`](../examples/quickstart/standalone/index.html)
and a verified, version-matched local runtime exported from the installed
wheel. Continue with
[standalone mode](standalone-mode.md).

## Verify the service

```bash
curl --fail http://127.0.0.1:8070/healthz
curl --fail http://127.0.0.1:8070/readyz
```

Both return HTTP 200 for a ready service. Browser startup failures appear in
the loading panel with a named stage; use the [troubleshooting guide](troubleshooting.md)
to interpret them.
