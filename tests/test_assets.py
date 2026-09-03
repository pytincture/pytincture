import hashlib
import json

import pytest

import pytincture
import pytincture.assets as browser_assets


def test_browser_asset_export_is_verified_and_self_contained(tmp_path):
    target = browser_assets.export_browser_assets(tmp_path / "frontend")
    manifest_path = target / "integrity" / f"pytincture-{pytincture.__version__}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["assets"]:
        content = (target / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert (target / "dist" / "pytincture.min.js").is_file()
    assert (target / "pyodide" / "0.29.3" / "full" / "pyodide.asm.wasm").is_file()
    assert (
        target
        / "vendor"
        / "materialdesignicons"
        / "fonts"
        / "materialdesignicons-webfont.woff2"
    ).is_file()
    assert (
        target
        / "vendor"
        / "materialdesignicons"
        / "materialdesignicons.css.map"
    ).is_file()


def test_browser_asset_export_rejects_tampered_installed_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "integrity").mkdir(parents=True)
    (source / "dist").mkdir()
    asset = source / "dist" / "pytincture.min.js"
    asset.write_bytes(b"reviewed")
    manifest = {
        "schema": 1,
        "framework_version": pytincture.__version__,
        "assets": [
            {
                "path": "dist/pytincture.min.js",
                "sha256": hashlib.sha256(b"reviewed").hexdigest(),
            }
        ],
    }
    (source / "integrity" / f"pytincture-{pytincture.__version__}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.setattr(browser_assets, "_FRONTEND_ROOT", source)
    asset.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="failed integrity verification"):
        browser_assets.verify_browser_assets()
