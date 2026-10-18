"""Prebuilt discovery remains bound to deployment-owned source bytes."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.backend import bff
from pytincture.backend.bff_manifest import build_bff_manifest, load_bff_manifest
from pytincture.prebuild import build_prebuilt_appcode

SOURCE = '''from pytincture.dataclass import backend_for_frontend, bff_policy
raise RuntimeError("application must not execute during discovery")
@backend_for_frontend
class Data:
    value = 3
    def read(self, values=(1, 2), *, options={"nested": [True, None]}): return values
'''
RESTRICTED = SOURCE.replace('    def read', '    @bff_policy(role="admin")\n    def read')


@pytest.fixture
def artifact(tmp_path):
    root = tmp_path / 'modules'
    root.mkdir()
    (root / 'data.py').write_text(SOURCE)
    (root / 'empty.py').write_text('# no exports')
    target = build_bff_manifest(root, tmp_path / 'private' / 'bff.json')
    return root, target


def test_roundtrip_preserves_operation_types_and_relative_paths(artifact, tmp_path):
    root, target = artifact
    expected = bff.BFFRegistry(str(root)).operations
    moved = tmp_path / 'deployed'
    shutil.copytree(root, moved)
    actual = bff.BFFRegistry(str(moved), prebuilt_manifest=str(target)).operations
    for operation in actual.values():
        assert operation['_source_path'].startswith(str(moved))
    for key in expected:
        assert {k: v for k, v in actual[key].items() if k != '_source_path'} == {
            k: v for k, v in expected[key].items() if k != '_source_path'}
    assert str(root) not in target.read_text()


def test_prebuilt_startup_does_not_parse_unchanged_source(artifact, monkeypatch):
    root, target = artifact
    from pytincture import dataclass
    def reject(*args, **kwargs):
        raise AssertionError('Unchanged source was parsed at runtime')
    monkeypatch.setattr(bff, 'get_bff_manifest', reject)
    monkeypatch.setattr(dataclass, 'get_bff_manifest', reject)
    application = create_app(PytinctureConfig(
        modules_path=str(root), bff_prebuilt_manifest=str(target)))
    with TestClient(application) as client:
        assert client.get('/healthz').status_code == 200
    assert application.state.pytincture_backend.BFF_REGISTRY


@pytest.mark.parametrize('change', ['modified', 'added', 'deleted', 'invalid', 'symlink'])
def test_source_changes_cannot_use_stale_prebuilt_exports(artifact, tmp_path, change):
    root, target = artifact
    source = root / 'data.py'
    if change == 'modified':
        source.write_text(RESTRICTED)
    elif change == 'added':
        (root / 'new.py').write_text(RESTRICTED)
    elif change == 'deleted':
        source.unlink()
    elif change == 'invalid':
        source.write_text('def broken(')
    else:
        source.unlink()
        outside = tmp_path / 'outside.py'
        outside.write_text(SOURCE)
        source.symlink_to(outside)
    application = create_app(PytinctureConfig(
        modules_path=str(root), bff_prebuilt_manifest=str(target)))
    backend = application.state.pytincture_backend
    if change in ('modified', 'added'):
        with pytest.raises(RuntimeError, match='@bff_policy exports require'):
            with TestClient(application):
                pass
    else:
        assert not backend.BFF_REGISTRY
        if change == 'invalid':
            assert backend.BFF_REGISTRY_FAILURES == {'data.py': 'invalid_python_syntax'}


def test_parser_mismatch_falls_back_to_source(artifact):
    root, target = artifact
    payload = json.loads(target.read_text())
    payload['parser']['parser_sha256'] = '0' * 64
    target.write_text(json.dumps(payload))
    assert load_bff_manifest(target) == {}
    assert bff.BFFRegistry(str(root), prebuilt_manifest=str(target)).operations


@pytest.mark.parametrize('damage', ['bad_json', 'bad_schema', 'path_escape', 'digest', 'metadata'])
def test_malformed_configured_artifacts_fail_startup(artifact, damage):
    root, target = artifact
    payload = json.loads(target.read_text())
    if damage == 'bad_json':
        target.write_text('{')
    else:
        if damage == 'bad_schema':
            payload['schema'] = 999
        elif damage == 'path_escape':
            payload['sources']['../escape.py'] = payload['sources'].pop('data.py')
        elif damage == 'digest':
            payload['sources']['data.py']['sha256'] = 'invalid'
        else:
            payload['sources']['data.py']['manifest'] = ['unexpected', []]
        target.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match='prebuilt BFF manifest'):
        bff.BFFRegistry(str(root), prebuilt_manifest=str(target))


def test_missing_configured_artifact_fails_startup(tmp_path):
    with pytest.raises(RuntimeError, match='prebuilt BFF manifest'):
        bff.BFFRegistry(str(tmp_path), prebuilt_manifest='missing.json')


def test_unusual_literal_defaults_fall_back_to_source(artifact):
    root, target = artifact
    (root / 'data.py').write_text(SOURCE.replace('values=(1, 2)', 'values={1, 2}'))
    build_bff_manifest(root, target)
    assert 'data.py' not in load_bff_manifest(target)
    registry = bff.BFFRegistry(str(root), prebuilt_manifest=str(target))
    assert registry.operations[('data.py', 'Data', 'read')]['parameters'][0]['default'] == {1, 2}


def test_appcode_build_can_emit_separate_private_server_artifact(tmp_path):
    root = tmp_path / 'modules'
    root.mkdir()
    (root / 'demo.py').write_text('value = 1\n')
    (root / 'data.py').write_text(SOURCE)
    private = tmp_path / 'private' / 'bff.json'
    archive = build_prebuilt_appcode('demo', tmp_path / 'public', modules_path=root,
                                     server_bff_manifest=private)
    assert archive.is_file() and private.is_file()
    assert 'data.py' in load_bff_manifest(private)
    import zipfile
    with zipfile.ZipFile(archive) as bundled:
        assert 'bff.json' not in bundled.namelist()


def test_build_rejects_invalid_sources_without_replacing_artifact(artifact):
    root, target = artifact
    before = target.read_bytes()
    (root / 'broken.py').write_text('def broken(')
    with pytest.raises(ValueError, match='rejected source files'):
        build_bff_manifest(root, target)
    assert target.read_bytes() == before


def test_configuration_roundtrips_prebuilt_manifest(artifact):
    root, target = artifact
    config = PytinctureConfig(modules_path=str(root), bff_prebuilt_manifest=str(target))
    assert config.to_environ()['PYTINCTURE_BFF_PREBUILT_MANIFEST'] == str(target)
    assert PytinctureConfig.from_env(config.to_environ()) == config


def test_prebuilt_registry_executes_real_request_and_revalidates_edits(artifact):
    root, target = artifact
    source = root / 'data.py'
    source.write_text(SOURCE.replace('raise RuntimeError("application must not execute during discovery")\n', ''))
    build_bff_manifest(root, target)
    application = create_app(PytinctureConfig(
        modules_path=str(root), bff_prebuilt_manifest=str(target)))
    with TestClient(application) as client:
        response = client.post('/data/classcall/data.py/Data/read',
                               json={'args': [[4, 5]], 'kwargs': {}})
        assert response.status_code == 200, response.text
        assert response.json() == [4, 5]
        application.state.pytincture_backend.set_bff_policy_hook(lambda **kwargs: False)
        source.write_text(source.read_text().replace('    def read', '    @bff_policy(role="admin")\n    def read'))
        response = client.post('/data/classcall/data.py/Data/read',
                               json={'args': [[4, 5]], 'kwargs': {}})
        assert response.status_code == 403, response.text


def test_appcode_build_rejects_private_manifest_in_public_output(tmp_path):
    with pytest.raises(ValueError, match='outside the browser output'):
        build_prebuilt_appcode('demo', tmp_path / 'public', modules_path=tmp_path,
                              server_bff_manifest=tmp_path / 'public' / 'server-bff.json')
