import json
from importlib.metadata import PackagePath
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.packaged_apps import build_app_manifest, create_packaged_app, load_packaged_app


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "customer_app" / "apps"
    root.mkdir(parents=True)
    (root / "orders.py").write_text("from services import Data\n")
    (root / "services.py").write_text(
        "from pytincture.dataclass import backend_for_frontend\n"
        "@backend_for_frontend\nclass Data:\n"
        "    def __init__(self, _user): pass\n"
        "    def read(self): return 42\n"
    )
    build_app_manifest(root, "orders")
    def distribution(_):
        return SimpleNamespace(version="0.3.0", metadata={"Name": "customer-orders"},
            files=[PackagePath(p.relative_to(tmp_path).as_posix()) for p in tmp_path.rglob("*") if p.is_file()],
            locate_file=lambda path: tmp_path / str(path))
    monkeypatch.setattr("pytincture.packaged_apps.metadata.distribution", distribution)
    return root


def test_packaged_service_and_existing_service_are_independent(installed, tmp_path):
    config = PytinctureConfig(modules_path=str(tmp_path))
    packaged = create_packaged_app("customer-orders", config, expected_version="0.3.0")
    existing = create_app(config)
    assert existing.state.pytincture_config.modules_path == str(tmp_path)
    assert packaged.state.pytincture_config.modules_path == str(installed)
    assert packaged.state.pytincture_config.default_application == "orders"
    with TestClient(packaged) as client:
        assert client.get("/healthz").status_code == 200
        response = client.post("/orders/classcall/services.py/Data/read",
                               json={"args": [], "kwargs": {}})
        assert response.status_code == 200
        assert response.json() == 42
    assert config.modules_path == str(tmp_path)


def test_rejects_changed_source(installed):
    (installed / "orders.py").write_text("raise RuntimeError('must never execute')")
    with pytest.raises(ValueError, match="integrity"):
        load_packaged_app("customer-orders")


def test_rejects_wrong_selected_version(installed):
    with pytest.raises(ValueError, match="version"):
        load_packaged_app("customer-orders", expected_version="0.4.0")


@pytest.mark.parametrize("change", [
    {"schema": 2}, {"files": {"../secret": "a" * 64}},
    {"requires_pytincture": ">=999"}, {"bff_manifest": "missing.json"},
    {"environment": {"ENABLE_DEV_EMAIL_LOGIN": "true"}},
])
def test_rejects_invalid_manifest(installed, change):
    path = installed / "pytincture-app.json"
    doc = json.loads(path.read_text()); doc.update(change)
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        load_packaged_app("customer-orders")


def test_rejects_uninventoried_file(installed):
    (installed / "injected.py").write_text("# not in approved inventory")
    with pytest.raises(ValueError, match="Uninventoried"):
        load_packaged_app("customer-orders")


def test_rejects_symlinks(installed, tmp_path):
    (tmp_path / "outside.py").write_text("secret")
    (installed / "orders.py").unlink()
    (installed / "orders.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(ValueError, match="symlink"):
        load_packaged_app("customer-orders")


def test_bff_manifest_is_selected_explicitly(installed):
    (installed / "bff.json").write_text("{}")
    build_app_manifest(installed, "orders", bff_manifest="bff.json")
    assert load_packaged_app("customer-orders").bff_manifest == installed / "bff.json"
