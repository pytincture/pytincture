"""Private BFF code must never become an advertised browser artifact."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from pytincture import PytinctureConfig
from pytincture.microapps import BrowserArtifact, LiveMicroappManager, create_microapp_host, empty_manifest
from pytincture.microapps.browser_safety import browser_archive_files
from pytincture.microapps.builder import build_environment_bundle
from pytincture.microapps.snapshot import build_snapshot
from test_environment_microapps import CountingStore, app_package, eventually, manager
from test_live_microapps import artifacts, wheel


PRIVATE_SOURCE = (
    'from pytincture.dataclass import backend_for_frontend\n'
    '@backend_for_frontend\n'
    'class Data:\n'
    '    def __init__(self, _user): pass\n'
    '    def read(self): return "SERVER_ONLY_TEST_MARKER"\n'
)
UI_SOURCE = (
    'async def mount(context):\n'
    '    value = await context.callBff("services.py", "Data", "read")\n'
    '    context.root.textContent = str(value)\n'
)


@pytest.mark.parametrize('name, source', [
    ('probe_ui/services.py', PRIVATE_SOURCE),
    ('probe_ui/services.py.txt', PRIVATE_SOURCE),
    ('probe_ui/services.py', PRIVATE_SOURCE.split('\n', 1)[1]),
    ('probe_ui/services.py', PRIVATE_SOURCE.replace('import backend_for_frontend', 'import backend_for_frontend as export')
                                          .replace('@backend_for_frontend', '@export')),
    ('probe_ui/services.py', 'import pytincture.dataclass as pt\n@pt.backend_for_frontend\nclass Data: pass\n'),
    ('probe_ui/services.py', 'from pytincture import backend_for_frontend\n'),
    ('probe_ui/services.py', 'import pytincture.backend.app\n'),
    ('probe_ui/services.py', 'def broken(\n'),
    ('probe_ui/private.pyc', 'opaque bytecode'),
    ('probe_ui/original.whl', 'nested private wheel'),
    ('probe_ui/original.zip', 'nested private sources'),
    ('probe_ui/.env', 'EXAMPLE=placeholder'),
    ('probe_ui/secrets.json', '{}'),
    ('probe_ui/pytincture-app.json', '{}'),
])
def test_browser_wheels_reject_server_implementation_and_uninspectable_code(tmp_path, name, source):
    artifact = wheel(tmp_path, 'probe_ui', '0.1.0', {name: source})
    with pytest.raises(ValueError):
        browser_archive_files(artifact.path)


def test_live_install_rejects_backend_source_before_workers_or_publication(tmp_path, monkeypatch):
    backend, _ = artifacts(tmp_path)
    unsafe = wheel(tmp_path, 'unsafe_ui', '0.1.0', {
        'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/services.py': PRIVATE_SOURCE})

    async def no_worker(*args):
        raise AssertionError('Unsafe browser code must be rejected before worker startup')

    async def exercise():
        current = LiveMicroappManager(tmp_path / 'runtime', PytinctureConfig())
        monkeypatch.setattr(current, '_start_worker', no_worker)
        await current.start()
        try:
            with pytest.raises(ValueError, match='backend BFF implementation'):
                await current.install('orders', backend, BrowserArtifact(unsafe, 'unsafe_ui:mount'), approval_id='review')
            assert current.catalog()['apps'] == []
            assert not current._releases
            assert not list(current.store.rglob('unsafe_ui-*.whl'))
        finally:
            await current.close()
    asyncio.run(exercise())


def test_live_install_rejects_private_class_copied_without_its_decorator(tmp_path):
    backend, _ = artifacts(tmp_path)
    copied = (tmp_path / 'source-0.1.0/services.py').read_text().split('class Data:', 1)[1]
    unsafe = wheel(tmp_path, 'unsafe_ui', '0.1.0', {
        'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/copied.py': 'class Data:' + copied})

    async def exercise():
        current = LiveMicroappManager(tmp_path / 'runtime', PytinctureConfig())
        await current.start()
        try:
            with pytest.raises(ValueError, match='copied private BFF implementation'):
                await current.install('orders', backend, BrowserArtifact(unsafe, 'unsafe_ui:mount'), approval_id='review')
            assert current.catalog()['apps'] == []
        finally:
            await current.close()
    asyncio.run(exercise())


def test_restart_rejects_unsafe_browser_wheel_from_an_older_controller(tmp_path, monkeypatch):
    backend, _ = artifacts(tmp_path)
    unsafe = wheel(tmp_path, 'unsafe_ui', '0.1.0', {
        'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/services.py': PRIVATE_SOURCE})

    async def exercise():
        current = LiveMicroappManager(tmp_path / 'runtime', PytinctureConfig())
        # Simulate a pre-fix controller that accepted the approved but unsafe wheel.
        with monkeypatch.context() as legacy:
            legacy.setattr('pytincture.microapps.manager.validate_browser_archive', lambda *a, **kw: None)
            await current.start()
            try:
                await current.install('orders', backend, BrowserArtifact(unsafe, 'unsafe_ui:mount'), approval_id='review')
            finally:
                await current.close()
        restored = LiveMicroappManager(current.store, PytinctureConfig())
        try:
            with pytest.raises(ValueError, match='backend BFF implementation'):
                await restored.start()
            assert restored.catalog()['apps'] == []
            assert not restored._workers
        finally:
            await restored.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('browser_delivery', ['host', 's3'])
def test_s3_selected_unsafe_wheel_is_never_published_or_signed(tmp_path, browser_delivery):
    signed = []

    class SigningStore(CountingStore):
        def signed_url(self, key):
            signed.append(key)
            return 'https://example-bucket.s3.us-east-1.amazonaws.com/' + key

    async def exercise():
        objects = SigningStore(tmp_path / 'objects')
        spec = await app_package(tmp_path, objects)
        unsafe = wheel(tmp_path, 'unsafe_ui', '0.1.0', {
            'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/services.py': PRIVATE_SOURCE})
        record = {'key': 'wheels/' + Path(unsafe.path).name, 'sha256': unsafe.sha256}
        await objects.put(record['key'], Path(unsafe.path).read_bytes(), expected=None)
        spec['browser'] = {'artifact': record, 'entrypoint': 'unsafe_ui:mount'}
        document = empty_manifest('dev-123'); document['apps']['orders'] = spec
        current = manager(tmp_path, objects, browser_delivery=browser_delivery,
                          asset_origins=('https://example-bucket.s3.us-east-1.amazonaws.com',))
        await current.initialize(document)
        try:
            with pytest.raises(ValueError, match='backend BFF implementation'):
                await current.start()
            assert current.catalog()['apps'] == []
            assert (await current.read_manifest())[0]['active'] is None
            assert not [key for key in objects.writes if key.startswith('bundles/')]
            assert signed == []
        finally:
            await current.close()
    asyncio.run(exercise())


def test_base_ui_wheel_cannot_publish_a_backend_implementation(tmp_path):
    unsafe = wheel(tmp_path, 'unsafe_base', '0.1.0', {'shell.py': PRIVATE_SOURCE})
    with pytest.raises(ValueError, match='backend BFF implementation'):
        build_snapshot(tmp_path / 'environment.zip',
            {'selection': 'test', 'runtime': 'pyodide', 'base': None}, {}, {}, Path(unsafe.path))
    assert not (tmp_path / 'environment.zip').exists()


def test_cached_aggregate_rechecks_private_copies_accepted_by_an_older_builder(tmp_path, monkeypatch):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        spec = await app_package(tmp_path, objects)
        copied = (tmp_path / 'orders0.1.0/source-0.1.0/services.py').read_text().split('class Data:', 1)[1]
        unsafe = wheel(tmp_path, 'unsafe_ui', '0.1.0', {
            'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/copied.py': 'class Data:' + copied})
        record = {'key': 'wheels/' + Path(unsafe.path).name, 'sha256': unsafe.sha256}
        await objects.put(record['key'], Path(unsafe.path).read_bytes(), expected=None)
        spec['browser'] = {'artifact': record, 'entrypoint': 'unsafe_ui:mount'}
        document = empty_manifest('dev-123'); document['apps']['orders'] = spec
        current = manager(tmp_path, objects)
        await current.initialize(document)
        with monkeypatch.context() as legacy:
            legacy.setattr('pytincture.microapps.manager.validate_browser_archive', lambda *a, **kw: None)
            await build_environment_bundle(objects, environment='dev-123')
        with pytest.raises(ValueError, match='copied private BFF implementation'):
            await build_environment_bundle(objects, environment='dev-123')
        try:
            with pytest.raises(ValueError, match='copied private BFF implementation'):
                await current.start()
            assert current._bundle is None
        finally:
            await current.close()
    asyncio.run(exercise())


def test_failed_live_safety_check_preserves_the_previous_browser_and_bff_release(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        document = empty_manifest('dev-123'); document['apps']['orders'] = await app_package(tmp_path, objects)
        current = manager(tmp_path, objects)
        await current.initialize(document); await current.start()
        try:
            old = current.catalog()['apps'][0]
            active = current._bundle['selection']
            replacement = await app_package(tmp_path, objects, version='0.2.0', value=99)
            unsafe = wheel(tmp_path, 'unsafe_ui', '0.2.0', {
                'unsafe_ui/__init__.py': UI_SOURCE, 'unsafe_ui/services.py': PRIVATE_SOURCE})
            record = {'key': 'wheels/' + Path(unsafe.path).name, 'sha256': unsafe.sha256}
            await objects.put(record['key'], Path(unsafe.path).read_bytes(), expected=None)
            replacement['browser'] = {'artifact': record, 'entrypoint': 'unsafe_ui:mount'}
            await current.request_install('orders', replacement)
            async def rejected(): return current._last_error == 'ValueError'
            await eventually(rejected)
            assert current._bundle['selection'] == active
            assert current.catalog()['apps'][0]['release'] == old['release']
            host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app_id: True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                response = await client.post(old['bff_base'] + '/classcall/services.py/Data/read', json={})
                assert response.status_code == 200 and response.json() == 42
        finally:
            await current.close()
    asyncio.run(exercise())


def test_prebuilt_aggregate_cannot_add_files_outside_the_browser_wheel_lock(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        document = empty_manifest('dev-123'); document['apps']['orders'] = await app_package(tmp_path, objects)
        current = manager(tmp_path, objects)
        await current.initialize(document)
        built = await build_environment_bundle(objects, environment='dev-123')
        with zipfile.ZipFile(objects._path(built['key'])) as original:
            files = {name: original.read(name) for name in original.namelist()}
        # No decorator/import marker: even apparently ordinary extra source must
        # be rejected if it did not come from an approved browser wheel.
        files['hidden_backend.py'] = b'def private_query(): return "SERVER_ONLY_TEST_MARKER"\n'
        bad_path = tmp_path / 'bad.zip'
        with zipfile.ZipFile(bad_path, 'w') as archive:
            for name, data in files.items(): archive.writestr(name, data)
        data = bad_path.read_bytes()
        doc, etag = await current.read_manifest()
        active = copy.deepcopy(doc['active'])
        active.update(key='bundles/unapproved/environment.zip', sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        await objects.put(active['key'], data, expected=None)
        doc, etag = await current.read_manifest(); doc['active'] = active
        await objects.put('environment.json', json.dumps(doc).encode(), expected=etag)
        try:
            with pytest.raises(ValueError, match='contents differ from approved browser wheels'):
                await current.start()
            assert current._bundle is None
            with pytest.raises(ValueError, match='contents differ from approved browser wheels'):
                await build_environment_bundle(objects, environment='dev-123')
        finally:
            await current.close()
    asyncio.run(exercise())


def test_browser_delivery_excludes_private_code_and_preserves_real_bff_calls(tmp_path, monkeypatch):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        document = empty_manifest('dev-123'); document['apps']['orders'] = await app_package(tmp_path, objects)
        current = manager(tmp_path, objects)
        await current.initialize(document)
        await current.start()
        try:
            host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app_id: True)
            item = current.catalog()['apps'][0]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                browser = await client.get(item['browser']['url'])
                bundle = await client.get(current.catalog()['snapshot']['url'])
                assert browser.status_code == bundle.status_code == 200
                for payload in (browser.content, bundle.content):
                    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                        content = b'\n'.join(archive.read(name) for name in archive.namelist())
                        assert b'context.callBff' in content
                        assert b'backend_for_frontend' not in content
                        assert 'services.py' not in archive.namelist()
                response = await client.post(item['bff_base'] + '/classcall/services.py/Data/read', json={})
                assert response.status_code == 200 and response.json() == 42

                def no_rescan(*args):
                    raise AssertionError('An unchanged verified bundle should not rescan all its wheels')
                monkeypatch.setattr('pytincture.microapps.environment.verify_snapshot_files', no_rescan)
                assert (await client.get(current.catalog()['snapshot']['url'])).status_code == 200
        finally:
            await current.close()
    asyncio.run(exercise())
