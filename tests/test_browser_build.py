import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest

from pytincture.browser_build import _bff_stub, build_browser_bundle
from pytincture.browser_compatibility import adapt


@pytest.fixture(autouse=True)
def browser_helpers(monkeypatch):
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace())
    module = ModuleType('_pytincture_bff')
    exec(Path('pytincture/browser_templates/bff.py.txt').read_text(), module.__dict__)
    monkeypatch.setitem(sys.modules, '_pytincture_bff', module)


def project(tmp_path, *, client='async def main():\n    pass\n', bff=''):
    (tmp_path / 'client.py').write_text(client)
    (tmp_path / 'runtime').mkdir()
    for name in ('micropython.mjs', 'micropython.wasm'):
        (tmp_path / 'runtime' / name).write_bytes((Path(__file__).resolve().parents[1]/'pytincture/browser_vendor'/name).read_bytes())
    bff_setting = ''
    if bff:
        (tmp_path / 'api').mkdir()
        (tmp_path / 'api/catalog.py').write_text(bff)
        (tmp_path / 'api/__init__.py').write_text('raise RuntimeError("SERVER_ONLY")\n')
        bff_setting = 'bff = ["api/catalog.py"]\n'
    config = tmp_path / 'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint = "client:main"\n'
                      'entry-kind = "async"\nfiles = ["client.py"]\n'
                      'micropython-assets = "runtime"\n' + bff_setting)
    return config


BFF = '''from pytincture import backend_for_frontend, bff_external
SERVER_ONLY = "never ship server implementations"
raise RuntimeError("must not execute during build")
@backend_for_frontend
class Catalog:
    def lookup(self, sku, limit=DEFAULT_LIMIT, *, category=None):
        return SERVER_ONLY
    @bff_external
    def external(self):
        return SERVER_ONLY
'''


def test_independent_app_builds_nested_bff_without_importing_backend(tmp_path):
    config = project(tmp_path, client='from api.catalog import Catalog\nasync def main():\n    return await Catalog().lookup_async("part-7")\n', bff=BFF)
    result = build_browser_bundle(config)
    manifest = json.loads(result.read_text())
    sources = json.loads((result.parent / 'sources.json').read_text())['files']
    assert manifest['runtimes'] == ['pyodide', 'micropython']
    assert manifest['scripts'] == []
    assert sources['api/__init__.py'] == ''
    assert 'SERVER_ONLY' not in json.dumps(sources)
    assert 'external_async' not in sources['api/catalog.py']
    assert 'api/catalog' in sources['api/catalog.py']
    assert 'from client import main' in sources['_pytincture_bootstrap.py']
    assert 'py_ui' not in json.dumps(sources)


def test_generated_stub_preserves_arguments_and_server_defaults(monkeypatch):
    calls = []

    async def request(module, cls, method, payload):
        calls.append((module, cls, method, json.loads(payload)))
        return '{"ok":true}'

    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace(pytinctureBrowserBff=request))
    namespace = {}
    exec(_bff_stub('api/catalog.py', BFF), namespace)
    instance = namespace['Catalog']()
    assert asyncio.run(instance.lookup_async('p7')) == {'ok': True}
    asyncio.run(instance.lookup_async('p8', 0, category=None))
    assert calls == [('api/catalog', 'Catalog', 'lookup', {'sku': 'p7'}),
                     ('api/catalog', 'Catalog', 'lookup', {'sku': 'p8', 'limit': 0, 'category': None})]
    with pytest.raises(TypeError):
        instance.lookup_async()


def test_backend_cannot_be_declared_as_browser_source(tmp_path):
    config = project(tmp_path, client=BFF + '\nasync def main(): pass\n')
    with pytest.raises(ValueError, match='contains backend code'):
        build_browser_bundle(config)
    assert not (tmp_path / 'browser').exists()


@pytest.mark.parametrize('source, message', [
    ('import numpy\nasync def main(): pass', 'unresolved browser import numpy'),
    ('from local_helper import value\nasync def main(): pass', 'declare local_helper'),
    ('class UI(metaclass=Special): pass\nasync def main(): pass', 'Custom metaclasses'),
    ('from pyodide.ffi import unsupported\nasync def main(): pass', 'Unsupported pyodide.ffi import'),
])
def test_unsupported_sources_fail_without_overwriting_existing_bundle(tmp_path, source, message):
    config = project(tmp_path)
    config.write_text(config.read_text() + 'discover-imports = false\n')
    manifest = build_browser_bundle(config)
    before = (manifest.parent / 'sources.json').read_bytes()
    (tmp_path / 'local_helper.py').write_text('value = 1')
    (tmp_path / 'client.py').write_text(source)
    with pytest.raises(ValueError, match=message):
        build_browser_bundle(config)
    assert (manifest.parent / 'sources.json').read_bytes() == before


