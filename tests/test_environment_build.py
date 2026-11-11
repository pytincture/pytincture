import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import httpx
from fastapi import FastAPI
import pytest

from pytincture.microapps import empty_manifest, create_microapp_host, EnvironmentMicroappManager
from pytincture.microapps.builder import build_environment_bundle
from test_environment_microapps import CountingStore, app_package, manager, eventually


def test_build_job_publishes_before_web_start_and_reuses_matching_bundle(tmp_path, monkeypatch):
    async def no_worker(*args, **kwargs):
        raise AssertionError('A build job must not start BFF workers')
    monkeypatch.setattr(EnvironmentMicroappManager, '_start_worker', no_worker)

    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        doc['apps']['unused'] = {'installed': False}
        controller = manager(tmp_path, objects)
        await controller.initialize(doc)
        first = await build_environment_bundle(objects, environment='dev-123')
        document, etag = await controller.read_manifest()
        assert first['state'] == 'ready' and first['selection'] == document['active']['selection']
        with zipfile.ZipFile(objects._path(first['key'])) as archive:
            inventory = json.loads(archive.read('pytincture-environment.json'))
            assert set(inventory['apps']) == {'orders'}
            assert not any('services.py' in name for name in archive.namelist())

        async def no_rebuild(*args): raise AssertionError('Matching selections must be reused')
        monkeypatch.setattr(EnvironmentMicroappManager, '_prepare_build', no_rebuild)
        second = await build_environment_bundle(objects, environment='dev-123')
        assert first == second
        assert (await controller.read_manifest())[1] == etag
        assert len([key for key in objects.writes if key.startswith('bundles/')]) == 1
    asyncio.run(exercise())


def test_fresh_web_replica_uses_prebuilt_s3_bundle_and_serves_bff(tmp_path, monkeypatch):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await manager(tmp_path, objects).initialize(doc)
        built = await build_environment_bundle(objects, environment='dev-123')

        def no_build(*args, **kwargs): raise AssertionError('Web replica must not build browser bundles')
        monkeypatch.setattr('pytincture.microapps.environment.build_snapshot', no_build)
        web = manager(tmp_path, objects, 'web', build_bundles=False)
        try:
            await web.start()
            assert web._bundle['key'] == built['key']
            app = web.catalog()['apps'][0]
            host = create_microapp_host(FastAPI(), web, authorize=lambda scope, app: True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                result = await client.post(app['bff_base'] + '/classcall/services.py/Data/read', json={})
                assert result.status_code == 200 and result.json() == 42
                response = await client.get(web.catalog()['snapshot']['url'])
                assert response.status_code == 200 and len(response.content) == built['bytes']
            assert len([key for key in objects.writes if key.startswith('bundles/')]) == 1
        finally: await web.close()
    asyncio.run(exercise())


def test_web_mode_waits_for_external_builder_without_writing_a_lease(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        web = manager(tmp_path, objects, build_bundles=False, startup_timeout=0.05)
        await web.initialize(empty_manifest('dev-123'))
        with pytest.raises(TimeoutError): await web.start()
        document, _ = await web.read_manifest()
        assert document['active'] is None and document['build'] is None and document['build_epoch'] == 0
        assert len(objects.writes) == 1
        await web.close()
    asyncio.run(exercise())


def test_running_web_replica_keeps_old_bundle_until_external_job_finishes(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        first = await app_package(tmp_path, objects)
        second = await app_package(tmp_path, objects, version='0.2.0', value=99)
        web = manager(tmp_path, objects, 'web', build_bundles=False)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = first
        await web.initialize(doc)
        await build_environment_bundle(objects, environment='dev-123')
        await web.start()
        old_selection = web._bundle['selection']
        try:
            await web.request_install('orders', second)
            await asyncio.sleep(0.15)
            pending, _ = await web.read_manifest()
            assert pending['active']['selection'] == old_selection and pending['build'] is None
            assert web._bundle['selection'] == old_selection
            result = await build_environment_bundle(objects, environment='dev-123')
            async def adopted(): return web._bundle['selection'] == result['selection']
            await eventually(adopted)
            assert web.catalog()['apps'][0]['version'] == '0.2.0'
        finally: await web.close()
    asyncio.run(exercise())


def test_concurrent_build_jobs_share_the_same_s3_fenced_builder(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await manager(tmp_path, objects).initialize(doc)
        first, second = await asyncio.gather(*[
            build_environment_bundle(objects, environment='dev-123', poll_seconds=0.05,
                                     cache_directory=tmp_path / name)
            for name in ('build-one', 'build-two')])
        assert first == second
        assert len([key for key in objects.writes if key.startswith('bundles/')]) == 1
    asyncio.run(exercise())


def test_published_bundle_does_not_replace_local_catalog_until_bff_is_ready(tmp_path, monkeypatch):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects')
        first = await app_package(tmp_path, objects)
        second = await app_package(tmp_path, objects, version='0.2.0', value=99)
        web = manager(tmp_path, objects, 'web', build_bundles=False)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = first
        await web.initialize(doc)
        await build_environment_bundle(objects, environment='dev-123')
        await web.start()
        original = web._bundle['selection']
        try:
            async def failed_worker(*args): raise RuntimeError('Backend worker is not ready')
            monkeypatch.setattr(web, '_start_worker', failed_worker)
            await web.request_install('orders', second)
            built = await build_environment_bundle(objects, environment='dev-123')
            assert built['selection'] != original
            async def failed(): return web._last_error == 'RuntimeError'
            await eventually(failed)
            assert web._bundle['selection'] == original
            assert web.catalog()['apps'][0]['version'] == '0.1.0'
            host = create_microapp_host(FastAPI(), web, authorize=lambda scope, app: True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                status = (await client.get('/_microapps/environment')).json()
                assert status['active'] == built['selection']
                assert status['serving'] == original and status['serving_error'] == 'RuntimeError'
        finally: await web.close()
    asyncio.run(exercise())


def test_failed_build_job_preserves_last_good_artifact(tmp_path):
    async def exercise():
        objects = CountingStore(tmp_path / 'objects'); controller = manager(tmp_path, objects)
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await controller.initialize(doc)
        first = await build_environment_bundle(objects, environment='dev-123')
        bad = copy.deepcopy(doc['apps']['orders']); bad['browser']['artifact']['sha256'] = '0' * 64
        await controller.request_install('orders', bad)
        with pytest.raises(ValueError, match='SHA-256'):
            await build_environment_bundle(objects, environment='dev-123')
        document, _ = await controller.read_manifest()
        assert document['active']['key'] == first['key']
        assert document['build'] is None and document['error']['type'] == 'ValueError'
    asyncio.run(exercise())


def test_build_cli_outputs_ready_artifact_without_starting_a_web_service(tmp_path):
    async def provision():
        objects = CountingStore(tmp_path / 'objects')
        doc = empty_manifest('dev-123'); doc['apps']['orders'] = await app_package(tmp_path, objects)
        await manager(tmp_path, objects).initialize(doc)
    asyncio.run(provision())
    result = subprocess.run([sys.executable, '-m', 'pytincture.microapps.builder',
        '--objects-directory', str(tmp_path / 'objects'), '--environment', 'dev-123'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    response = json.loads(result.stdout)
    assert response['state'] == 'ready' and response['environment'] == 'dev-123'
    assert (tmp_path / 'objects' / response['key']).is_file()
