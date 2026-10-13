"""Exercise the Book Library example in all supported delivery/engine combinations.

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

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'legacy-pyodide': ('pyodide', 'legacy-package'),
    'portable-pyodide': ('pyodide', 'portable-bundle'),
    'portable-micropython': ('micropython', 'portable-bundle'),
}
INSTRUMENT = """() => {
  window.__csp = [];
  addEventListener('securitypolicyviolation', e => __csp.push({directive:e.effectiveDirective,uri:e.blockedURI}));
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
    base = 'http://127.0.0.1:8095/py_ui'
    routes = ['GRID VIEW', 'FORM VIEW', 'Book Ratings Chart', 'CALENDAR VIEW', 'Reports']
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
            page.locator('.dhx_grid-row').first.wait_for(timeout=30000)
            page.get_by_text(re.compile('^'+re.escape(route)+'$', re.I)).first.click()
            cold = page.evaluate('pytinctureRuntime.getInfo()')
            assert cold['engine'] == engine and cold['deliveryMode'] == delivery, cold
            assert cold['pythonImplementation'] == ('cpython' if engine == 'pyodide' else 'micropython'), cold
            assert bool(cold['bundleId']) == (delivery == 'portable-bundle'), cold
            assert page.locator('body').get_attribute('data-delivery-mode') == delivery
            assert any('/pyodide/' in url for url in requests) == (engine == 'pyodide'), requests
            assert not any('/micropython.wasm' in url for url in requests) if engine == 'pyodide' else True
            await_fonts = page.evaluate('async () => { await document.fonts.ready; return document.fonts.status; }')
            assert await_fonts == 'loaded'
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
    for name in ('example', 'example-python'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--prepared', type=Path, help='prepared.json from prepare_application_conformance.py')
    parser.add_argument('--output', type=Path, default=ROOT/'validation-results/full-apps')
    parser.add_argument('--profile', choices=PROFILES, action='append')
    parser.add_argument('--app', choices=['example'], action='append')
    args = parser.parse_args()
    # This suite repeatedly opens/reloads full applications from one peer.
    # Give its browser diagnostics enough capacity without changing production
    # defaults or hiding browser errors and failed HTTP responses.
    env = {
        **os.environ, 'PYTHONPATH':str(ROOT),
        'BROWSER_LOG_RATE_LIMIT_ATTEMPTS': os.environ.get('BROWSER_LOG_RATE_LIMIT_ATTEMPTS', '1000'),
    }
    if args.prepared:
        prepared = json.loads(args.prepared.read_text())
        args.example = Path(prepared['example'])
        args.example_python = Path(prepared['python'])
    if not all((args.example,args.example_python)):
        parser.error('Provide --prepared or --example and --example-python')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    for repo, python in [(args.example,args.example_python)]:
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
        print(profile+' passed',flush=True)


if __name__ == '__main__':
    main()
