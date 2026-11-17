import asyncio
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from pytincture import PytinctureConfig
from pytincture.microapps import LiveMicroappManager, WheelArtifact, create_microapp_host
from pytincture.microapps.browser_wheel import build_browser_wheel
from pytincture.packaged_apps import build_app_manifest
from test_live_microapps import wheel


BFF = '''from pytincture.dataclass import backend_for_frontend
from private_orders.apps.private_database import database
SERVER_KEY = 'PRIVATE_MODULE_CONSTANT'
@backend_for_frontend
class Data:
    """PRIVATE_CLASS_DOCUMENTATION"""
    key = 'PRIVATE_CLASS_VALUE'
    def __init__(self, _user):
        self._key = 'PRIVATE_CONSTRUCTOR_VALUE'
    def read(self, region='PRIVATE_DEFAULT_VALUE'):
        """PRIVATE_METHOD_DOCUMENTATION"""
        return 42
'''
UI = '''from services import Data
async def mount(context):
    context.root.textContent = str(await Data().read_async())
'''


def private_app(tmp_path, *, source=UI, bff=BFF, extra=None):
    root = tmp_path / 'source'; root.mkdir()
    files = {'orders.py': 'from client import mount\n', 'client.py': source,
             'services.py': bff, 'private_database.py': 'database = "PRIVATE_DATABASE_CONTENT"\n',
             'unused.py': 'UNUSED_BACKEND_HELPER = "PRIVATE_UNUSED_CONTENT"\n', **(extra or {})}
    for name, content in files.items():
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
    build_app_manifest(root, 'orders')
    return wheel(tmp_path, 'private_orders', '0.1.0', {
        'private_orders/apps/' + path.relative_to(root).as_posix(): path.read_text()
        for path in root.rglob('*') if path.is_file()})


def build(tmp_path, backend, **kwargs):
    return build_browser_wheel(backend, tmp_path / 'public', namespace='generated_orders', entrypoint='client:mount', **kwargs)


def test_builder_strips_complete_bff_modules_and_does_not_execute_app_code(tmp_path):
    backend = private_app(tmp_path, bff=BFF + '\nraise RuntimeError("PRIVATE_BUILD_SIDE_EFFECT")\n',
                          extra={'style.css': '.orders {color: blue}', 'not-selected.txt': 'PRIVATE_RESOURCE'})
    result = build(tmp_path, backend, resources=['style.css'])
    with zipfile.ZipFile(result.wheel.path) as archive:
        content = b'\n'.join(archive.read(name) for name in archive.namelist())
        assert b'PRIVATE_' not in content
        assert b'backend_for_frontend' not in content
        assert b'read_async' in content
        assert b'context.callBff' in content
        assert 'generated_orders/private_database.py' not in archive.namelist()
        assert 'generated_orders/unused.py' not in archive.namelist()
        assert archive.read('generated_orders/style.css') == b'.orders {color: blue}'
        assert all(name.startswith('generated_orders/') or '.dist-info/' in name for name in archive.namelist())
    assert result.entrypoint == 'generated_orders:mount'
    assert build(tmp_path, backend, resources=['style.css']) == result


def test_generated_proxy_reaches_its_real_versioned_bff_and_omits_sensitive_defaults(tmp_path, monkeypatch):
    backend = private_app(tmp_path)
    browser = build(tmp_path, backend)
    imported = tmp_path / 'imported'; imported.mkdir()
    with zipfile.ZipFile(browser.wheel.path) as archive: archive.extractall(imported)
    monkeypatch.syspath_prepend(str(imported))

    async def exercise():
        manager = LiveMicroappManager(tmp_path / 'runtime', PytinctureConfig())
        await manager.start()
        try:
            release = await manager.install('orders', backend, browser, approval_id='review-generated-wheel')
            host = create_microapp_host(FastAPI(), manager, authorize=lambda scope, app: True)
            calls = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                async def call(module, cls, method, args, kwargs):
                    calls.append((module, cls, method, args, kwargs))
                    response = await client.post(release['bff_base'] + f'/classcall/{module}/{cls}/{method}',
                                                 json={'args': args, 'kwargs': kwargs})
                    assert response.status_code == 200, response.text
                    return response.json()
                context = SimpleNamespace(root=SimpleNamespace(textContent=''), callBff=call)
                entry = importlib.import_module('generated_orders')
                await entry.mount(context)
                assert context.root.textContent == '42'
                assert calls == [('services.py', 'Data', 'read', [], {})]
                await entry.unmount(context)
                proxy = importlib.import_module('generated_orders.services').Data()
                with pytest.raises(RuntimeError, match='Mount the microapp'):
                    await proxy.read_async()
        finally:
            await manager.close()
            for name in list(sys.modules):
                if name == 'generated_orders' or name.startswith('generated_orders.'):
                    del sys.modules[name]
    asyncio.run(exercise())


