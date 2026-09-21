import base64
import json
from pathlib import Path
import zipfile

import pytest

from pytincture.browser_build import build_browser_bundle
from pytincture.browser_profile import CompatibilityError
from pytincture.browser_assets import inspect_bundle


def project(root, source, settings=''):
    (root/'app.py').write_text(source)
    config = root/'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\n'+settings)
    return config


def test_cpython_profile_is_a_separate_reference_and_reports_exclusions(tmp_path):
    config = project(tmp_path, 'from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row:\n    value: int\nasync def main(): pass\n', 'runtimes=["pyodide"]\n')
    result = build_browser_bundle(config, report_path=tmp_path/'report.json')
    report = json.loads((tmp_path/'report.json').read_text())
    assert report['runtimes']['pyodide']['status'] == 'supported'
    assert report['runtimes']['micropython']['status'] == 'unsupported'
    assert not report['runtimes']['pyodide']['shims']
    sources = json.loads((result.parent/'sources-pyodide.json').read_text())['files']
    assert 'frozen=True' in sources['app.py']
    assert 'from dataclasses import dataclass' in sources['app.py']
    namespace = {}
    exec(sources['app.py'], namespace)
    with pytest.raises(AttributeError):
        namespace['Row'](1).value = 2


def test_failure_report_includes_both_targets_and_does_not_publish(tmp_path):
    config = project(tmp_path, 'import numpy\nasync def main(): pass\n')
    with pytest.raises(CompatibilityError) as error:
        build_browser_bundle(config, report_path=tmp_path/'report.json')
    report = json.loads((tmp_path/'report.json').read_text())
    assert report == error.value.report
    assert all(result['status'] == 'unsupported' for result in report['runtimes'].values())
    assert not (tmp_path/'browser').exists()


def test_resource_wheel_data_fonts_hashes_and_reproducibility(tmp_path):
    config = project(tmp_path, 'import helper\nasync def main(): pass\n', 'wheels=["helper.whl"]\nassets=["ui.css", "font.woff2"]\nstyles=["ui.css"]\n')
    with zipfile.ZipFile(tmp_path/'helper.whl', 'w') as wheel:
        wheel.writestr('helper/__init__.py', 'from importlib import resources\n')
        wheel.writestr('helper/defaults.json', '{"color":"blue"}')
    (tmp_path/'ui.css').write_text('@font-face{font-family:test;src:url(font.woff2)}')
    (tmp_path/'font.woff2').write_bytes(b'font fixture')
    manifest_path = build_browser_bundle(config)
    before = manifest_path.read_bytes()
    manifest = json.loads(before)
    revision = manifest_path.parent/manifest['assetBase']
    resources = json.loads((revision/'resources.json').read_text())['files']
    assert json.loads((revision/resources['helper/defaults.json']['asset']).read_text()) == {'color': 'blue'}
    assert inspect_bundle(manifest_path)['bundle_id'] == manifest['bundleId']
    build_browser_bundle(config)
    assert manifest_path.read_bytes() == before
    (tmp_path/'font.woff2').write_bytes(b'updated font')
    build_browser_bundle(config)
    assert json.loads(manifest_path.read_bytes())['bundleId'] != manifest['bundleId']
    assert (revision/'assets/font.woff2').read_bytes() == b'font fixture'
    current = manifest_path.parent/json.loads(manifest_path.read_text())['assetBase']
    (current/'assets/font.woff2').write_bytes(b'corruption')
    with pytest.raises(ValueError, match='integrity mismatch'):
        inspect_bundle(manifest_path)


def test_missing_css_resources_and_implicit_cdn_fail(tmp_path):
    config = project(tmp_path, 'async def main(): pass\n', 'assets=["ui.css"]\nstyles=["ui.css"]\n')
    (tmp_path/'ui.css').write_text('@font-face{src:url(missing.woff2)}')
    with pytest.raises(ValueError, match='missing local font'):
        build_browser_bundle(config)
    (tmp_path/'ui.css').write_text('@font-face{src:url(https://fonts.example/font.woff2)}')
    with pytest.raises(ValueError, match='external font'):
        build_browser_bundle(config)
    config.write_text(config.read_text()+'[tool.pytincture.browser.external-origins]\nfont=["https://fonts.example"]\n')
    result = build_browser_bundle(config)
    report = json.loads((result.parent/'compatibility.json').read_text())
    requirements = report['runtimes']['pyodide']['asset_audit']['required_origins']
    assert requirements['font'] == ['https://fonts.example']
    assert requirements['style'] == []