@pytest.mark.parametrize('path', ['../outside.py', '/outside.py', 'client.py/../outside.py'])
def test_declared_sources_cannot_escape_root(tmp_path, path):
    config = project(tmp_path)
    config.write_text(config.read_text().replace('files = ["client.py"]', f'files = [{json.dumps(path)}]'))
    with pytest.raises(ValueError, match='relative public path'):
        build_browser_bundle(config)


def test_symlink_source_cannot_escape_root(tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    config = project(root)
    (tmp_path / 'private.py').write_text('private = True')
    (root / 'client.py').unlink()
    (root / 'client.py').symlink_to(tmp_path / 'private.py')
    with pytest.raises(ValueError, match='escapes its root'):
        build_browser_bundle(config)


def test_stream_stubs_return_portable_async_iterators():
    source = 'from pytincture import backend_for_frontend, bff_stream\n@backend_for_frontend\nclass Catalog:\n    @bff_stream\n    def events(self): yield 1'
    namespace = {}
    exec(_bff_stub('catalog.py', source), namespace)
    stream = namespace['Catalog']().events()
    assert stream.__aiter__() is stream
    assert stream.arguments[:4] == ('catalog', 'Catalog', 'events', '{}')
    asyncio.run(stream.aclose())
    with pytest.raises(StopAsyncIteration):
        asyncio.run(stream.__anext__())


def test_compatibility_preserves_dictionary_overrides_and_exception_binding(monkeypatch):
    monkeypatch.setitem(sys.modules, '_pytincture_compat', SimpleNamespace(
        merge_dicts=lambda *parts: dict(item for part in parts for item in part.items()),
        format_exception=lambda error: str(error), to_python=lambda value: value, spawn=lambda value: value,
        initialize_layout=lambda cls: cls, can_to_python=lambda value: False, browser_event_loop=lambda: None, string_title=lambda value: value.title(),
    ))
    monkeypatch.setitem(sys.modules, '_pytincture_dataclasses', SimpleNamespace())
    namespace = {}
    exec(adapt('base={"value":1}\nresult={**base,"value":2}\ntry:\n    raise ValueError("expected")\nexcept Exception as err:\n    import traceback\n    error=traceback.format_exc()\n'), namespace)
    assert namespace['result'] == {'value': 2}
    assert namespace['error'] == 'expected'


def test_import_graph_discovers_relative_packages_and_stops_at_bff(tmp_path):
    config = project(tmp_path, client='from views import screen\nasync def main(): return screen.value\n', bff=BFF)
    (tmp_path / 'views').mkdir()
    (tmp_path / 'views/__init__.py').write_text('from .screen import value\n')
    (tmp_path / 'views/screen.py').write_text('from api.catalog import Catalog\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import private_backend\nvalue = 7\n')
    (tmp_path / 'private_backend.py').write_text('SERVER_ONLY = "never publish"\n')
    # Nothing except the entry module is manually listed; BFF is inferred too.
    config.write_text(config.read_text().replace('bff = ["api/catalog.py"]\n', ''))
    result = build_browser_bundle(config)
    files = json.loads((result.parent / 'sources.json').read_text())['files']
    assert 'views/screen.py' in files
    assert 'from .screen import value' in files['views/__init__.py']
    assert 'lookup_async' in files['api/catalog.py']
    assert files['api/__init__.py'] == ''
    assert 'private_backend.py' not in files
    assert 'SERVER_ONLY' not in json.dumps(files)


def test_multiple_app_builds_are_isolated_and_check_does_not_write(tmp_path):
    config = project(tmp_path)
    (tmp_path / 'other.py').write_text('APP_ENTRYPOINT="main"\nasync def main(): pass\n')
    config.write_text('[tool.pytincture.browser]\nmicropython-assets="runtime"\n'
                      '[tool.pytincture.browser.apps.first]\nentrypoint="client:main"\n'
                      '[tool.pytincture.browser.apps.other]\n')
    assert build_browser_bundle(config, application='first', check=True).name == 'manifest.json'
    assert not (tmp_path / 'browser').exists()
    first = build_browser_bundle(config, application='first')
    other = build_browser_bundle(config, application='other')
    assert first != other
    first_files = json.loads((first.parent / 'sources.json').read_text())['files']
    other_files = json.loads((other.parent / 'sources.json').read_text())['files']
    assert 'other.py' not in first_files and 'client.py' not in other_files
    with pytest.raises(ValueError, match='Choose an application'):
        build_browser_bundle(config)


def test_browser_wheel_and_assets(tmp_path):
    import zipfile
    config = project(tmp_path, client='import browsermath\nasync def main(): return browsermath.double(7)\n')
    wheel = tmp_path / 'helpers.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('browsermath/__init__.py', 'def double(value): return value * 2')
    (tmp_path / 'theme.css').write_text('body {color: navy;}')
    config.write_text(config.read_text() + 'wheels=["helpers.whl"]\nassets=["theme.css"]\nstyles=["theme.css"]\n')
    result = build_browser_bundle(config)
    files = json.loads((result.parent / 'sources.json').read_text())['files']
    assert 'browsermath/__init__.py' in files
    assert (result.parent / json.loads(result.read_text())['assetBase'] / 'assets/theme.css').read_text() == 'body {color: navy;}'
    assert json.loads(result.read_text())['styles'] == ['assets/theme.css']
    with zipfile.ZipFile(wheel, 'a') as archive:
        archive.writestr('browsermath/native.so', b'not-wasm')
    with pytest.raises(ValueError, match='native extensions'):
        build_browser_bundle(config)


def test_variable_bff_arguments_and_get_method(monkeypatch):
    calls = []
    async def request(*args):
        calls.append(args)
        return 'true'
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace(pytinctureBrowserBff=request))
    namespace = {}
    exec(_bff_stub('api/methods.py', '''from pytincture.dataclass import backend_for_frontend, bff_http_methods
@backend_for_frontend
class Methods:
    def positional(self, id, /, *parts, **options): pass
    @bff_http_methods("GET")
    def ping(self): pass
'''), namespace)
    instance = namespace['Methods']()
    asyncio.run(instance.positional_async(1, 2, active=True))
    asyncio.run(instance.ping_async())
    assert json.loads(calls[0][3]) == {'args': [1, 2], 'kwargs': {'active': True}}
    assert calls[1][-1] == 'GET'


