import ast
import json
import sys
import zipfile
from types import ModuleType

import pytest

from pytincture.browser_build import build_browser_bundle
from pytincture.browser_boundaries import guard_server_only_imports
from pytincture.browser_profile import CompatibilityError


def project(root, source, settings='', excluded='["yaml", "app_settings", "apps.manifest_loader", "fastapi"]'):
    (root / 'app.py').write_text(source)
    config = root / 'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\n'
                      f'server-only-imports={excluded}\n' + settings)
    return config


def test_exclusions_stop_discovery_preserve_imports_and_real_client_inputs(tmp_path, monkeypatch):
    (tmp_path / 'yaml.py').write_text('not valid Python! Never even parse this')
    (tmp_path / 'app_settings.py').write_text('import must_not_traverse\n')
    (tmp_path / 'apps').mkdir()
    (tmp_path / 'apps/__init__.py').write_text('')
    (tmp_path / 'apps/manifest_loader.py').write_text('invalid Python!')
    (tmp_path / 'apps/client.py').write_text('VALUE = "real synced client"\n')
    (tmp_path / 'yaml_extra.py').write_text('VALUE = "adjacent name"\n')
    source = '''from apps.client import VALUE
import yaml_extra
try:
    import yaml
except ImportError:
    yaml = None
try:
    from app_settings import settings
except ImportError:
    settings = "browser settings"
try:
    from apps import manifest_loader
except ImportError:
    manifest_loader = None
try:
    from fastapi.responses import Response
except ImportError:
    Response = None
async def main(): pass
'''
    config = project(tmp_path, source, 'files=["apps/client.py"]\n')
    manifest = build_browser_bundle(config)
    for filename in ('sources.json', 'sources-pyodide.json'):
        files = json.loads((manifest.parent / filename).read_text())['files']
        assert {'apps/client.py', 'yaml_extra.py'} <= files.keys()
        assert not {'yaml.py', 'app_settings.py', 'apps/manifest_loader.py'} & files.keys()
        assert 'import yaml' in files['app.py']
        assert 'from app_settings import settings' in files['app.py']
        assert 'from apps import manifest_loader' in files['app.py']
        assert 'must_not_traverse' not in json.dumps(files)
    # Even a host-installed/server module must not bypass the browser fallback.
    monkeypatch.setitem(sys.modules, 'yaml', ModuleType('yaml'))
    namespace = {}
    guarded = guard_server_only_imports('try:\n import yaml\nexcept ImportError:\n yaml = None\n',
                                      filename='app.py', boundaries=('yaml',), report=[])
    exec(guarded, namespace)
    assert namespace['yaml'] is None
    report = json.loads((manifest.parent / 'compatibility.json').read_text())
    for result in report['runtimes'].values():
        assert result['server_only_imports'] == ['app_settings', 'apps.manifest_loader', 'fastapi', 'yaml']
        assert len([f for f in result['findings'] if f['rule'] == 'server-only-import']) == 4


@pytest.mark.parametrize('source', [
    'import yaml\nasync def main(): pass',
    'async def main():\n import yaml',
    'class Startup:\n import yaml\nasync def main(): pass',
    'try:\n pass\nexcept ImportError:\n import yaml\nasync def main(): pass',
    'try:\n pass\nexcept ImportError:\n pass\nelse:\n import yaml\nasync def main(): pass',
    'try:\n pass\nexcept ImportError:\n pass\nfinally:\n import yaml\nasync def main(): pass',
    'try:\n def delayed():\n  import yaml\nexcept ImportError:\n pass\nasync def main(): delayed()',
    'try:\n import yaml\nexcept ValueError:\n pass\nasync def main(): pass',
])
def test_unguarded_imports_fail_before_publication_with_source_location(tmp_path, source):
    config = project(tmp_path, source)
    with pytest.raises(CompatibilityError, match='unguarded browser import') as error:
        build_browser_bundle(config)
    assert not (tmp_path / 'browser').exists()
    for result in error.value.report['runtimes'].values():
        finding = next(f for f in result['findings'] if f['rule'] == 'server-only-import')
        assert finding['file'] == 'app.py' and finding['line'] > 0
        assert finding['severity'] == 'error'


@pytest.mark.parametrize('handler', ['ImportError', '(ValueError, ImportError)', 'Exception', 'BaseException', ''])
def test_guarded_function_and_class_scopes_preserve_fallback(handler):
    source = f'''def client():
    try:
        class Startup:
            import yaml
    except {handler}:
        return "fallback"
'''.replace('except :', 'except:')
    namespace = {}
    exec(guard_server_only_imports(source, filename='client.py', boundaries=['yaml'], report=[]), namespace)
    assert namespace['client']() == 'fallback'


def test_relative_imports_and_outer_handler_cover_inner_finally(tmp_path):
    (tmp_path / 'apps').mkdir()
    (tmp_path / 'apps/__init__.py').write_text('')
    (tmp_path / 'apps/client.py').write_text('''try:
    try:
        pass
    finally:
        from . import manifest_loader
except ImportError:
    manifest_loader = None
''')
    config = project(tmp_path, 'from apps import client\nasync def main(): pass\n')
    build_browser_bundle(config)


def test_bff_stub_ignores_server_implementation_imports(tmp_path):
    (tmp_path / 'data.py').write_text('''import yaml
from fastapi import Request
from pytincture import backend_for_frontend
@backend_for_frontend
class Data:
    async def read(self, key):
        return yaml.safe_load(key)
''')
    config = project(tmp_path, 'from data import Data\nasync def main(): return await Data().read("x")\n')
    manifest = build_browser_bundle(config)
    for name in ('sources.json', 'sources-pyodide.json'):
        files = json.loads((manifest.parent / name).read_text())['files']
        assert 'pytinctureBrowserBff' in files['data.py']
        assert 'yaml' not in files['data.py'] and 'fastapi' not in files['data.py']


