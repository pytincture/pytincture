"""Optional real-browser check for a second app built by the shared compiler.

Run with a Python environment containing Playwright and Chromium:
  PYTHONPATH=. python tests/browser_build_smoke.py --micropython-assets /path/to/npm/package
"""
import argparse
import json
from pathlib import Path
import socket
import shutil
import tempfile
import threading
import time

from playwright.sync_api import expect, sync_playwright
import uvicorn

from pytincture import PytinctureConfig, create_app
from pytincture.browser_build import build_browser_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--micropython-assets', type=Path)
    parser.add_argument('--widget-wheel', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='pytincture-second-app-') as temporary:
        root = Path(temporary)
        shutil.copytree(Path(__file__).with_name('browser_apps'), root, dirs_exist_ok=True)
        config = root / 'pyproject.toml'
        settings = '[tool.pytincture.browser]\n'
        if args.micropython_assets:
            settings += 'micropython-assets = ' + json.dumps(str(args.micropython_assets.resolve())) + '\n'
        if args.widget_wheel:
            settings += 'widget-wheel = ' + json.dumps(str(args.widget_wheel.resolve())) + '\n'
        config.write_text(settings
                          + '[tool.pytincture.browser.apps.warehouse]\n'
                          + 'assets=["public/settings.json", "public/theme.css"]\nstyles=["public/theme.css"]\n'
                          + '[tool.pytincture.browser.apps.widgets]\n')
        for name in ('warehouse', 'widgets'):
            manifest = build_browser_bundle(config, application=name)
            assert 'server-implementation-must-not-be-shipped' not in (manifest.parent / 'sources.json').read_text()
        app = create_app(PytinctureConfig(
            modules_path=str(root), allow_runtime_selection=True,
            environment={
                'PYTINCTURE_PUBLIC_ASSET_PATHS': '{"warehouse":["browser/warehouse/*"],"widgets":["browser/widgets/*"]}',
                'ALLOWED_NOAUTH_CLASSCALLS': json.dumps([{
                    'application': 'warehouse', 'file': 'api/catalog.py',
                    'class': 'Catalog', 'function': method,
                } for method in ('lookup', 'combine', 'ping')]),
            },
        ))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            base = f'http://127.0.0.1:{listener.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Test server did not start')
                    time.sleep(0.01)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    for runtime in ('micropython', 'pyodide'):
                        page = browser.new_page()
                        errors = []
                        requests = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                        page.on('request', lambda request: requests.append(request.url))
                        page.goto(base + '/warehouse?runtime=' + runtime)
                        expect(page.locator('h1')).to_have_text('Warehouse: part-7 / 3', timeout=60000)
                        page.wait_for_function('window.pytinctureAppReady', timeout=20000)
                        page.get_by_role('button', name='Use the DOM', exact=True).click()
                        expect(page.get_by_role('button')).to_have_text('DOM callback worked')
                        assert page.title() == 'Warehouse'
                        assert not errors, errors
                        assert any('/classcall/api/catalog/Catalog/lookup' in url for url in requests)
                        assert any('/pyodide/' in url for url in requests) == (runtime == 'pyodide')
                        print(runtime + ': direct DOM, callbacks, nested imports, BFF defaults/variadic/GET, assets and dataclasses passed', flush=True)
                        page.close()
                        page = browser.new_page()
                        page.on('response', lambda response: print('FAILED RESOURCE', response.url, response.status, flush=True) if response.status >= 400 else None)
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
                        page.goto(base + '/widgets?runtime=' + runtime)
                        page.wait_for_function('window.pytinctureAppReady || document.body.innerText.includes(\"Failed during\")', timeout=60000)
                        assert page.evaluate('window.pytinctureAppReady || false'), errors
                        expect(page.get_by_text('Nested layout initialized')).to_be_visible()
                        expect(page.get_by_text('First card', exact=True)).to_be_visible()
                        expect(page.get_by_text('First task', exact=True)).to_be_visible()
                        expect(page.get_by_text('Chat ready', exact=True)).to_be_visible()
                        assert page.title() == 'Widget Workspace'
                        assert not errors, errors
                        print(runtime + ': nested layouts, CardPanel, Kanban and Chat passed', flush=True)
                        page.close()
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == '__main__':
    main()