def test_transformations_shims_and_dynamic_imports_are_visible(tmp_path):
    config = project(tmp_path, 'import datetime\nvalue: str = "hello".title()\nasync def main(): pass\n')
    result = build_browser_bundle(config)
    report = json.loads((result.parent/'compatibility.json').read_text())
    micro = report['runtimes']['micropython']
    assert 'datetime.py' in micro['shims']
    assert any(f['rule'] == 'adapt-Call' and f['behavior_changing'] for f in micro['findings'])
    assert report['runtimes']['pyodide']['shims'] == []
    (tmp_path/'app.py').write_text('import importlib\nasync def main(): return importlib.import_module(input())\n')
    with pytest.raises(ValueError, match='computed imports'):
        build_browser_bundle(config)


def test_single_app_runtime_override_and_invalid_target_types(tmp_path):
    (tmp_path/'app.py').write_text('from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row: value:int\nasync def main(): pass\n')
    config = tmp_path/'pyproject.toml'
    config.write_text('[tool.pytincture.browser.apps.app]\nentrypoint="app:main"\nruntimes=["pyodide"]\n')
    result = build_browser_bundle(config)
    assert json.loads(result.read_text())['runtimes'] == ['pyodide']
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\nruntimes=[{}]\n')
    with pytest.raises(ValueError, match='runtimes must'):
        build_browser_bundle(config)


def test_final_inventory_includes_reports_in_aggregate_limits(monkeypatch):
    from pytincture import browser_assets
    monkeypatch.setattr(browser_assets, 'MAX_BUNDLE_BYTES', 4)
    with pytest.raises(ValueError, match='Aggregate'):
        browser_assets.seal_manifest({}, {'source':b'abc','compatibility.json':b'xyz'})


def test_binary_resources_nested_iteration_and_package_boundaries(monkeypatch):
    import sys
    from types import SimpleNamespace
    from pytincture.browser_build import TEMPLATES
    data = {'pkg/nested/icon.bin':b'\x00\xff', 'pkg/defaults.json':b'{"enabled":true}'}
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace(
        pytinctureResourcePaths=json.dumps(list(data)),
        pytinctureReadResourceBytes=lambda path:base64.b64encode(data[path]).decode()))
    namespace = {}
    exec((TEMPLATES/'resources.py.txt').read_text(),namespace)
    root = namespace['files']('pkg')
    assert [p.name for p in root.iterdir()] == ['defaults.json','nested']
    assert (root/'nested').is_dir()
    assert (root/'nested'/'icon.bin').read_bytes() == b'\x00\xff'
    assert json.loads((root/'defaults.json').read_text()) == {'enabled':True}
    with pytest.raises(ValueError, match='inside'):
        root.joinpath('../private')
    with pytest.raises(ValueError, match='UTF-8'):
        (root/'defaults.json').read_text(encoding='latin-1')


def test_source_cannot_bypass_bff_discovery_through_resource_settings(tmp_path):
    for setting in ('resources','assets'):
        config = project(tmp_path,'async def main(): pass\n',setting+'=["private.py"]\n')
        (tmp_path/'private.py').write_text('private_data = "server-only fixture"\n')
        with pytest.raises(ValueError,match='Python source or bytecode'):
            build_browser_bundle(config)


def test_extensionless_css_import_requires_style_origin_without_font_permission(tmp_path):
    config = project(tmp_path,'async def main(): pass\n',
                     'assets=["theme.css"]\nstyles=["theme.css"]\n[tool.pytincture.browser.external-origins]\nstyle=["https://styles.example"]\n')
    (tmp_path/'theme.css').write_text('@import url("https://styles.example/theme");')
    manifest = build_browser_bundle(config)
    report = json.loads((manifest.parent/'compatibility.json').read_text())
    origins = report['runtimes']['pyodide']['asset_audit']['required_origins']
    assert origins['style']==['https://styles.example'] and origins['font']==[]
