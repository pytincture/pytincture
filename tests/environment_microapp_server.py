"""Loopback-only acceptance fixture for environment bundles. Never deploy it."""
import asyncio
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pytincture import PytinctureConfig
from pytincture.microapps import EnvironmentMicroappManager, DirectoryObjectStore, empty_manifest, create_microapp_host
from pytincture.microapps.builder import build_environment_bundle
from test_environment_microapps import app_package
from test_live_microapps import wheel

root = Path(tempfile.mkdtemp(prefix='pytincture-environment-e2e-')).resolve()
objects = DirectoryObjectStore(root / 'objects')
manager = EnvironmentMicroappManager(root / 'runtime', PytinctureConfig(), objects=objects,
    environment='dev-browser', poll_seconds=0.1)
specs = {}


async def provision():
    doc = empty_manifest('dev-browser')
    for app_id in ['orders', 'reports', 'dormant']:
        specs[app_id] = await app_package(root, objects, app_id, value=99 if app_id == 'reports' else 42)
    doc['apps']['orders'] = specs['orders']
    doc['apps']['dormant'] = {**specs['dormant'], 'installed': False}
    base = wheel(root, 'tenzyn_shell', '1.0.0', {'shell.py':
        "from js import document\nbase_startup_marker = 'original shell'\n"
        "def main():\n    document.getElementById('status').textContent = 'Shell running'\n"})
    key = 'wheels/base/' + Path(base.path).name
    await objects.put(key, Path(base.path).read_bytes(), expected=None)
    doc['base'] = {'artifact': {'key': key, 'sha256': base.sha256}, 'application': 'shell', 'entrypoint': 'main'}
    await manager.initialize(doc)
    # Publish the initial browser artifact before the ASGI/BFF service starts.
    await build_environment_bundle(objects, environment='dev-browser')
asyncio.run(provision())

base = FastAPI()
base.mount('/frontend', StaticFiles(directory=Path(__file__).resolve().parents[1] / 'pytincture/frontend'))


@base.get('/healthz')
async def health(): return {'ok': True}


@base.post('/test-only-trigger-bff')
async def trigger(): return {'ok': True}


@base.get('/shell/classcall/ping.py/Ping/read')
async def ping(): return 7


@base.get('/')
async def shell():
    return HTMLResponse('''<!doctype html><html><body>
<div id="status">Starting platform</div><nav id="apps"></nav><main id="maindiv"><div id="microapp"></div></main>
<script src="/frontend/pytincture.js"></script>
<script>
window.pytinctureAutoStartDisabled = true;
window.addEventListener('pytincture:microapps', event => {
  for (const app of event.detail.apps) {
    if (document.getElementById('open-' + app.id)) continue;
    const button = document.createElement('button'); button.id = 'open-' + app.id;
    button.textContent = 'Open ' + app.id;
    button.onclick = () => pytinctureMicroapps.mount(app.id, document.getElementById('microapp')).catch(e => window.failure = String(e));
    document.getElementById('apps').append(button);
  }
});
window.starting = runTinctureApp({application:'shell', entrypoint:'main', mode:'package',
  widgetlib:null, pyodideBaseUrl:'/frontend/pyodide/0.29.3/full/', loadMaterialIcons:false,
  enableServiceWorker:false, environmentCatalogUrl:'/_microapps/catalog'}).then(runtime => {
    window.runtime=runtime; window.client=pytinctureMicroapps;
    document.getElementById('status').textContent='Platform ready';
    client.pollMs=3600000;
    clearTimeout(client.timer);
}).catch(e => {window.failure=String(e); document.getElementById('status').textContent=String(e);});
</script></body></html>''')


def approve(scope, command):
    if command.get('app_id') not in specs or command.get('version') != '0.1.0': return None
    return {**specs[command['app_id']], 'installed': command['installed']}

app = create_microapp_host(base, manager, authorize=lambda scope, app_id: True, authorize_install=approve)
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=41840, log_level='warning')
