import ast
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from pytincture.browser_build import build_browser_bundle
from pytincture.browser_compatibility import adapt
from pytincture.browser_sources import validate_import_aliases
from pytincture.backend import browser_dependencies
from pytincture.backend.browser_packages import create_appcode_archive, AppcodeArchiveCache
from pytincture.backend.pages import find_main_window_subclass


def test_computed_import_allowlist_is_bundled_and_enforced_before_execution(tmp_path, monkeypatch):
    (tmp_path/'app.py').write_text('import importlib\nmodule_name="providers.demo"\nprovider=importlib.import_module(module_name)\ndef main(): return provider\n')
    (tmp_path/'providers').mkdir()
    (tmp_path/'providers/__init__.py').write_text('')
    (tmp_path/'providers/demo.py').write_text('VALUE="allowed"\n')
    (tmp_path/'providers/denied.py').write_text('raise RuntimeError("must not execute")\n')
    config=tmp_path/'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\ndynamic-imports=["providers.demo"]\n')
    result=build_browser_bundle(config)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ('sources.json','sources-pyodide.json'):
        sources=json.loads((result.parent/name).read_text())['files']
        assert 'providers/demo.py' in sources
        assert 'providers/denied.py' not in sources
        assert '_pytincture_checked_import(module_name)' in sources['app.py']
        namespace={}
        exec(sources['_pytincture_imports.py'],namespace)
        assert namespace['import_module']('providers.demo').VALUE=='allowed'
        assert namespace['import_module']('.demo','providers').VALUE=='allowed'
        for denied in ('providers.denied','providers.demo.child','os','providers'):
            with pytest.raises(ImportError,match='not allowlisted'):
                namespace['import_module'](denied)
        assert 'providers.denied' not in sys.modules
    # Leave no synthetic package behind for another test.
    for name in ('providers.demo','providers'):
        sys.modules.pop(name,None)


def test_computed_import_aliases_and_keywords_are_guarded():
    from pytincture.browser_profile import guard_dynamic_imports
    findings=[]
    source='import importlib as imports\nfrom importlib import import_module as load\na=imports.import_module(name=selected)\nb=load(selected)\n'
    converted=guard_dynamic_imports(source,engine='pyodide',report=findings,filename='app.py')
    assert 'a = _pytincture_checked_import(name=selected)' in converted
    assert 'b = _pytincture_checked_import(selected)' in converted
    assert len(findings)==2


def test_monotonic_clock_uses_browser_milliseconds_and_handles_aliases(monkeypatch):
    source='import time as clock\nfrom time import monotonic as now\na=clock.monotonic()\nb=now()\n'
    findings=[]
    converted=adapt(source,report=findings,filename='app.py')
    assert 'a = browser_monotonic()' in converted
    assert 'browser_monotonic as now' in converted
    helpers=ast.parse(Path('pytincture/browser_templates/compat.py.txt').read_text())
    helpers.body=[node for node in helpers.body if isinstance(node,ast.FunctionDef) and node.name=='browser_monotonic']
    clock=iter([1234.5,1244.5])
    namespace={'js':types.SimpleNamespace(performance=types.SimpleNamespace(now=lambda:next(clock)))}
    exec(compile(helpers,'clock','exec'),namespace)
    assert namespace['browser_monotonic']()==1.2345
    assert namespace['browser_monotonic']()==1.2445
    assert any(f['rule']=='adapt-Attribute' for f in findings)


def test_substitute_registry_excludes_computed_server_registry_and_preserves_relative_imports(tmp_path):
    (tmp_path/'app.py').write_text('from registry import selected\nasync def main(): return selected\n')
    (tmp_path/'registry.py').write_text('import importlib\nSERVER_SECRET="excluded"\nselected=importlib.import_module(input())\n')
    (tmp_path/'browser').mkdir()
    (tmp_path/'browser/__init__.py').write_text('')
    (tmp_path/'browser/registry.py').write_text('from .views import selected\n')
    (tmp_path/'browser/views.py').write_text('selected = "portable"\n')
    config=tmp_path/'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\nimport-aliases={registry="browser.registry"}\n')
    manifest=build_browser_bundle(config)
    for name in ('sources.json','sources-pyodide.json'):
        sources=json.loads((manifest.parent/name).read_text())['files']
        assert 'SERVER_SECRET' not in json.dumps(sources)
        assert 'from browser.views import selected' in sources['registry.py']
        assert 'browser/views.py' in sources
    report=json.loads((manifest.parent/'compatibility.json').read_text())
    for result in report['runtimes'].values():
        assert result['import_substitutions']=={'registry.py':'browser/registry.py'}


@pytest.mark.parametrize('aliases', [{'a':'a'}, {'a':'a.child'}, {'a':'b','b':'a'}, {'a':'../private'}, ['a']])
def test_invalid_substitute_mapping_is_rejected(aliases):
    with pytest.raises(ValueError):
        validate_import_aliases(aliases)


