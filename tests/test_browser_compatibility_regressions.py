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


@pytest.mark.parametrize('statement', ['from pyodide.ffi import create_once_callable', 'from pathlib import Path', 'from html import escape', 'from pyodide.code import run_js', 'from pyodide.code import run_js as evaluate'])
def test_new_portable_imports_build_on_both_engines(tmp_path, statement):
    (tmp_path/'app.py').write_text(statement+'\ndef main(): pass\n')
    config=tmp_path/'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\n')
    manifest=build_browser_bundle(config)
    sources=json.loads((manifest.parent/'sources.json').read_text())['files']
    pyodide=json.loads((manifest.parent/'sources-pyodide.json').read_text())['files']
    assert statement in pyodide['app.py']
    for name in ('pathlib','html'):
        if 'from '+name in statement:
            path = 'html/__init__.py' if name == 'html' else name+'.py'
            assert path in sources and path not in pyodide


def test_bare_handlers_catch_base_exceptions_and_keep_nested_tracebacks():
    source='''import traceback
_runtime_error = 'user value'
try:
    raise KeyboardInterrupt('outer')
except:
    before = traceback.format_exc()
    try:
        raise ValueError('inner')
    except:
        inner = traceback.format_exc()
    after = traceback.format_exc()
'''
    converted=adapt(source)
    assert 'except as ' not in converted
    tree=ast.parse(converted)
    tree.body=[node for node in tree.body if not isinstance(node,(ast.Import,ast.ImportFrom))]
    import traceback
    namespace={'browser_base_exception':BaseException,'format_exception':lambda error:''.join(traceback.format_exception(error))}
    exec(compile(tree,'converted','exec'),namespace)
    assert 'KeyboardInterrupt: outer' in namespace['before']
    assert namespace['before']==namespace['after']
    assert 'ValueError: inner' in namespace['inner']
    assert namespace['_runtime_error']=='user value'


@pytest.mark.parametrize('path', ['', '/', 'a/b.tar.gz', '/a/./b/../c', '.hidden', 'a/b.'])
def test_portable_path_properties_match_cpython(path):
    namespace={}
    exec(Path('pytincture/browser_templates/pathlib.py.txt').read_text(),namespace)
    portable=namespace['Path'](path)
    native=Path(path)
    for attribute in ('name','suffix','suffixes','stem','parts'):
        # Browser Pyodide is pinned to CPython 3.13. Python 3.14 hosts changed
        # trailing-dot suffixes; the build host must not change this profile.
        reference = {'suffix':'', 'suffixes':[], 'stem':'b.'}
        expected = reference[attribute] if path == 'a/b.' and attribute in reference else getattr(native,attribute)
        assert getattr(portable,attribute)==expected
    assert str(portable)==str(native)
    assert str(portable.parent)==str(native.parent)
    assert list(map(str,portable.parents))==list(map(str,native.parents))
    assert str(portable.joinpath('child'))==str(native.joinpath('child'))


def test_portable_paths_read_write_and_error_behavior(tmp_path):
    namespace={}
    exec(Path('pytincture/browser_templates/pathlib.py.txt').read_text(),namespace)
    directory=namespace['Path'](str(tmp_path))/'nested'/'deep'
    directory.mkdir(parents=True)
    file=directory/'value.txt'
    assert file.write_text('héllo',encoding='utf-8')==5
    assert file.read_text()=='héllo'
    assert file.read_bytes()=='héllo'.encode()
    assert file.is_file() and directory.is_dir()
    assert file.stat().st_size == len('héllo'.encode())
    assert list(directory.iterdir())==[file]
    assert str(file.relative_to(directory))=='value.txt'
    assert file.resolve(strict=True)==file
    with pytest.raises(OSError): file.mkdir(exist_ok=True)
    with pytest.raises(ValueError): file.relative_to('/unrelated')
    file.unlink()
    assert not file.exists()
    with pytest.raises(OSError): file.resolve(strict=True)
    file.unlink(missing_ok=True)
    directory.rmdir()


def test_portable_html_escape_matches_cpython():
    import html
    namespace={}
    exec(Path('pytincture/browser_templates/html.py.txt').read_text(),namespace)
    for value in ('', '<a title="x">&\' café', '&amp;', 'plain'):
        for quote in (True,False):
            assert namespace['escape'](value,quote)==html.escape(value,quote)


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
    monkeypatch.setitem(sys.modules, '_pytincture_resources', types.ModuleType('_pytincture_resources'))
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


@pytest.mark.parametrize('ready', [False, True, None])
def test_widget_font_guards_skip_work_only_for_the_ready_package(monkeypatch, ready):
    from pytincture.browser_sources import bridge_widget_imports
    reads = []
    host = types.SimpleNamespace()
    if ready is not None:
        host.pytinctureAssets = types.SimpleNamespace(isPackageReady=lambda package: ready and package == 'widgets')
    monkeypatch.setitem(sys.modules, 'js', types.SimpleNamespace(pytinctureWidgetBridge=host))
    source = """def _try_inject_inter_fonts():
    'Keep my docstring'
    reads.append('inter')
def _try_inject_icon_fonts():
    reads.append('icons')
def unrelated_initializer():
    reads.append('other')
alias = _try_inject_inter_fonts
alias()
_try_inject_icon_fonts()
unrelated_initializer()
"""
    findings = []
    converted = bridge_widget_imports(source, package='widgets', report=findings, filename='widgets/__init__.py')
    namespace = {'reads': reads}
    exec(converted, namespace)
    assert reads == (['other'] if ready else ['inter', 'icons', 'other'])
    assert namespace['_try_inject_inter_fonts'].__doc__ == 'Keep my docstring'
    assert [item['rule'] for item in findings] == ['widget-font-ownership'] * 2
    # Source outside the Widgetset is not given a package ownership guard.
    reads.clear()
    exec(bridge_widget_imports(source), namespace)
    assert reads == ['inter', 'icons', 'other']


def test_run_js_preserves_result_type_check_and_errors():
    helpers = ast.parse(Path('pytincture/browser_templates/compat.py.txt').read_text())
    helpers.body = [n for n in helpers.body if isinstance(n, ast.FunctionDef) and n.name == 'run_js']
    values = []
    def evaluate(code, widget):
        assert not widget
        values.append(code)
        if code == 'throw':
            return types.SimpleNamespace(ok=False, error='JS error')
        return types.SimpleNamespace(ok=True, value=42)
    namespace = {'js': types.SimpleNamespace(pytinctureRunJavascript=evaluate)}
    exec(compile(helpers, 'helpers', 'exec'), namespace)
    call = namespace['run_js']
    assert call('6 * 7') == 42
    with pytest.raises(TypeError): call(42)
    with pytest.raises(TypeError): call(code='6 * 7')
    with pytest.raises(RuntimeError, match='JS error'): call('throw')
    assert values == ['6 * 7', 'throw']
