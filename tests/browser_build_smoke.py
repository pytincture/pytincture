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
    parser.add_argument('--output', type=Path, default=Path('validation-results/conformance-fixtures'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = []
    with tempfile.TemporaryDirectory(prefix='pytincture-second-app-') as temporary:
        root = Path(temporary)
        shutil.copytree(Path(__file__).with_name('browser_apps'), root, dirs_exist_ok=True)
        (root/'components/logo.bin').write_bytes(b'\x00\xffportable-resource')
        editor_vendor = Path(__file__).resolve().parents[1]/'pytincture/frontend/node_modules/codemirror'
        for name in ('lib/codemirror.js', 'lib/codemirror.css', 'LICENSE'):
            target = root/'public/editor'/Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(editor_vendor/name, target)
        config = root / 'pyproject.toml'
        settings = '[tool.pytincture.browser]\n'
        if args.micropython_assets:
            settings += 'micropython-assets = ' + json.dumps(str(args.micropython_assets.resolve())) + '\n'
        if args.widget_wheel:
            settings += 'widget-wheel = ' + json.dumps(str(args.widget_wheel.resolve())) + '\n'
        config.write_text(settings
                          + '[tool.pytincture.browser.apps.warehouse]\nresources=["components/logo.bin"]\n'
                          + 'assets=["public/settings.json", "public/theme.css"]\nstyles=["public/theme.css"]\n'
                          + '[tool.pytincture.browser.apps.widgets]\n'
                          + '[tool.pytincture.browser.apps.editor]\nassets=["public/editor/codemirror.js","public/editor/codemirror.css","public/editor/LICENSE"]\nscripts=["public/editor/codemirror.js"]\nstyles=["public/editor/codemirror.css"]\n')
        for name in ('warehouse', 'widgets', 'editor'):
            manifest = build_browser_bundle(config, application=name)
            assert 'server-implementation-must-not-be-shipped' not in (manifest.parent / 'sources.json').read_text()
        app = create_app(PytinctureConfig(
            modules_path=str(root), allow_runtime_selection=True, delivery_mode="portable-bundle",
            environment={
                'PYTINCTURE_PUBLIC_ASSET_PATHS': '{"warehouse":["browser/warehouse/*"],"widgets":["browser/widgets/*"],"editor":["browser/editor/*"]}',
                'ALLOWED_NOAUTH_CLASSCALLS': json.dumps([{
                    'application': 'warehouse', 'file': 'api/catalog.py',
                    'class': 'Catalog', 'function': method,
                } for method in ('lookup', 'combine', 'ping', 'events', 'raw_events')]),
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
                    context = browser.new_context(viewport={'width':1400,'height':1000})
                    context.add_init_script("window.__cspViolations=[];document.addEventListener('securitypolicyviolation',e=>window.__cspViolations.push({directive:e.violatedDirective,uri:e.blockedURI}));")
                    for runtime in ('micropython', 'pyodide'):
                        page = context.new_page()
                        errors = []
                        requests = []
                        page.on('pageerror', lambda error: (errors.append(str(error)), print('BROWSER ERROR', str(error), flush=True)))
                        page.on('requestfailed', lambda request: None if request.failure == 'net::ERR_ABORTED' and
                                request.url.endswith(('/classcall/api/catalog/Catalog/events', '/classcall/api/catalog/Catalog/raw_events'))
                                else errors.append('Request failed: '+request.url+' '+str(request.failure)))
                        page.on('console', lambda message: (errors.append(message.text), print('CONSOLE ERROR', message.text, flush=True)) if message.type == 'error' else None)
                        page.on('request', lambda request: requests.append(request.url))
                        page.goto(base + '/warehouse?runtime=' + runtime)
                        expect(page.locator('h1')).to_have_text('Warehouse: part-7 / 3', timeout=60000)
                        page.wait_for_function('window.pytinctureAppReady', timeout=20000)
                        page.locator('#pytincture-loading').wait_for(state='hidden', timeout=20000)
                        identity = page.evaluate('pytinctureRuntime.getInfo()')
                        assert identity['engine'] == runtime and identity['deliveryMode'] == 'portable-bundle'
                        assert identity['pythonImplementation'] == ('micropython' if runtime == 'micropython' else 'cpython'), identity
                        assert identity['bundleId'] and identity['compatibilityProfile'] == 'pytincture-portable-1'
                        assert any(t['stage'] == 'module-import' and t['durationMs'] >= 0 for t in identity['startupTimings'])
                        page.get_by_role('button', name='Use the DOM', exact=True).click()
                        expect(page.get_by_role('button')).to_have_text('DOM callback worked')
                        assert page.title() == 'Warehouse'
                        assert page.evaluate("pytinctureBrowserRuntime.captureOutput(\"print('artifact café', end='')\")") == 'artifact café'
                        assert page.evaluate("() => {try {pytinctureBrowserRuntime.captureOutput(\"raise ValueError('expected')\")} catch (_) {return pytinctureBrowserRuntime.captureOutput(\"print('recovered')\")} }") == 'recovered\n'
                        assert not errors, errors
                        assert any('/classcall/api/catalog/Catalog/lookup' in url for url in requests)
                        assert any('/pyodide/' in url for url in requests) == (runtime == 'pyodide')
                        print(runtime + ': direct DOM, callbacks, nested imports, BFF sync/async/streaming/defaults/variadic/GET, assets and dataclasses passed', flush=True)
                        page.close()
                        page = context.new_page()
                        page.on('response', lambda response: print('FAILED RESOURCE', response.url, response.status, flush=True) if response.status >= 400 else None)
                        page.on('pageerror', lambda error: (errors.append(str(error)), print('BROWSER ERROR', str(error), flush=True)))
                        page.on('requestfailed', lambda request: None if request.failure == 'net::ERR_ABORTED' and
                                request.url.endswith(('/classcall/api/catalog/Catalog/events', '/classcall/api/catalog/Catalog/raw_events'))
                                else errors.append('Request failed: '+request.url+' '+str(request.failure)))
                        page.on('console', lambda message: (errors.append(message.text), print('CONSOLE ERROR', message.text, flush=True)) if message.type == 'error' else None)
                        page.goto(base + '/widgets?runtime=' + runtime)
                        page.wait_for_function('window.pytinctureAppReady || document.body.innerText.includes(\"Failed during\")', timeout=60000)
                        assert page.evaluate('window.pytinctureAppReady || false'), errors
                        expect(page.get_by_text('Nested layout initialized')).to_be_visible()
                        expect(page.get_by_text('First card', exact=True)).to_be_visible()
                        expect(page.get_by_text('First task', exact=True)).to_be_visible()
                        expect(page.get_by_text('Chat ready', exact=True)).to_be_visible()
                        assert page.title() == 'Widget Workspace'
                        assert not errors, errors
                        page.screenshot(path=str(args.output/(runtime+'-widgets.png')))
                        assert not page.evaluate('window.__cspViolations'), page.evaluate('window.__cspViolations')
                        print(runtime + ': nested layouts, CardPanel, Kanban and Chat passed', flush=True)
                        page.close()
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(base+'/editor?runtime='+runtime)
                        page.locator('#pytincture-loading').wait_for(state='hidden', timeout=60000)
                        cold = page.evaluate('pytinctureRuntime.getInfo()')
                        page.get_by_role('button',name='Open editor').click()
                        page.keyboard.press('ControlOrMeta+A')
                        page.keyboard.insert_text('portable editor '+runtime)
                        page.get_by_role('button',name='Save code').click()
                        expect(page.locator('#saved-code')).to_have_text('portable editor '+runtime)
                        page.get_by_role('button',name='Open editor').click()
                        expect(page.locator('.CodeMirror')).to_contain_text('portable editor '+runtime)
                        page.get_by_role('button',name='Save code').click()
                        page.reload()
                        page.locator('#pytincture-loading').wait_for(state='hidden',timeout=60000)
                        warm = page.evaluate('pytinctureRuntime.getInfo()')
                        page.get_by_role('button',name='Open editor').click()
                        expect(page.locator('.CodeMirror')).to_contain_text('portable editor '+runtime)
                        await_fonts = page.evaluate('async () => {await document.fonts.ready; return document.fonts.status}')
                        assert await_fonts == 'loaded'
                        assert not page.evaluate('window.__cspViolations'), page.evaluate('window.__cspViolations')
                        assert not errors, errors
                        page.screenshot(path=str(args.output/(runtime+'-editor.png')))
                        evidence.append({'runtime':runtime,'cold':cold,'warm':warm,'csp_violations':[]})
                        page.close()
                        print(runtime+': CodeMirror editing, persistent modal state, reload, identity and cold/warm timings passed',flush=True)
                    (args.output/'results.json').write_text(json.dumps(evidence,indent=2))
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == '__main__':
    main()