def test_package_substitute_preserves_relative_child_identity(tmp_path):
    from pytincture.browser_sources import discover_sources
    (tmp_path/'app.py').write_text('import registry.child\n')
    (tmp_path/'browser_registry').mkdir()
    (tmp_path/'browser_registry/__init__.py').write_text('from . import child\n')
    (tmp_path/'browser_registry/child.py').write_text('VALUE=1\n')
    sources,_=discover_sources(tmp_path,'app.py',[],[],import_aliases={'registry':'browser_registry'})
    assert set(sources)=={'app.py','registry/__init__.py','registry/child.py'}
    assert 'from registry import child' in sources['registry/__init__.py']


@pytest.mark.parametrize('expression', ['[mark(0), *items(), mark(3), *tail()]', '(mark(0), *items(), mark(3), *tail())', '{mark(0), *items(), mark(3), *tail()}'])
def test_iterable_display_preserves_values_evaluation_and_iteration_order(expression):
    prelude='''events=[]
def mark(value):
    events.append(value)
    return value
def items():
    events.append('start')
    yield mark(1)
    yield mark(2)
    events.append('end')
def tail():
    events.append('tail')
    return (4, 5)
'''
    native={}
    exec(prelude+'result='+expression, native)
    tree=ast.parse(adapt(prelude+'result='+expression))
    tree.body=[node for node in tree.body if not isinstance(node,(ast.Import,ast.ImportFrom))]
    helpers=ast.parse(Path('pytincture/browser_templates/compat.py.txt').read_text())
    helpers.body=[node for node in helpers.body if isinstance(node,ast.FunctionDef) and node.name in {'_expand_list','_expand_set','_display_tuple'}]
    converted={'initialize_layout':lambda cls:cls}
    exec(compile(helpers,'helpers','exec'),converted)
    exec(compile(tree,'transformed','exec'),converted)
    assert converted['result']==native['result']
    assert type(converted['result']) is type(native['result'])
    assert converted['events']==native['events']


def test_getenv_shim_never_copies_server_environment(monkeypatch):
    monkeypatch.setenv('PRIVATE_SERVER_VALUE','must-not-leak')
    fake_os=types.ModuleType('os')
    fake_sys=types.SimpleNamespace(implementation=types.SimpleNamespace(name='micropython'))
    monkeypatch.setitem(sys.modules,'os',fake_os)
    monkeypatch.setitem(sys.modules,'sys',fake_sys)
    monkeypatch.setitem(sys.modules,'js',types.SimpleNamespace())
    monkeypatch.setitem(sys.modules,'jsffi',types.SimpleNamespace(create_proxy=lambda value:value,JsProxy=type('JsProxy',(),{})))
    namespace={}
    exec(Path('pytincture/browser_templates/compat.py.txt').read_text(),namespace)
    assert fake_os.getenv('PRIVATE_SERVER_VALUE') is None
    assert fake_os.getenv('missing','default')=='default'
    fake_os.environ['local']='browser-only'
    assert fake_os.getenv('local')=='browser-only'
    with pytest.raises(TypeError): fake_os.getenv(42)


def test_entrypoint_aliases_are_accepted_and_unknown_inheritance_can_defer_to_browser():
    assert find_main_window_subclass('app.py',source_code='class Screen: pass\nLaunch=Screen\nAPP_ENTRYPOINT="Launch"\n')=='Launch'
    assert find_main_window_subclass('app.py',source_code='from screens import Screen as Launch\nAPP_ENTRYPOINT="Launch"\n')=='Launch'
    assert find_main_window_subclass('app.py',source_code='from shared import Base\nclass Screen(Base): pass\n',allow_browser_discovery=True) is None


def test_legacy_archive_discovers_installed_pure_python_dependencies_without_importing_them(tmp_path, monkeypatch):
    root=tmp_path/'app'; root.mkdir()
    installed=tmp_path/'installed'; (installed/'dotenv').mkdir(parents=True)
    (root/'app.py').write_text('from dotenv import dotenv_values\nclass app: pass\n')
    (installed/'dotenv/__init__.py').write_text('raise RuntimeError("build must never import installed code")\n')
    (installed/'dotenv/data.json').write_text('{"package":"data"}')
    (installed/'dotenv/.env').write_text('PRIVATE_SECRET=never-copy')
    owned=['dotenv/__init__.py','dotenv/data.json','dotenv/.env']
    distribution=types.SimpleNamespace(files=owned,version='1.2.2',metadata={'Name':'python-dotenv'},requires=[],
        read_text=lambda name:'Root-Is-Purelib: true\n',locate_file=lambda name:installed/name)
    monkeypatch.setattr(browser_dependencies,'metadata',types.SimpleNamespace(
        packages_distributions=lambda:{'dotenv':['python-dotenv']},distribution=lambda name:distribution,PackageNotFoundError=LookupError))
    def parser(*args,source_code,**kwargs): return source_code
    cache=AppcodeArchiveCache(max_entries=4)
    manifest={}
    archive=create_appcode_archive('localhost','http','app',str(root),parser,cache=cache,manifest_out=manifest)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read('dotenv/data.json')==b'{"package":"data"}'
        assert 'dotenv/.env' not in bundle.namelist()
    assert manifest['dependencies'][0]['distribution']=='python-dotenv'
    (installed/'dotenv/__init__.py').write_text('updated=True\n')
    with zipfile.ZipFile(create_appcode_archive('localhost','http','app',str(root),parser,cache=cache)) as bundle:
        assert bundle.read('dotenv/__init__.py')==b'updated=True\n'