def test_generated_proxy_supports_positional_envelopes(tmp_path, monkeypatch):
    source = UI.replace('read_async()', 'read_async(1, 2, three=3)')
    bff = BFF[:BFF.index('    def read(')] + '    def read(self, first, /, *args, **kwargs): return first\n'
    backend = private_app(tmp_path, source=source, bff=bff)
    browser = build(tmp_path, backend)
    imported = tmp_path / 'imported'; imported.mkdir()
    with zipfile.ZipFile(browser.wheel.path) as archive: archive.extractall(imported)
    monkeypatch.syspath_prepend(str(imported))
    async def exercise():
        calls = []
        async def call(*args): calls.append(args); return 7
        entry = importlib.import_module('generated_orders')
        try:
            context = SimpleNamespace(root=SimpleNamespace(), callBff=call)
            await entry.mount(context)
            assert calls == [('services.py', 'Data', 'read', [1, 2], {'three': 3})]
            assert context.root.textContent == '7'
        finally:
            await entry.unmount(context)
            for name in list(sys.modules):
                if name == 'generated_orders' or name.startswith('generated_orders.'):
                    del sys.modules[name]
    asyncio.run(exercise())


@pytest.mark.parametrize('source', [
    'import api.services\nasync def mount(context):\n    return await api.services.Data().read_async()\n',
    'from api import services\nasync def mount(context):\n    return await services.Data().read_async()\n',
])
def test_nested_bff_modules_drop_backend_initializers_and_their_private_imports(tmp_path, source):
    backend = private_app(tmp_path, source='from ui_helpers import title\n' + source, extra={
        'api/services.py': BFF,
        'api/__init__.py': 'from . import private_helper\nimport ui_helpers\n',
        'api/private_helper.py': 'from . import private_settings\n',
        'api/private_settings.py': 'SETTING = "PRIVATE_PARENT_SETTING"\n',
        'ui_helpers.py': 'title = "Orders"\n',
    })
    browser = build(tmp_path, backend)
    with zipfile.ZipFile(browser.wheel.path) as archive:
        assert archive.read('generated_orders/api/__init__.py').strip() == b''
        assert b'generated_orders.api' in archive.read('generated_orders/client.py')
        assert 'generated_orders/api/private_helper.py' not in archive.namelist()
        assert 'generated_orders/api/private_settings.py' not in archive.namelist()
        assert archive.read('generated_orders/ui_helpers.py').strip() == b"title = 'Orders'"
        assert b'PRIVATE_' not in b'\n'.join(archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize('kwargs, match', [
    ({'files': ['services.py']}, 'contains backend code'),
    ({'resources': ['services.py']}, 'Declare Python files'),
    ({'resources': ['../outside.txt']}, 'relative public path'),
])
def test_builder_refuses_public_declarations_that_cross_backend_boundary(tmp_path, kwargs, match):
    backend = private_app(tmp_path)
    with pytest.raises(ValueError, match=match): build(tmp_path, backend, **kwargs)
    assert not (tmp_path / 'public').exists()


def test_builder_refuses_unsupported_transport_before_publishing(tmp_path):
    bff = BFF.replace('import backend_for_frontend', 'import backend_for_frontend, bff_stream')
    bff = bff.replace('    def read(', '    @bff_stream\n    def read(')
    backend = private_app(tmp_path, bff=bff)
    with pytest.raises(ValueError, match='non-streaming POST'): build(tmp_path, backend)
    assert not (tmp_path / 'public').exists()


def test_cli_emits_only_the_sanitized_artifact_identity(tmp_path):
    backend = private_app(tmp_path)
    completed = subprocess.run([sys.executable, '-m', 'pytincture.microapps.browser_wheel',
        '--backend-wheel', str(backend.path), '--backend-sha256', backend.sha256,
        '--namespace', 'generated_orders', '--entrypoint', 'client:mount',
        '--output-directory', str(tmp_path / 'public')], capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    assert Path(result['wheel']).is_file()
    assert result['entrypoint'] == 'generated_orders:mount'
    assert 'PRIVATE_' not in completed.stdout


def test_generator_verifies_approved_input_hash(tmp_path):
    backend = private_app(tmp_path)
    with pytest.raises(ValueError, match='SHA-256'):
        build(tmp_path, WheelArtifact(backend.path, '0' * 64))
    assert not (tmp_path / 'public').exists()


def test_generated_wheel_version_is_immutable(tmp_path):
    backend = private_app(tmp_path)
    first = build(tmp_path, backend)
    original = Path(first.wheel.path).read_bytes()
    other = tmp_path / 'other'; other.mkdir()
    changed = private_app(other, source=UI + '\nchanged_ui = True\n')
    with pytest.raises(ValueError, match='already exists with different bytes'):
        build(tmp_path, changed)
    assert Path(first.wheel.path).read_bytes() == original


@pytest.mark.parametrize('source, match', [
    ('async def mount(): pass\n', 'one context argument'),
    ('async def mount(context):\n    __import__("services")\n', 'static imports'),
])
def test_unsupported_ui_shapes_fail_before_publication(tmp_path, source, match):
    backend = private_app(tmp_path, source=source)
    with pytest.raises(ValueError, match=match): build(tmp_path, backend)
    assert not (tmp_path / 'public').exists()
