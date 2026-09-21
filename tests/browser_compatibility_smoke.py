"""Real-browser regressions for private-app patterns, without private app source."""
import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import zipfile

from playwright.sync_api import expect, sync_playwright

from pytincture.browser_build import build_browser_bundle
from pytincture.browser_profile import import_source
from pytincture.backend.browser_packages import create_appcode_archive


WIDGET_SOURCE = '''import js
from js import document as dom
from pyodide.code import run_js as evaluate
from importlib import resources
import base64

def _try_inject_inter_fonts():
    data = resources.files('oldwidgets').joinpath('assets/font.bin').read_bytes()
    encoded = base64.b64encode(data).decode('ascii')
    style = dom.createElement('style')
    style.id = 'duplicate-font-work'
    style.textContent = '/*' + encoded + '*/'
    dom.head.appendChild(style)

def _try_inject_icon_fonts():
    _try_inject_inter_fonts()

_try_inject_inter_fonts()
_try_inject_icon_fonts()

def load_assets(force=False):
    content = resources.files('oldwidgets').joinpath('assets/ui.js').read_text()
    evaluate(content)
    style = dom.createElement('style')
    style.innerHTML = resources.files('oldwidgets').joinpath('assets/ui.css').read_text()
    dom.head.appendChild(style)
'''