@pytest.mark.parametrize('excluded', ['"yaml"', '[1]', '["../yaml"]', '["yaml.*"]', '["a..b"]', '["class"]', '["_pytincture_compat"]'])
def test_invalid_boundaries(tmp_path, excluded):
    config = project(tmp_path, 'async def main(): pass', excluded=excluded)
    with pytest.raises(ValueError, match='server-only-imports'):
        build_browser_bundle(config)


@pytest.mark.parametrize('settings', [
    'files=["yaml.py"]', 'bff=["yaml.py"]', 'dynamic-imports=["yaml"]',
    'import-aliases={yaml="browser_yaml"}', 'import-aliases={client="yaml"}',
    'resources=["yaml/data.json"]', 'assets=["yaml/ui.js"]',
])
def test_explicit_input_conflicts_are_not_silently_discarded(tmp_path, settings):
    config = project(tmp_path, 'async def main(): pass', settings)
    with pytest.raises(ValueError, match='conflicts with server-only-imports'):
        build_browser_bundle(config)


def test_entrypoint_conflict_is_checked_before_reading_source(tmp_path):
    config = project(tmp_path, 'invalid source', excluded='["app"]')
    with pytest.raises(ValueError, match='entrypoint conflicts'):
        build_browser_bundle(config)


def test_wheel_exclusion_skips_sources_native_extensions_and_package_data(tmp_path):
    with zipfile.ZipFile(tmp_path / 'mixed.whl', 'w') as archive:
        archive.writestr('yaml/__init__.py', 'invalid source!')
        archive.writestr('yaml/parser.so', b'not a browser library')
        archive.writestr('yaml/private.json', '{"fixture":"server-only"}')
        archive.writestr('client_lib.py', 'VALUE = 42\n')
    config = project(tmp_path, 'import client_lib\nasync def main(): pass\n', 'wheels=["mixed.whl"]\n')
    manifest = build_browser_bundle(config)
    files = json.loads((manifest.parent / 'sources.json').read_text())['files']
    assert 'client_lib.py' in files
    assert not any(name.startswith('yaml/') for name in files)
    assert 'yaml/' not in json.dumps(json.loads(manifest.read_text())['integrity'])


def test_native_and_optional_shim_imports_are_preserved_and_excluded(tmp_path):
    source = 'try:\n from pathlib import Path\nexcept ImportError:\n Path = None\nasync def main(): pass\n'
    config = project(tmp_path, source, excluded='["pathlib"]')
    manifest = build_browser_bundle(config)
    for name in ('sources.json', 'sources-pyodide.json'):
        files = json.loads((manifest.parent / name).read_text())['files']
        assert 'pathlib.py' not in files
        assert 'from pathlib import Path' in files['app.py']


def test_non_excluded_missing_imports_still_fail_and_main_guard_is_ignored(tmp_path):
    config = project(tmp_path, 'if __name__ == "__main__":\n import yaml\nasync def main(): pass\n')
    build_browser_bundle(config, check=True)
    (tmp_path / 'app.py').write_text('try:\n import accidental_missing\nexcept ImportError:\n pass\nasync def main(): pass\n')
    with pytest.raises(ValueError, match='unresolved browser import accidental_missing'):
        build_browser_bundle(config, check=True)


def test_multi_imports_keep_earlier_bindings_and_import_statements():
    source = 'try:\n import json, yaml\nexcept ImportError:\n value = json.loads("42")\n'
    transformed = guard_server_only_imports(source, filename='app.py', boundaries=['yaml'], report=[])
    namespace = {}
    exec(transformed, namespace)
    assert namespace['value'] == 42
    assert [alias.name for node in ast.walk(ast.parse(transformed)) if isinstance(node, ast.Import)
            for alias in node.names] == ['json', 'yaml']


def test_excluded_from_import_does_not_traverse_unneeded_parent(tmp_path):
    (tmp_path / 'apps').mkdir()
    (tmp_path / 'apps/__init__.py').write_text('invalid server initializer!')
    (tmp_path / 'apps/manifest_loader.py').write_text('invalid server implementation!')
    config = project(tmp_path, 'try:\n from apps import manifest_loader\nexcept ImportError:\n pass\nasync def main(): pass\n')
    manifest = build_browser_bundle(config)
    files = json.loads((manifest.parent / 'sources.json').read_text())['files']
    assert not any(name.startswith('apps/') for name in files)


def test_required_shim_dependency_conflict_fails_before_startup(tmp_path):
    config = project(tmp_path, 'import html\nasync def main(): pass\n', excluded='["html.entities"]')
    with pytest.raises(CompatibilityError, match='required browser support imports server-only module html.entities'):
        build_browser_bundle(config)


def test_app_specific_boundary_does_not_leak_to_other_apps(tmp_path):
    config = project(tmp_path, 'try:\n import yaml\nexcept ImportError:\n pass\nasync def main(): pass\n',
                     '[tool.pytincture.browser.apps.other]\nserver-only-imports=[]\n')
    with pytest.raises(CompatibilityError, match='unresolved browser import yaml'):
        build_browser_bundle(config, application='other')


def test_adjacent_resource_names_are_not_python_module_children(tmp_path):
    (tmp_path / 'app_settings.json').write_text('{"theme":"browser"}')
    config = project(tmp_path, 'async def main(): pass\n', 'resources=["app_settings.json"]\n')
    manifest = build_browser_bundle(config)
    assert 'vendor/app_settings.json' in json.loads(manifest.read_text())['integrity']
