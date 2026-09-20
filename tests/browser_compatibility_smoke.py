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
from pytincture.backend.browser_packages import create_appcode_archive


WIDGET_SOURCE = '''import js
from js import document as dom
from js import eval as evaluate
from importlib import resources

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
from pyodide.ffi import create_proxy

def main():
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
                   'oldwidgets-1.0.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: oldwidgets\nVersion: 1.0.0\n',
                   'oldwidgets-1.0.0.dist-info/WHEEL':'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
            files['oldwidgets/pytincture-assets.json']=json.dumps({'schema':1,'package':'oldwidgets','version':'1.0.0','assets':[
                {'path':path,'type':'javascript' if path.endswith('.js') else 'css','sha256':hashlib.sha256(text.encode()).hexdigest()}
                for path,text in assets.items()]})
            files['oldwidgets-1.0.0.dist-info/RECORD']='\n'.join(path+',,' for path in files)+'\noldwidgets-1.0.0.dist-info/RECORD,,\n'
            for name,text in files.items(): archive.writestr(name,text)
        (root/'app.py').write_text(APP)
        (root/'registry.py').write_text('import importlib\nvalue=importlib.import_module(input())\n')
        (root/'browser_registry.py').write_text('label="browser substitute"\n')
        (root/'providers').mkdir()
        (root/'providers/__init__.py').write_text('')
        (root/'providers/demo.py').write_text('VALUE="allowed provider"\n')
        (root/'providers/denied.py').write_text('raise RuntimeError("must not execute")\n')
        config=root/'pyproject.toml'
        config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\nwidget-package="oldwidgets"\nwidget-wheel="'+wheel.name+'"\nimport-aliases={registry="browser_registry"}\ndynamic-imports=["providers.demo"]\n')
        build_browser_bundle(config)

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
                        result=json.loads(page.locator('#compat-result').inner_text())
                        assert result=={'registry':'browser substitute','provider':'allowed provider','rejected':True,'fallback':'fallback','local':'local','values':[0,1,2,3,4],'tuple':[0,1,2],'set':[0,1,2]},result
                        assert page.evaluate('window.oldWidgetLoads')==1
                        assert page.evaluate('Array.from(document.querySelectorAll("style")).filter(n=>n.textContent.includes("#compat-result")).length')==0
                        expect(page.locator('#compat-result')).to_have_css('color','rgb(12, 34, 56)')
                        page.locator('#compat-result').click()
                        expect(page.locator('#compat-result')).to_have_attribute('data-clicked','yes')
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
