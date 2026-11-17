import asyncio
import csv
import hashlib
import io
from pathlib import Path
import zipfile

import httpx
import pytest

from pytincture import PytinctureConfig, create_app
from pytincture.microapps import BrowserArtifact, LiveMicroappManager, WheelArtifact, create_microapp_host
from pytincture.packaged_apps import build_app_manifest


def wheel(tmp_path, name, version, files):
    filename = f"{name}-{version}-py3-none-any.whl"
    info = f"{name}-{version}.dist-info"
    content = {**files, f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
               f"{info}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = io.StringIO()
    writer = csv.writer(record)
    for path in [*content, f"{info}/RECORD"]:
        writer.writerow([path, "", ""])
    content[f"{info}/RECORD"] = record.getvalue()
    path = tmp_path / filename
    with zipfile.ZipFile(path, "w") as archive:
        for relative, text in content.items():
            archive.writestr(relative, text)
    return WheelArtifact(path, hashlib.sha256(path.read_bytes()).hexdigest())


def artifacts(tmp_path, version="0.1.0", value=42):
    root = tmp_path / ("source-" + version)
    root.mkdir()
    (root / "orders.py").write_text("from services import Data\n")
    (root / "services.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\nclass Data:\n"
        "    def __init__(self, _user): pass\n"
        f"    def read(self): return {value}\n")
    build_app_manifest(root, "orders")
    backend = wheel(tmp_path, "demo_backend", version,
                    {"demo_backend/apps/" + p.name: p.read_text() for p in root.iterdir()})
    browser = wheel(tmp_path, "demo_browser", version, {"demo_live_ui/__init__.py":
        "async def mount(context):\n"
        "    value = await context.callBff('services.py', 'Data', 'read')\n"
        "    context.root.textContent = 'Live value: ' + str(value)\n"})
    return backend, BrowserArtifact(browser, "demo_live_ui:mount")


def test_install_while_host_and_client_are_running_and_restart(tmp_path):
    backend, browser = artifacts(tmp_path)
    async def exercise():
        config = PytinctureConfig(modules_path=str(tmp_path))
        manager = LiveMicroappManager(tmp_path / "store", config)
        host = create_microapp_host(create_app(config), manager, authorize=lambda scope, app: True)
        await manager.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://127.0.0.1") as client:
                assert (await client.get("/healthz")).status_code == 200
                assert (await client.get("/_microapps/catalog")).json()["apps"] == []
                release = await manager.install("orders", backend, browser, approval_id="review-1")
                assert release is not None
                catalog = (await client.get("/_microapps/catalog")).json()
                assert catalog["revision"] == 1
                item = catalog["apps"][0]
                response = await client.get(item["browser"]["url"])
                assert hashlib.sha256(response.content).hexdigest() == browser.wheel.sha256
                # Private backend artifact is never a browser asset.
                assert (await client.get(item["browser"]["url"].rsplit('/', 1)[0] + '/' + Path(backend.path).name)).status_code == 404
                response = await client.post(item["bff_base"] + "/classcall/services.py/Data/read", json={"args": [], "kwargs": {}})
                assert response.status_code == 200, response.text
                assert response.json() == 42
                await manager.install("orders", backend, browser, approval_id="review-1")
                assert manager.catalog()["revision"] == 1
        finally:
            await manager.close()
        restored = LiveMicroappManager(tmp_path / "store", config)
        await restored.start()
        try:
            assert restored.catalog()["apps"][0]["release"] == release["release"]
        finally:
            await restored.close()
    asyncio.run(exercise())


def test_catalog_and_artifacts_require_environment_authorization(tmp_path):
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        host = create_microapp_host(create_app(), manager, authorize=lambda scope, app: False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://127.0.0.1") as client:
            response = await client.get("/_microapps/catalog")
            assert response.status_code == 403
            assert (await client.get('/healthz')).status_code == 200
    asyncio.run(exercise())


def test_rejected_artifact_does_not_activate_or_change_catalog(tmp_path):
    backend, browser = artifacts(tmp_path)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        await manager.start()
        try:
            with pytest.raises(ValueError, match="SHA-256"):
                await manager.install("orders", WheelArtifact(backend.path, "0" * 64), browser, approval_id="review")
            assert manager.catalog() == {"schema": 1, "revision": 0, "apps": []}
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_versions_keep_old_bff_routes_working(tmp_path):
    first, first_ui = artifacts(tmp_path)
    second, second_ui = artifacts(tmp_path, "0.2.0", 99)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        host = create_microapp_host(create_app(), manager, authorize=lambda scope, app: True)
        await manager.start()
        try:
            old = await manager.install("orders", first, first_ui, approval_id="review-1")
            new = await manager.install("orders", second, second_ui, approval_id="review-2")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://127.0.0.1") as client:
                for release, value in [(old, 42), (new, 99)]:
                    response = await client.post(release["bff_base"] + "/classcall/services.py/Data/read", json={"args": [], "kwargs": {}})
                    assert response.status_code == 200, response.text
                    assert response.json() == value
            assert manager.catalog()["apps"][0]["version"] == "0.2.0"
            await manager.activate("orders", old["release"])
            assert manager.catalog()["apps"][0]["version"] == "0.1.0"
            assert manager.catalog()["revision"] == 3
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_not_ready_does_not_publish_release(tmp_path, monkeypatch):
    backend, browser = artifacts(tmp_path)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        await manager.start()
        async def not_ready(key, release):
            raise RuntimeError("not ready")
        monkeypatch.setattr(manager, "_start_worker", not_ready)
        try:
            with pytest.raises(RuntimeError, match="not ready"):
                await manager.install("orders", backend, browser, approval_id="review")
            assert manager.catalog()["revision"] == 0
            assert not manager.catalog()["apps"]
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_only_one_controller_can_own_a_store(tmp_path):
    async def exercise():
        first = LiveMicroappManager(tmp_path, PytinctureConfig())
        second = LiveMicroappManager(tmp_path, PytinctureConfig())
        await first.start()
        try:
            with pytest.raises(RuntimeError, match="controller"):
                await second.start()
        finally:
            await first.close()
        await second.start()
        await second.close()
    asyncio.run(exercise())


def test_catalog_filters_unauthorized_app_and_denies_its_bff(tmp_path):
    backend, browser = artifacts(tmp_path)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        host = create_microapp_host(create_app(), manager, authorize=lambda scope, app: app is None)
        await manager.start()
        try:
            item = await manager.install("orders", backend, browser, approval_id="review")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://127.0.0.1") as client:
                assert (await client.get('/_microapps/catalog')).json()['apps'] == []
                assert (await client.get(item['browser']['url'])).status_code == 403
                assert (await client.post(item['bff_base'] + '/classcall/services.py/Data/read', json={})).status_code == 403
        finally:
            await manager.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'pkg/../../escape.py'])