def test_browser_dataclasses_preserve_fields_factories_inheritance_and_post_init(monkeypatch):
    from pathlib import Path
    from types import ModuleType
    dc = ModuleType('_pytincture_dataclasses')
    exec((Path(__file__).parents[1] / 'pytincture/browser_templates/dataclasses.py.txt').read_text(), dc.__dict__)
    monkeypatch.setitem(sys.modules, '_pytincture_dataclasses', dc)
    monkeypatch.setitem(sys.modules, '_pytincture_compat', SimpleNamespace(
        initialize_layout=lambda cls: cls, can_to_python=lambda x: False, browser_event_loop=lambda: None, string_title=lambda x: x.title(),
        to_python=lambda x: x, spawn=lambda x: x, format_exception=str,
        merge_dicts=lambda *parts: dict(item for part in parts for item in part.items()),
    ))
    namespace = {}
    exec(adapt('''from dataclasses import dataclass, field, asdict, replace
@dataclass
class Base:
    id: int
@dataclass
class Entry(Base):
    values: list = field(default_factory=list)
    def __post_init__(self):
        self.ready = True
first = Entry(3)
second = Entry(4)
first.values.append(7)
'''), namespace)
    assert namespace['first'].ready
    assert namespace['second'].values == []
    assert dc.asdict(namespace['first']) == {'id': 3, 'values': [7]}
    assert dc.replace(namespace['first'], id=5).id == 5
    with pytest.raises(TypeError, match='missing required'):
        namespace['Entry']()


def test_bff_payload_names_do_not_shadow_generated_locals(monkeypatch):
    calls = []
    async def request(*args):
        calls.append(json.loads(args[3]))
        return 'true'
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace(pytinctureBrowserBff=request))
    namespace = {}
    exec(_bff_stub('data.py', '''from pytincture import backend_for_frontend
@backend_for_frontend
class Data:
    def send(self, payload, _pytincture_arguments=None, _UNSET=7): pass
'''), namespace)
    asyncio.run(namespace['Data']().send_async({'id': 2}, _UNSET=0))
    assert calls == [{'payload': {'id': 2}, '_UNSET': 0}]


