import json

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app


def client_for(tmp_path, *, manifest=True, public=True, **settings):
    (tmp_path / "sample.py").write_text(
        'APP_TITLE = "Sample"\n' + ('APP_RUNTIME_MANIFEST = "browser/manifest.json"\n' if manifest else '')
    )
    (tmp_path / "browser").mkdir(exist_ok=True)
    (tmp_path / "browser/manifest.json").write_text(json.dumps({
        "schema": 1, "runtimes": ["pyodide", "micropython", "transcrypt"],
    }))
    config = PytinctureConfig(
        modules_path=str(tmp_path),
        environment={"PYTINCTURE_PUBLIC_ASSET_PATHS": '{"sample":["browser/*"]}'} if public else {},
        **settings,
    )
    return TestClient(create_app(config))


def test_runtime_configuration_environment_and_validation():
    config = PytinctureConfig.from_env({
        "PYTINCTURE_BROWSER_RUNTIME": "micropython",
        "PYTINCTURE_ALLOW_RUNTIME_SELECTION": "true",
    })
    assert config.browser_runtime == "micropython"
    assert config.allow_runtime_selection is True
    assert config.to_environ()["PYTINCTURE_BROWSER_RUNTIME"] == "micropython"
    with pytest.raises(ValueError, match="browser_runtime"):
        PytinctureConfig(browser_runtime="unknown")
    with pytest.raises(ValueError, match="boolean"):
        PytinctureConfig(allow_runtime_selection="true")


def test_existing_app_keeps_pyodide_preload(tmp_path):
    with client_for(tmp_path, manifest=False) as client:
        page = client.get('/sample')
    assert page.status_code == 200
    assert '/pyodide.js?uuid=' in page.text
    assert 'runtime: "pyodide"' in page.text
    assert 'runtimeManifestUrl: null' in page.text
    assert '***' not in page.text


@pytest.mark.parametrize('engine', ['pyodide', 'micropython', 'transcrypt'])
def test_portable_application_selects_engine_without_preloading_pyodide(tmp_path, engine):
    with client_for(tmp_path, allow_runtime_selection=True) as client:
        page = client.get('/sample', params={'runtime': engine})
        asset = client.get('/sample/appcode/browser/manifest.json')
        unrelated = client.get('/other/appcode/browser/manifest.json')
        loader = client.get('/sample/frontend/browser-runtimes.js')
    assert page.status_code == 200, page.text
    assert f'runtime: "{engine}"' in page.text
    assert 'runtimeManifestUrl: "/sample/appcode/browser/manifest.json"' in page.text
    assert '/pyodide.js?uuid=' not in page.text
    assert asset.status_code == 200
    assert unrelated.status_code == 404
    assert loader.status_code == 200


def test_selection_is_opt_in_and_unknown_engine_fails(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get('/sample?runtime=transcrypt').status_code == 400
    with client_for(tmp_path, allow_runtime_selection=True) as client:
        assert client.get('/sample?runtime=unknown').status_code == 422


def test_engine_default_and_application_override(tmp_path):
    with client_for(tmp_path, browser_runtime='micropython') as client:
        assert 'runtime: "micropython"' in client.get('/sample').text
        with (tmp_path/'sample.py').open('a') as source:
            source.write('APP_BROWSER_RUNTIME = "transcrypt"\n')
        assert 'runtime: "transcrypt"' in client.get('/sample').text


def test_missing_private_and_unsupported_manifests_fail_explicitly(tmp_path):
    with client_for(tmp_path, manifest=False, browser_runtime='micropython') as client:
        response = client.get('/sample')
        assert response.status_code == 422
        assert 'APP_RUNTIME_MANIFEST' in response.text
    with client_for(tmp_path, public=False) as client:
        assert client.get('/sample').status_code == 422
    with client_for(tmp_path, browser_runtime='transcrypt') as client:
        (tmp_path/'browser/manifest.json').write_text('{"schema":1,"runtimes":["pyodide"]}')
        response = client.get('/sample')
        assert response.status_code == 422
        assert 'does not support transcrypt' in response.text


def test_manifest_path_cannot_escape_module_root(tmp_path):
    with client_for(tmp_path) as client:
        (tmp_path/'sample.py').write_text('APP_RUNTIME_MANIFEST = "../private.json"\n')
        assert client.get('/sample').status_code == 422


def test_source_files_remain_private(tmp_path):
    with client_for(tmp_path) as client:
        (tmp_path/'browser/secret.py').write_text('secret = "server-only"\n')
        assert client.get('/sample/appcode/browser/secret.py').status_code == 404


def test_portable_replay_tokens_fail_explicitly(tmp_path):
    with client_for(tmp_path, enable_bff_replay_tokens=True) as client:
        response = client.get('/sample')
        assert response.status_code == 422
        assert 'replay tokens' in response.text


def test_micropython_conventional_bundle_needs_no_source_declaration(tmp_path):
    with client_for(tmp_path, manifest=False, browser_runtime='micropython', allow_runtime_selection=True) as client:
        (tmp_path / 'browser/sample').mkdir()
        (tmp_path / 'browser/sample/manifest.json').write_text('{"schema":1,"runtimes":["micropython"]}')
        response = client.get('/sample')
        assert response.status_code == 200
        assert 'runtimeManifestUrl: "/sample/appcode/browser/sample/manifest.json"' in response.text
        # Opting back into Pyodide retains the original package delivery path.
        response = client.get('/sample?runtime=pyodide')
        assert response.status_code == 200
        assert 'runtimeManifestUrl: null' in response.text
