import asyncio
import copy
import hashlib
import json
from pathlib import Path
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from pytincture import PytinctureConfig
from pytincture.microapps import EnvironmentMicroappManager, DirectoryObjectStore, empty_manifest, create_microapp_host
from pytincture.microapps.environment import SupersededBuild
from pytincture.microapps.environment_manifest import encode, decode, selection_id
from pytincture.microapps.object_store import ObjectConflict
from test_live_microapps import artifacts, wheel


class CountingStore(DirectoryObjectStore):
    def __init__(self, root):
        super().__init__(root); self.writes = []; self.reads = []
    async def get(self, key, **kwargs):
        self.reads.append(key)
        return await super().get(key, **kwargs)
    async def put(self, key, data, **kwargs):
        result = await super().put(key, data, **kwargs)
        self.writes.append(key)
        return result


async def app_package(tmp_path, objects, app_id='orders', version='0.1.0', value=42):
    source = tmp_path / (app_id + version); source.mkdir()
    backend, _ = artifacts(source, version, value)
    browser = wheel(source, app_id + '_ui', version, {app_id + '_ui/__init__.py':
        "async def mount(context):\n    value = await context.callBff('services.py', 'Data', 'read')\n    context.root.textContent = str(value)\n"})
    result = {'installed': True, 'version': version, 'approval_id': 'review-' + version,
              'backend': {'key': f'wheels/{app_id}/{Path(backend.path).name}', 'sha256': backend.sha256},
              'browser': {'artifact': {'key': f'wheels/{app_id}/{Path(browser.path).name}', 'sha256': browser.sha256},
                          'entrypoint': app_id + '_ui:mount'}, 'dependencies': []}
    for artifact, record in [(backend, result['backend']), (browser, result['browser']['artifact'])]:
        await objects.put(record['key'], Path(artifact.path).read_bytes(), expected=None)
    return result


async def eventually(predicate, timeout=10):
    async with asyncio.timeout(timeout):
        while not await predicate(): await asyncio.sleep(0.05)


def manager(tmp_path, objects, suffix='one', **kwargs):
    return EnvironmentMicroappManager(tmp_path / ('runtime-' + suffix), PytinctureConfig(),
        objects=objects, environment='dev-123', poll_seconds=0.1, **kwargs)


