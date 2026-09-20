"""Exercise the real applications in all supported delivery/engine combinations.

Run with a Python environment containing Playwright. App checkouts must contain
the validation launchers and tests recorded in docs/browser-runtime-validation.md.
This script owns its servers, uses their isolated databases, and never pulls,
resets, publishes, or reads a production application configuration.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'legacy-pyodide': ('pyodide', 'legacy-package'),
    'portable-pyodide': ('pyodide', 'portable-bundle'),
    'portable-micropython': ('micropython', 'portable-bundle'),
}
INSTRUMENT = """() => {
  window.__csp = [];
  addEventListener('securitypolicyviolation', e => __csp.push({directive:e.effectiveDirective,uri:e.blockedURI}));
  window.__widgetAssignments = {};
  let widgets = new Proxy({}, {set(target,key,value) {
    if (typeof value === 'function') __widgetAssignments[key] = (__widgetAssignments[key] || 0) + 1;
    target[key] = value; return true;
  }});
  Object.defineProperty(window, 'wapyt', {configurable:true, get:()=>widgets, set:value=>{widgets=value;}});
}"""


def command(args, cwd, log, env):
    with log.open('w') as output:
        result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Conformance command failed; see {log}')


@contextmanager
def server(repo, python, port, delivery, output, env):
    try:
        urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1)
    except OSError:
        pass
    else:
        raise RuntimeError(f'Port {port} is already serving an application; stop that test server first')
    with output.open('w') as log:
        process = subprocess.Popen([str(python), 'tests/runtime_server.py'], cwd=repo,
                                   env={**env, 'PYTINCTURE_TEST_DELIVERY_MODE': delivery}, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+45
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f'Application failed to start; see {output}')
                try:
                    urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1).close()
                    break
                except OSError:
                    time.sleep(.2)
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def probe(app, engine, delivery, output):
    """Fresh/reset routes, cold/warm identity, CSP, fonts and asset execution."""
    base = 'http://127.0.0.1:'+('8095/py_ui' if app == 'example' else '8094/chat')
    routes = ['GRID VIEW', 'FORM VIEW', 'Book Ratings Chart', 'CALENDAR VIEW', 'Reports'] if app == 'example' else ['Chat', 'Providers', 'Users']
    evidence = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width':1400,'height':950})
        context.add_init_script('('+INSTRUMENT+')()')
        login = context.request.get(base+'/login')
        token = re.search(r'name="login_csrf_token" value="([^"]+)"', login.text())[1]
        response = context.request.post(base+'/auth/user', form={
            'email':'demo@example.com', 'password':'demo-password', 'login_csrf_token':token,
        }, max_redirects=0)
        assert response.status == 303
        for index, route in enumerate(routes):
            page = context.new_page()
            errors, failed, requests = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.on('response', lambda response: failed.append([response.status,response.url]) if response.status >=400 else None)
            page.on('request', lambda request: requests.append(request.url))
            page.goto(base+('?runtime='+engine if delivery == 'portable-bundle' else ''))
            page.locator('#pytincture-loading').wait_for(state='hidden', timeout=180000)
            if app == 'example':
                page.locator('.dhx_grid-row').first.wait_for(timeout=30000)
                page.get_by_text(re.compile('^'+re.escape(route)+'$', re.I)).first.click()
            else:
                page.get_by_role('button', name=route, exact=True).click()
                expect(page.get_by_role('button', name={'Chat':'New chat','Providers':'Add Provider','Users':'Add User'}[route], exact=True)).to_be_visible()
            cold = page.evaluate('pytinctureRuntime.getInfo()')
            assert cold['engine'] == engine and cold['deliveryMode'] == delivery, cold
            assert cold['pythonImplementation'] == ('cpython' if engine == 'pyodide' else 'micropython'), cold
            assert bool(cold['bundleId']) == (delivery == 'portable-bundle'), cold
            assert page.locator('body').get_attribute('data-delivery-mode') == delivery
            assert any('/pyodide/' in url for url in requests) == (engine == 'pyodide'), requests
            assert not any('/micropython.wasm' in url for url in requests) if engine == 'pyodide' else True
            await_fonts = page.evaluate('async () => { await document.fonts.ready; return document.fonts.status; }')
            assert await_fonts == 'loaded'
            if app == 'chat' and delivery == 'portable-bundle':
                ownership = page.evaluate('pytinctureAssets.getInfo()')
                assert ownership['packages'] == ['wapyt'], ownership
                assignments = page.evaluate('__widgetAssignments')
                assert assignments and all(count == 1 for count in assignments.values()), assignments
                # Even an explicit force request must not re-evaluate manifest-owned assets.
                page.evaluate('pytinctureBrowserRuntime.runtime.runPython("import wapyt._runtime as r; r._load_assets(force=True)")')
                assert page.evaluate('__widgetAssignments') == assignments
                duplicate_css = page.locator('style').evaluate_all("""async nodes => {
                    const owned = new Set(pytinctureAssets.getInfo().assets.filter(a=>a.path.endsWith('.css')).map(a=>a.sha256));
                    const hashes = await Promise.all(nodes.map(async n=>Array.from(new Uint8Array(
                        await crypto.subtle.digest('SHA-256', new TextEncoder().encode(n.textContent))),b=>b.toString(16).padStart(2,'0')).join('')));
                    return hashes.some(hash=>owned.has(hash));
                }""")
                assert not duplicate_css, 'Manifest-owned widget CSS was injected again'
            assert not page.evaluate('__csp'), page.evaluate('__csp')
            page.screenshot(path=str(output/f'{app}-{index}.png'))
            page.reload()
            page.locator('#pytincture-loading').wait_for(state='hidden', timeout=180000)
            warm = page.evaluate('pytinctureRuntime.getInfo()')
            assert not errors and not failed, [errors, failed]
            assert not page.evaluate('__csp'), page.evaluate('__csp')
            evidence.append({'route':route,'cold':cold,'warm':warm,'errors':errors,'failed_requests':failed,'csp':[]})
            page.close()
        browser.close()
    (output/f'{app}-diagnostics.json').write_text(json.dumps(evidence, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('example', 'chat', 'example-python', 'chat-python'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--prepared', type=Path, help='prepared.json from prepare_application_conformance.py')
    parser.add_argument('--output', type=Path, default=ROOT/'validation-results/full-apps')
    parser.add_argument('--profile', choices=PROFILES, action='append')
    parser.add_argument('--app', choices=['example','chat'], action='append')
    args = parser.parse_args()
    env = {**os.environ, 'PYTHONPATH':str(ROOT)}
    if args.prepared:
        prepared = json.loads(args.prepared.read_text())
        args.example, args.chat = Path(prepared['example']), Path(prepared['chat'])
        args.example_python = args.chat_python = Path(prepared['python'])
        env['WHISPER_MODEL'] = prepared['voice_model']
    if not all((args.example,args.chat,args.example_python,args.chat_python)):
        parser.error('Provide --prepared or both app paths and their Python executables')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    for repo, python in [(args.example,args.example_python),(args.chat,args.chat_python)]:
        command([python,'-m','pytincture.browser_build','--config','pyproject.toml'],repo,args.output/(repo.name+'-build.log'),env)
    for profile in args.profile or PROFILES:
        engine, delivery = PROFILES[profile]
        output = args.output/profile
        output.mkdir(parents=True, exist_ok=True)
        print('Validating '+profile, flush=True)
        if not args.app or 'example' in args.app:
            with server(args.example,args.example_python,8095,delivery,output/'example-server.log',env):
                command([sys.executable,'tests/ui_smoke.py','--base-url','http://127.0.0.1:8095','--screenshot-dir',output/'example',
                         *(['--runtime',engine] if delivery == 'portable-bundle' else [])], args.example, output/'example.log',env)
                command([sys.executable,'tests/browser_interactions.py','--runtime',engine,'--output',output/'example-interactions'],args.example,output/'example-interactions.log',env)
                probe('example',engine,delivery,output)
        if not args.app or 'chat' in args.app:
            with server(args.chat,args.chat_python,8094,delivery,output/'chat-server.log',env):
                for script in ('browser_runtime_smoke.py','browser_voice_smoke.py'):
                    command([sys.executable,'tests/'+script,'--runtime',engine,'--output',output/'chat'],args.chat,output/(script+'.log'),env)
                probe('chat',engine,delivery,output)
        print(profile+' passed',flush=True)


if __name__ == '__main__':
    main()