APP = '''import js
import json
import os as environment
import importlib
import time as clock
from time import monotonic as now
from os import getenv as setting
from registry import label
from oldwidgets import load_assets
from pyodide.ffi import create_proxy, create_once_callable
from pyodide.code import run_js as evaluate_js
from pathlib import Path
from html import escape
import traceback
from stdlib_probe import validate

def main():
    validate()
    assert evaluate_js('6 * 7') == 42
    assert evaluate_js('({answer: 42})').answer == 42
    rejected_js = False
    try:
        evaluate_js(42)
    except TypeError:
        rejected_js = True
    assert rejected_js
    failed_js = False
    try:
        evaluate_js("throw new Error('run-js-probe')")
    except Exception:
        failed_js = True
    assert failed_js
    # Binary resource paths still work; avoiding duplicate transfer must not
    # make assets disappear from the interpreter filesystem.
    assert len(Path('/oldwidgets/assets/font.bin').read_bytes()) == 2 * 1024 * 1024
    caught = False
    try:
        raise KeyboardInterrupt('bare-handler')
    except:
        caught = True
        assert 'bare-handler' in traceback.format_exc()
    assert caught
    assets = Path(__file__).resolve().parent / 'sample'
    assert assets.joinpath('message.txt').read_text(encoding='utf-8') == 'bundled text'
    assert assets.is_dir()
    assert sorted(p.name for p in assets.iterdir()) == ['archive.zip', 'message.txt']
    scratch = Path('/compat-output')
    scratch.mkdir(parents=True, exist_ok=True)
    output = scratch / 'result.txt'
    assert output.write_text('hello') == 5
    assert output.read_bytes() == b'hello'
    assert output.stat().st_size == 5
    assert output.suffix == '.txt' and output.stem == 'result'
    assert str(output.relative_to(scratch)) == 'result.txt'
    output.unlink()
    scratch.rmdir()
    assert escape('<a title="x">&') == '&lt;a title=&quot;x&quot;&gt;&amp;'
    assert escape("'", quote=False) == "'"
    module_name = 'providers.demo'
    provider = importlib.import_module(module_name)
    rejected = False
    try:
        module_name = 'providers.denied'
        importlib.import_module(module_name)
    except ImportError:
        rejected = True
    started = clock.monotonic()
    assert 0 <= now() - started < 1
    load_assets()
    load_assets(force=True)
    environment.environ['BROWSER_LOCAL']='local'
    result = {
        'registry': label,
        'provider': provider.VALUE,
        'rejected': rejected,
        'fallback': setting('MISSING', 'fallback'),
        'local': environment.getenv('BROWSER_LOCAL'),
        'values': [0, *range(1, 3), 3, *[4]],
        'tuple': list((0, *range(1, 3))),
        'set': sorted({0, *range(1, 3)}),
    }
    node=js.document.createElement('button')
    node.id='compat-result'
    node.textContent=json.dumps(result)
    def click(event):
        node.setAttribute('data-clicked', 'yes')
    node.onclick=create_proxy(click)
    def once(value):
        node.setAttribute('data-once', str(value))
        return value + 1
    js.window.compatOnce = create_once_callable(once)
    js.window.compatCancelled = create_once_callable(once)
    js.document.body.appendChild(node)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('validation-results/compatibility-browser'))
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='pytincture-compat-') as temporary:
        root=Path(temporary)
        (root/'frontend').symlink_to(repo/'pytincture/frontend',target_is_directory=True)
        js='window.oldWidgetLoads = (window.oldWidgetLoads || 0) + 1;'
        css='#compat-result { color: rgb(12, 34, 56); }'
        assets={'oldwidgets/assets/ui.js':js,'oldwidgets/assets/ui.css':css}
        wheel=root/'oldwidgets-1.0.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel,'w') as archive:
            files={'oldwidgets/__init__.py':WIDGET_SOURCE,**assets,
                   'oldwidgets/assets/font.bin':b'F' * (2 * 1024 * 1024),
                   'oldwidgets-1.0.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: oldwidgets\nVersion: 1.0.0\n',
                   'oldwidgets-1.0.0.dist-info/WHEEL':'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
            files['oldwidgets/pytincture-assets.json']=json.dumps({'schema':1,'package':'oldwidgets','version':'1.0.0','assets':[
                {'path':path,'type':'javascript' if path.endswith('.js') else 'css','sha256':hashlib.sha256(text.encode()).hexdigest()}
                for path,text in assets.items()]})
            files['oldwidgets-1.0.0.dist-info/RECORD']='\n'.join(path+',,' for path in files)+'\noldwidgets-1.0.0.dist-info/RECORD,,\n'
            for name,text in files.items(): archive.writestr(name,text)
        (root/'app.py').write_text(APP)
        (root/'stdlib_probe.py').write_text((repo/'tests/fixtures/portable_stdlib/probe.py').read_text())
        (root/'registry.py').write_text('import importlib\nvalue=importlib.import_module(input())\n')
        (root/'browser_registry.py').write_text('label="browser substitute"\n')
        (root/'providers').mkdir()
        (root/'providers/__init__.py').write_text('')
        (root/'providers/demo.py').write_text('VALUE="allowed provider"\n')
        (root/'providers/denied.py').write_text('raise RuntimeError("must not execute")\n')
        (root/'sample').mkdir()
        (root/'sample/message.txt').write_text('bundled text')
        with zipfile.ZipFile(root/'sample/archive.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.txt', 'portable archive')
        config=root/'pyproject.toml'
        config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\nwidget-package="oldwidgets"\nwidget-wheel="'+wheel.name+'"\nimport-aliases={registry="browser_registry"}\ndynamic-imports=["providers.demo"]\nresources=["sample/message.txt", "sample/archive.zip"]\n')
        manifest_path = build_browser_bundle(config)
        manifest = json.loads(manifest_path.read_text())
        # Resources are references, not a second base64 copy of the large font.
        assert manifest['integrity']['resources.json']['bytes'] < 10000
        report = json.loads((manifest_path.parent/'compatibility.json').read_text())
        assert 'widget-font-ownership' in json.dumps(report)

        # The real legacy archive must carry the discovered installed dotenv
        # package. Browser entry discovery must follow imported aliases and MRO.
        (root/'legacy.py').write_text('from screens import ActualWindow as Launch\n')
        (root/'base.py').write_text('class MainWindow: pass\nclass Intermediate(MainWindow): pass\n')
        (root/'screens.py').write_text('''from base import Intermediate
import io
import js
from dotenv import dotenv_values
class ActualWindow(Intermediate):
    def __init__(self):
        node=js.document.createElement('div')
        node.id='legacy-result'
        node.textContent=dotenv_values(stream=io.StringIO('LABEL=legacy-ready'))['LABEL']
        js.document.body.appendChild(node)
