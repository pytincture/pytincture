"""Loopback-only browser acceptance fixture; never deploy this server."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pytincture import PytinctureConfig
from pytincture.microapps import LiveMicroappManager, create_microapp_host
from test_live_microapps import artifacts

root = Path(tempfile.mkdtemp(prefix="pytincture-live-e2e-")).resolve()
backend, browser = artifacts(root)
manager = LiveMicroappManager(root / "store", PytinctureConfig(modules_path=str(root)))
base = FastAPI()
base.mount("/frontend", StaticFiles(directory=Path(__file__).resolve().parents[1] / "pytincture/frontend"))


@base.get("/healthz")
async def health():
    return {"ok": True}


@base.post("/test-only-install")
async def install():
    return await manager.install("orders", backend, browser, approval_id="test-review")


@base.get("/")
async def shell():
    return HTMLResponse('''<!doctype html><html><body>
<div id="status">Starting platform</div><nav id="apps"></nav><main id="microapp"></main>
<script src="/frontend/pyodide/0.29.3/full/pyodide.js"></script>
<script type="module">
import {MicroappClient} from '/frontend/microapps.js';
window.runtime = await loadPyodide({indexURL: '/frontend/pyodide/0.29.3/full/'});
await runtime.runPythonAsync("platform_startup_marker = 'original bundle still running'");
window.client = new MicroappClient({runtime, pollMs: 1000,
  onCatalog: catalog => {
    for (const app of catalog.apps) {
      if (document.getElementById('open-' + app.id)) continue;
      const button = document.createElement('button'); button.id = 'open-' + app.id;
      button.textContent = 'Open ' + app.id;
      button.onclick = () => client.mount(app.id, document.getElementById('microapp')).catch(e => window.failure = String(e));
      document.getElementById('apps').append(button);
    }
  }, onError: e => window.failure = String(e)});
await client.start();
document.getElementById('status').textContent = 'Platform ready';
</script></body></html>''')


app = create_microapp_host(base, manager, authorize=lambda scope, app_id: True)
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=41839, log_level="warning")