def test_rejects_wheel_path_traversal(tmp_path, name):
    from pytincture.microapps.manager import _unpack_wheel
    artifact = wheel(tmp_path, 'bad', '0.1', {name: 'bad'})
    with pytest.raises(ValueError, match='paths'):
        _unpack_wheel(Path(artifact.path), tmp_path / 'unpack')


def test_catalog_save_failure_preserves_previous_release(tmp_path, monkeypatch):
    first, first_ui = artifacts(tmp_path)
    second, second_ui = artifacts(tmp_path, "0.2.0", 99)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / "store", PytinctureConfig())
        await manager.start()
        try:
            old = await manager.install("orders", first, first_ui, approval_id="review-1")
            def fail_save():
                raise OSError("disk unavailable")
            monkeypatch.setattr(manager, "_save", fail_save)
            with pytest.raises(OSError, match="disk unavailable"):
                await manager.install("orders", second, second_ui, approval_id="review-2")
            assert manager.catalog()["revision"] == 1
            assert manager.catalog()["apps"][0]["release"] == old["release"]
            assert len(manager._workers) == 1
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_platform_login_and_csrf_work_across_worker_boundary(tmp_path):
    import re
    import secrets
    backend, browser = artifacts(tmp_path)
    async def exercise():
        config = PytinctureConfig(modules_path=str(tmp_path / 'source-0.1.0'),
            enable_user_login=True, enable_dev_email_login=True,
            session_secret=secrets.token_urlsafe(32),
            environment={'ALLOWED_EMAILS': 'person@example.com'})
        base = create_app(config)
        manager = LiveMicroappManager(tmp_path / 'store', config)
        host = create_microapp_host(base, manager, authorize=lambda scope, app: True)
        await manager.start()
        try:
            item = await manager.install('orders', backend, browser, approval_id='review')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url='http://127.0.0.1') as client:
                url = item['bff_base'] + '/classcall/services.py/Data/read'
                assert (await client.post(url, json={})).status_code == 401
                page = await client.get('/orders/login')
                token = re.search(r'name="login_csrf_token" value="([^"]+)"', page.text).group(1)
                login = await client.post('/orders/auth/user', data={'email': 'person@example.com',
                    'password': 'password', 'login_csrf_token': token})
                assert login.status_code == 303, login.text
                assert (await client.post(url, json={})).status_code == 403
                csrf = client.cookies[base.state.pytincture_backend._CSRF_COOKIE]
                result = await client.post(url, json={'args': [], 'kwargs': {}}, headers={'X-CSRF-Token': csrf})
                assert result.status_code == 200, result.text
                assert result.json() == 42
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_live_worker_supports_readonly_modules_hardening(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip('Filesystem permission enforcement requires a non-root service account')
    backend, browser = artifacts(tmp_path)
    source = tmp_path / 'source-0.1.0'
    source.chmod(0o555)
    async def exercise():
        config = PytinctureConfig(modules_path=str(source), require_readonly_modules_path=True)
        manager = LiveMicroappManager(tmp_path / 'store', config)
        await manager.start()
        try:
            item = await manager.install('orders', backend, browser, approval_id='review')
            assert item['id'] == 'orders'
        finally:
            await manager.close()
    try:
        asyncio.run(exercise())
    finally:
        source.chmod(0o755)


def test_mismatched_wheel_metadata_rejects_without_hanging(tmp_path):
    import shutil
    backend, browser = artifacts(tmp_path)
    renamed = tmp_path / 'different_name-0.1.0-py3-none-any.whl'
    shutil.copyfile(backend.path, renamed)
    async def exercise():
        manager = LiveMicroappManager(tmp_path / 'store', PytinctureConfig())
        await manager.start()
        try:
            with pytest.raises(ValueError, match='matching distribution'):
                await asyncio.wait_for(manager.install('orders', WheelArtifact(renamed, backend.sha256),
                    browser, approval_id='review'), timeout=3)
            assert manager.catalog()['revision'] == 0
        finally:
            await manager.close()
    asyncio.run(exercise())