''')
        archive=create_appcode_archive('localhost','http','legacy',str(root),lambda *a,source_code,**kw:source_code)
        (root/'legacy/appcode').mkdir(parents=True)
        (root/'legacy/appcode/appcode.pyt').write_bytes(archive.getvalue())
        (root/'index.html').write_text('<!doctype html><div id="maindiv"></div><script src="/frontend/dist/pytincture.js"></script>')
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*a,**kw): super().__init__(*a,directory=str(root),**kw)
            def log_message(self,*a): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        origin='http://127.0.0.1:'+str(server.server_port)
        results=[]
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch()
                for engine in ('micropython','pyodide','legacy'):
                    page=browser.new_page(); errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin)
                    options={'application':'app','runtime':engine,'deliveryMode':'portable-bundle',
                             'runtimeManifestUrl':'/browser/app/manifest.json','pyodideBaseUrl':origin+'/frontend/pyodide/0.29.3/full/',
                             'enableBackendLogging':False,'loadMaterialIcons':False,'warmPyodideCache':False}
                    if engine=='legacy':
                        options.update(application='legacy',runtime='pyodide',deliveryMode='legacy-package',mode='package',
                                       widgetlib='oldwidgets==1.0.0',widgetSource=origin+'/'+wheel.name+'#sha256='+hashlib.sha256(wheel.read_bytes()).hexdigest())
                    page.evaluate('(options) => {window.startup=runTinctureApp(options).catch(e=>{window.startupError=String(e);throw e;});}', options)
                    page.wait_for_function('!document.getElementById("pytincture-loading") || window.startupError',timeout=180000)
                    assert not page.evaluate('window.startupError || null'), page.evaluate('window.startupError')
                    if engine=='legacy':
                        expect(page.locator('#legacy-result')).to_have_text('legacy-ready')
                    else:
                        if engine == 'pyodide':
                            source = 'def _pep701_render(value):\n    return f"""before {("" if value else f"""nested {value}""")} after"""\n'
                            page.evaluate('source => pytinctureBrowserRuntime.runtime.runPython(source)', import_source(source))
                            for value, expected in [('True', 'before  after'), ('False', 'before nested False after')]:
                                assert page.evaluate('source => pytinctureBrowserRuntime.runtime.runPython(source)', '_pep701_render('+value+')') == expected
                        result=json.loads(page.locator('#compat-result').inner_text())
                        assert result=={'registry':'browser substitute','provider':'allowed provider','rejected':True,'fallback':'fallback','local':'local','values':[0,1,2,3,4],'tuple':[0,1,2],'set':[0,1,2]},result
                        assert page.evaluate('window.oldWidgetLoads')==1
                        assert page.locator('#duplicate-font-work').count() == 0
                        assert page.evaluate('Array.from(document.querySelectorAll("style")).filter(n=>n.textContent.includes("#compat-result")).length')==0
                        expect(page.locator('#compat-result')).to_have_css('color','rgb(12, 34, 56)')
                        page.locator('#compat-result').click()
                        expect(page.locator('#compat-result')).to_have_attribute('data-clicked','yes')
                        assert page.evaluate('compatOnce(41)') == 42
                        expect(page.locator('#compat-result')).to_have_attribute('data-once','41')
                        page.locator('#portable-listener').click()
                        expect(page.locator('#portable-listener')).to_have_attribute('data-count', '1')
                        page.evaluate('removePortableListener()')
                        page.locator('#portable-listener').click()
                        expect(page.locator('#portable-listener')).to_have_attribute('data-count', '1')
                        assert page.evaluate('() => {try {compatOnce(100); return false;} catch {return true;}}')
                        assert page.evaluate('() => {compatCancelled.destroy(); try {compatCancelled(100); return false;} catch {return true;}}')
                        expect(page.locator('#compat-result')).to_have_attribute('data-once','41')
                    assert not errors,errors
                    page.screenshot(path=str(args.output/(engine+'.png')))
                    results.append({'engine':engine,'status':'passed','identity':page.evaluate('pytinctureRuntime.getInfo()')})
                    (args.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
                    print(engine+' passed',flush=True)
                    page.close()
                browser.close()
        finally:
            server.shutdown(); server.server_close()


if __name__=='__main__': main()