def test_startup_builds_only_installed_apps_embeds_inventory_and_reuses_matching_bundle(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        document = empty_manifest('dev-123')
        document['apps']['orders'] = await app_package(tmp_path, objects)
        document['apps']['unused'] = {'installed': False, 'version': '99.0', 'backend': {'key': 'not-present.whl'}}
        first = manager(tmp_path, objects)
        await first.initialize(document)
        await first.start()
        try:
            current, etag = await first.read_manifest()
            assert current['active']['selection'] == selection_id(document)
            assert [a['id'] for a in first.catalog()['apps']] == ['orders']
            with zipfile.ZipFile(first._bundle_file) as archive:
                inventory = json.loads(archive.read('pytincture-environment.json'))
                assert inventory['apps']['orders']['version'] == '0.1.0'
                assert 'orders_ui/__init__.py' in archive.namelist()
                assert not any('services.py' in n for n in archive.namelist())
            writes = len([key for key in objects.writes if key.startswith('bundles/')])
        finally: await first.close()
        second = manager(tmp_path, objects)
        await second.start()
        try:
            assert len([key for key in objects.writes if key.startswith('bundles/')]) == writes == 1
            assert (await second.read_manifest())[1] == etag
            assert 'not-present.whl' not in objects.reads
        finally: await second.close()
    asyncio.run(exercise())


def test_two_ecs_replicas_share_one_build_and_both_serve_the_same_release(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        first, second = manager(tmp_path, objects, 'a'), manager(tmp_path, objects, 'b')
        await first.initialize(doc)
        try:
            await asyncio.gather(first.start(), second.start())
            assert len([key for key in objects.writes if key.startswith('bundles/')]) == 1
            for replica in [first, second]:
                host = create_microapp_host(FastAPI(), replica, authorize=lambda scope, app: True)
                item = replica.catalog()['apps'][0]
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                    result = await client.post(item['bff_base'] + '/classcall/services.py/Data/read', json={})
                    assert result.status_code == 200, result.text
                    assert result.json() == 42
        finally: await asyncio.gather(first.close(), second.close())
    asyncio.run(exercise())


def test_live_manifest_update_builds_new_bundle_old_routes_and_revision_header_survive(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        first_spec = await app_package(tmp_path, objects)
        next_spec = await app_package(tmp_path, objects, version='0.2.0', value=99)
        current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = first_spec
        await current.initialize(doc); await current.start()
        old_app = current.catalog()['apps'][0]; old_snapshot = current._bundle['selection']
        try:
            accepted = await current.request_install('orders', next_spec, expected_revision=0)
            assert accepted['state'] == 'accepted' and accepted['revision'] == 1
            async def ready(): return current._bundle['selection'] != old_snapshot
            await eventually(ready)
            host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app: True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                old_bundle = await client.get('/_microapps/bundles/' + old_snapshot + '/environment.zip')
                assert old_bundle.status_code == 200
                assert old_bundle.headers['etag'] != '"' + current._bundle['sha256'] + '"'
                for app, value in [(old_app, 42), (current.catalog()['apps'][0], 99)]:
                    response = await client.post(app['bff_base'] + '/classcall/services.py/Data/read', json={})
                    assert response.json() == value
                    assert response.headers['x-pytincture-bundle'] == current._bundle['selection']
            new_replica = manager(tmp_path, objects, 'new')
            await new_replica.start()
            try:
                host = create_microapp_host(FastAPI(), new_replica, authorize=lambda scope, app: True)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                    response = await client.post(old_app['bff_base'] + '/classcall/services.py/Data/read', json={})
                    assert response.status_code == 200 and response.json() == 42
            finally: await new_replica.close()
        finally: await current.close()
    asyncio.run(exercise())


def test_failed_background_build_keeps_last_good_bundle_and_blocks_bad_startup(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await current.initialize(doc); await current.start()
        old = current._bundle['selection']
        invalid = copy.deepcopy(doc['apps']['orders']); invalid['browser']['artifact']['sha256'] = '0' * 64
        try:
            await current.request_install('orders', invalid)
            async def failed(): return (await current.read_manifest())[0]['error'] is not None
            await eventually(failed)
            assert current._bundle['selection'] == old
            assert (await current.read_manifest())[0]['active']['selection'] == old
        finally: await current.close()
        restarted = manager(tmp_path, objects, 'broken')
        with pytest.raises(ValueError, match='SHA-256'): await restarted.start()
        await restarted.close()
    asyncio.run(exercise())


def test_api_acknowledges_durable_manifest_change_and_requires_approval(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        specification = await app_package(tmp_path, objects)
        await current.initialize(empty_manifest('dev-123')); await current.start()
        host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app: True,
            authorize_install=lambda scope, command: specification if command['version'] == '0.1.0' else None)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                command = {'app_id': 'orders', 'installed': True, 'version': '0.1.0', 'expected_revision': 0}
                headers = {'X-Pytincture-Install': '1', 'Origin': 'http://127.0.0.1'}
                response = await client.post('/_microapps/installations', json=command, headers=headers)
                assert response.status_code == 202, response.text
                assert (await current.read_manifest())[0]['apps']['orders']['installed']
                assert (await client.post('/_microapps/installations', json=command, headers={**headers, 'Origin': 'https://evil.test'})).status_code == 403
                assert (await client.post('/_microapps/installations', json={**command, 'version': '0.2.0'}, headers=headers)).status_code == 403
                assert (await client.post('/_microapps/installations', json=command, headers=headers)).status_code == 409
        finally: await current.close()
    asyncio.run(exercise())


def test_changes_during_build_do_not_activate_a_stale_snapshot(tmp_path, monkeypatch):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        reports = await app_package(tmp_path, objects, 'reports')
        await current.initialize(doc)
        entered, release = asyncio.Event(), asyncio.Event()
        prepare = current._prepare; calls = 0
        async def delayed(lock):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set(); await release.wait()
            return await prepare(lock)
        monkeypatch.setattr(current, '_prepare', delayed)
        starting = asyncio.create_task(current.start())
        try:
            await entered.wait()
            await current.request_install('reports', reports)
            release.set(); await starting
            assert set(current._bundle['inventory']['releases']) == {'orders', 'reports'}
            assert (await current.read_manifest())[0]['active']['revision'] == 1
        finally:
            release.set(); await current.close()
    asyncio.run(exercise())


def test_filename_version_and_metadata_are_validated(tmp_path):
    from pytincture.microapps.environment_manifest import validate_manifest
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); doc = empty_manifest('dev-123')
        doc['apps']['orders'] = await app_package(tmp_path, objects)
        doc['apps']['orders']['version'] = '9.0'
        with pytest.raises(ValueError, match='filename version'): validate_manifest(doc)
    asyncio.run(exercise())


def test_expired_or_stolen_build_lease_cannot_activate(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); await current.initialize(doc)
        doc, etag = await current.read_manifest()
        import time
        doc['build_epoch'] = 2
        doc['build'] = {'token': 'b' * 32, 'selection': selection_id(doc), 'expires_at': time.time() + 60, 'epoch': 2}
        await objects.put('environment.json', encode(doc), expected=etag)
        with pytest.raises(SupersededBuild): await current._publish('a' * 32, {'selection': selection_id(doc)})
        assert (await current.read_manifest())[0]['active'] is None
    asyncio.run(exercise())


def test_unchanged_versions_and_hashes_do_not_rebuild_for_approval_metadata(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await current.initialize(doc); await current.start()
        try:
            old_selection = current._bundle['selection']
            updated = {**doc['apps']['orders'], 'approval_id': 'reapproved-same-artifact'}
            await current.request_install('orders', updated)
            await current.reconcile()
            assert current._bundle['selection'] == old_selection
            assert len([key for key in objects.writes if key.startswith('bundles/')]) == 1
        finally: await current.close()
    asyncio.run(exercise())


def test_uninstall_removes_app_from_bundle_and_denies_retained_bff(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await current.initialize(doc); await current.start()
        old_app = current.catalog()['apps'][0]
        old_bundle = current._bundle['selection']
        try:
            await current.request_install('orders', {'installed': False})
            await current.reconcile()
            assert current.catalog()['apps'] == []
            with zipfile.ZipFile(current._bundle_file) as archive:
                assert not any(name.startswith('orders_ui/') for name in archive.namelist())
            host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app: True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                assert (await client.post(old_app['bff_base'] + '/classcall/services.py/Data/read', json={})).status_code == 404
                assert (await client.get('/_microapps/bundles/' + old_bundle + '/environment.zip')).status_code == 404
        finally: await current.close()
    asyncio.run(exercise())


def test_aggregate_bundle_is_not_disclosed_to_partially_authorized_users(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        doc = empty_manifest('dev-123')
        doc['apps']['orders'] = await app_package(tmp_path, objects)
        doc['apps']['reports'] = await app_package(tmp_path, objects, 'reports')
        await current.initialize(doc); await current.start()
        try:
            host = create_microapp_host(FastAPI(), current, authorize=lambda scope, app: app != 'reports')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                data = (await client.get('/_microapps/catalog')).json()
                assert 'snapshot' not in data
                assert [app['id'] for app in data['apps']] == ['orders']
                url = '/_microapps/bundles/' + current._bundle['selection'] + '/environment.zip'
                assert (await client.get(url)).status_code == 403
        finally: await current.close()
    asyncio.run(exercise())


def test_missing_remote_aggregate_is_rebuilt_on_fresh_replica(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); first = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await first.initialize(doc); await first.start()
        active = copy.deepcopy(first._bundle); await first.close()
        objects._path(active['key']).unlink()
        second = manager(tmp_path, objects, 'two')
        try:
            await second.start()
            assert second._bundle['sha256'] == active['sha256']
            assert second._bundle['selection'] == active['selection']
            assert objects._path(active['key']).is_file()
        finally: await second.close()
    asyncio.run(exercise())


def test_direct_s3_delivery_exposes_only_approved_browser_artifacts(tmp_path):
    class SigningStore(CountingStore):
        def signed_url(self, key):
            return 'https://example-bucket.s3.us-east-1.amazonaws.com/' + key + '?signature=test'
    async def exercise():
        objects = SigningStore(tmp_path / 'objects')
        current = manager(tmp_path, objects, browser_delivery='s3',
                          asset_origins=('https://example-bucket.s3.us-east-1.amazonaws.com',))
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await current.initialize(doc); await current.start()
        try:
            catalog = current.catalog()
            assert catalog['snapshot']['url'].startswith('https://example-bucket.s3.')
            assert catalog['apps'][0]['browser']['url'].endswith('?signature=test')
            assert doc['apps']['orders']['backend']['key'] not in json.dumps(catalog)
        finally: await current.close()
    asyncio.run(exercise())


def test_standard_fastapi_page_enables_environment_bootstrap_only_when_wrapped(tmp_path):
    from pytincture import create_app
    async def exercise():
        source = tmp_path / 'source'; source.mkdir()
        (source / 'shell.py').write_text('def main(): pass\n')
        config = PytinctureConfig(modules_path=str(source))
        base = create_app(config)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=base), base_url='http://127.0.0.1') as client:
            response = await client.get('/shell/')
            assert response.status_code == 200
            assert 'environmentCatalogUrl: null' in response.text
        objects = CountingStore(tmp_path / 'objects')
        current = EnvironmentMicroappManager(tmp_path / 'runtime', config, objects=objects, environment='dev-123')
        await current.initialize(empty_manifest('dev-123'))
        host = create_microapp_host(base, current, authorize=lambda scope, app: True)
        await current.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                response = await client.get('/shell/')
                assert response.status_code == 200
                assert 'environmentCatalogUrl: "/_microapps/catalog"' in response.text
                assert '***ENVIRONMENT_' not in response.text
        finally: await current.close()
    asyncio.run(exercise())


def test_cached_wheel_with_mismatched_metadata_is_rejected_on_every_attempt(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); current = manager(tmp_path, objects)
        artifact = wheel(tmp_path, 'orders_ui', '0.1.0', {'orders_ui.py': 'def mount(context): pass\n'})
        record = {'key': 'wheels/orders_ui-0.2.0-py3-none-any.whl', 'sha256': artifact.sha256}
        await objects.put(record['key'], Path(artifact.path).read_bytes())
        for _ in range(2):
            with pytest.raises(ValueError, match='metadata disagree'):
                await current._cached(record, browser=True)
    asyncio.run(exercise())


def test_concurrent_accepted_installs_survive_before_any_builder_starts(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); controller = manager(tmp_path, objects)
        orders = await app_package(tmp_path, objects)
        reports = await app_package(tmp_path, objects, 'reports')
        await controller.initialize(empty_manifest('dev-123'))
        results = await asyncio.gather(controller.request_install('orders', orders),
                                       controller.request_install('reports', reports))
        assert sorted(result['revision'] for result in results) == [1, 2]
        document, _ = await controller.read_manifest()
        assert document['active'] is None and set(document['apps']) == {'orders', 'reports'}
        restarted = manager(tmp_path, objects, 'restarted')
        try:
            await restarted.start()
            assert set(restarted._bundle['inventory']['releases']) == {'orders', 'reports'}
        finally: await restarted.close()
    asyncio.run(exercise())