def test_default_build_uses_packaged_runtime(tmp_path):
    config = project(tmp_path)
    config.write_text(config.read_text().replace('micropython-assets = "runtime"\n', ''))
    result = build_browser_bundle(config)
    assert (result.parent / json.loads(result.read_text())['assetBase'] / 'vendor/micropython/micropython.wasm').read_bytes()[:4] == b'\0asm'
    assert (result.parent / json.loads(result.read_text())['assetBase'] / 'vendor/micropython/LICENSE').is_file()


def test_packaged_runtime_matches_vendor_inventory():
    import hashlib
    from pytincture.browser_build import VENDOR
    inventory = json.loads((VENDOR / 'inventory.json').read_text())
    for package in inventory['packages']:
        for name, expected in package['files'].items():
            assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == expected


def test_widget_manifest_supports_other_packages_and_verifies_assets(tmp_path):
    import hashlib
    import zipfile
    config = project(tmp_path, client='from customwidgets import main\n')
    # The entrypoint itself is local; the provider package supplies browser widgets.
    (tmp_path / 'client.py').write_text('from customwidgets import Widget\nasync def main(): pass\n')
    with zipfile.ZipFile(tmp_path / 'widgets.whl', 'w') as wheel:
        wheel.writestr('customwidgets/__init__.py', 'class Widget: pass\ndef adopt(): pass\n')
        wheel.writestr('customwidgets/assets/ui.js', 'window.customWidget = true;')
        wheel.writestr('customwidgets/assets/.keep', '')
        wheel.writestr('customwidgets/pytincture-assets.json', json.dumps({
            'schema':1, 'package':'customwidgets', 'asset_loader':{'module':'customwidgets','function':'adopt'}, 'assets':[{
                'path':'customwidgets/assets/ui.js', 'type':'javascript',
                'sha256':hashlib.sha256(b'window.customWidget = true;').hexdigest(),
            }],
        }))
    config.write_text(config.read_text() + 'widget-package="customwidgets"\nwidget-wheel="widgets.whl"\n')
    manifest = build_browser_bundle(config)
    assert json.loads(manifest.read_text())['scripts'] == ['vendor/customwidgets/assets/ui.js']
    for name in ('sources.json','sources-pyodide.json'):
        bootstrap = json.loads((manifest.parent/name).read_text())['files']['_pytincture_bootstrap.py']
        assert bootstrap.index('_adopt_assets()') < bootstrap.index('from client import')
    assert not (manifest.parent / 'vendor/customwidgets/assets/.keep').exists()
    with zipfile.ZipFile(tmp_path / 'widgets.whl', 'a') as wheel:
        wheel.writestr('customwidgets/assets/ui.js', 'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        build_browser_bundle(config)


def test_main_guard_retains_else_and_does_not_erase_or_conditions(tmp_path):
    from pytincture.browser_sources import main_only
    import ast
    assert main_only(ast.parse("__name__ == '__main__' and condition", mode='eval').body)
    assert not main_only(ast.parse("__name__ == '__main__' or condition", mode='eval').body)
    config = project(tmp_path, client="if __name__ == '__main__':\n    import desktop_only\nelse:\n    active = True\nasync def main(): pass\n")
    build_browser_bundle(config)


def test_async_generators_fail_clearly_instead_of_losing_stream_events(tmp_path):
    config = project(tmp_path, client='async def main():\n    yield 1\n')
    with pytest.raises(ValueError, match='Async generators.*__anext__'):
        build_browser_bundle(config)


def test_optional_stdlib_is_pinned_and_only_bundled_when_imported(tmp_path):
    import hashlib
    root = Path('pytincture/browser_vendor/stdlib')
    inventory = json.loads((root / 'inventory.json').read_text())
    for name, digest in inventory['modules'].items():
        assert hashlib.sha256((root / (name + '.py.txt')).read_bytes()).hexdigest() == digest
    config = project(tmp_path, client='import copy\nimport datetime\nasync def main(): pass\n')
    manifest = build_browser_bundle(config)
    sources = json.loads((manifest.parent / 'sources.json').read_text())['files']
    assert {'copy.py', 'types.py', 'datetime.py'} <= sources.keys()


def test_custom_runtime_bytes_must_match_the_profile_before_publication(tmp_path):
    config = project(tmp_path)
    manifest = build_browser_bundle(config)
    original = manifest.read_bytes()
    (tmp_path/'runtime/micropython.wasm').write_bytes(b'unknown-interpreter')
    with pytest.raises(ValueError, match='pinned portable profile'):
        build_browser_bundle(config)
    assert manifest.read_bytes() == original
