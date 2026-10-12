"""The immutable-code opt-out changes cache freshness, not dispatch guards."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.backend.bff import BFFRegistry


SOURCE = '''from pytincture.dataclass import backend_for_frontend
calls = []
@backend_for_frontend
class Data:
    def read(self):
        calls.append(1)
        return {"calls": len(calls)}
'''


def application(root, *, source=SOURCE, **options):
    (root / 'demo.py').write_text('from data import Data\n')
    (root / 'data.py').write_text(source)
    return create_app(PytinctureConfig(modules_path=str(root), **options))


def test_cache_revalidation_defaults_on_and_is_isolated_per_app(tmp_path):
    default = PytinctureConfig(modules_path=str(tmp_path))
    assert default.bff_cache_revalidation is True
    assert default.to_environ()['BFF_CACHE_REVALIDATION'] == 'true'
    disabled = PytinctureConfig.from_env({
        'MODULES_PATH': str(tmp_path), 'BFF_CACHE_REVALIDATION': 'false',
    })
    assert disabled.bff_cache_revalidation is False
    assert disabled.to_environ()['BFF_CACHE_REVALIDATION'] == 'false'
    assert PytinctureConfig.from_env(disabled.to_environ(), bff_cache_revalidation=True).bff_cache_revalidation
    with pytest.raises(ValueError, match='boolean'):
        PytinctureConfig.from_env({'BFF_CACHE_REVALIDATION': 'maybe'})
    for invalid in ('false', None, 0):
        with pytest.raises(ValueError, match='bff_cache_revalidation must be a boolean'):
            PytinctureConfig(bff_cache_revalidation=invalid)
    first = create_app(disabled).state.pytincture_backend
    second = create_app(default).state.pytincture_backend
    assert first._BFF_REGISTRY_STATE.revalidate is False
    assert first.APPLICATION_GRAPH_CACHE.revalidate is False
    assert second._BFF_REGISTRY_STATE.revalidate is True
    assert second.APPLICATION_GRAPH_CACHE.revalidate is True


@pytest.mark.parametrize('revalidate', [True, False])
def test_registry_freshness_is_optional_but_initial_discovery_is_not(tmp_path, revalidate):
    source = tmp_path / 'data.py'
    source.write_text(SOURCE)
    registry = BFFRegistry(str(tmp_path), revalidate=revalidate)
    first = registry.operation(str(tmp_path), 'data.py', 'Data', 'read')
    assert first['_source_digest'] == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text(SOURCE.replace('append(1)', 'append(2)'))
    second = registry.operation(str(tmp_path), 'data.py', 'Data', 'read')
    assert (first['_source_digest'] != second['_source_digest']) is revalidate
    assert registry.operation(str(tmp_path), 'data.py', 'Data', 'missing') is None


@pytest.mark.parametrize('revalidate', [True, False])
def test_graph_freshness_follows_setting_and_restart_refreshes_it(tmp_path, revalidate):
    app = application(tmp_path, bff_cache_revalidation=revalidate)
    backend = app.state.pytincture_backend
    before = backend._application_bff_identifiers('demo', str(tmp_path))
    assert 'data.py' in before
    (tmp_path / 'demo.py').write_text('VALUE = 1\n')
    after = backend._application_bff_identifiers('demo', str(tmp_path))
    assert ('data.py' not in after) is revalidate
    restarted = create_app(app.state.pytincture_config).state.pytincture_backend
    assert 'data.py' not in restarted._application_bff_identifiers('demo', str(tmp_path))


def test_disabled_warm_revalidation_still_checks_cold_graph_consistency(tmp_path, monkeypatch):
    from fastapi import HTTPException

    backend = application(tmp_path, bff_cache_revalidation=False).state.pytincture_backend
    monkeypatch.setattr(backend.APPLICATION_GRAPH_CACHE, '_is_current', lambda *_args: False)
    with pytest.raises(HTTPException, match='changed during graph discovery') as failure:
        backend._application_bff_identifiers('demo', str(tmp_path))
    assert failure.value.status_code == 503


@pytest.mark.parametrize('mutation', ['changed', 'removed', 'symlink'])
def test_disabled_cache_revalidation_still_rejects_changed_or_unsafe_execution_source(tmp_path, mutation):
    app = application(tmp_path, bff_cache_revalidation=False)
    source = tmp_path / 'data.py'
    marker = tmp_path / 'unexpected-execution'
    replacement = SOURCE + f'\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("PRIVATE_RESULT")\n'
    with TestClient(app, raise_server_exceptions=False) as client:
        route = '/demo/classcall/data.py/Data/read'
        assert client.post(route, json={}).json() == {'calls': 1}
        assert client.post(route, json={}).json() == {'calls': 1}
        if mutation == 'changed':
            source.write_text(replacement)
        else:
            source.unlink()
            if mutation == 'symlink':
                outside = tmp_path.parent / (tmp_path.name + '-outside.py')
                outside.write_text(replacement)
                source.symlink_to(outside)
        rejected = client.post(route, json={})
    assert rejected.status_code == 500
    assert 'PRIVATE_RESULT' not in rejected.text
    assert not marker.exists()


def test_disabled_cache_revalidation_still_checks_current_runtime_method(tmp_path, monkeypatch):
    app = application(tmp_path, bff_cache_revalidation=False)
    backend = app.state.pytincture_backend
    load = backend._load_source_module
    executed = []

    def replacement(self):
        executed.append(True)
        return "PRIVATE_RESULT"

    def replaced_module(*args, **kwargs):
        module = load(*args, **kwargs)
        module.Data._pytincture_bff_original.read = replacement
        return module

    with TestClient(app) as client:
        route = '/demo/classcall/data.py/Data/read'
        assert client.post(route, json={}).json() == {'calls': 1}
        monkeypatch.setattr(backend, '_load_source_module', replaced_module)
        rejected = client.post(route, json={})
    assert rejected.status_code == 404
    assert 'PRIVATE_RESULT' not in rejected.text
    assert executed == []


@pytest.mark.parametrize('revalidate', [True, False])
def test_browser_auth_csrf_and_replay_checks_still_run_on_warm_calls(tmp_path, monkeypatch, revalidate):
    from test_bff_api_access import service, client_for, login, call

    app = service(tmp_path, bff_cache_revalidation=revalidate)
    with client_for(app) as client:
        assert call(client, 'private').status_code == 401
        headers = login(client)
        assert call(client, 'private', headers=headers).status_code == 200
        assert call(client, 'private').status_code == 403
        assert call(client, 'private', headers=headers, application='other').status_code == 403
        monkeypatch.setattr(app.state.pytincture_backend, 'ENABLE_BFF_REPLAY_TOKENS', True)
        assert call(client, 'private', headers=headers).status_code == 409


def test_disabled_cache_revalidation_preserves_application_membership(tmp_path):
    (tmp_path / 'foreign.py').write_text(SOURCE.replace('class Data:', 'class Foreign:'))
    app = application(tmp_path, bff_cache_revalidation=False)
    with TestClient(app) as client:
        assert client.post('/demo/classcall/data.py/Data/read', json={}).status_code == 200
        denied = client.post('/demo/classcall/foreign.py/Foreign/read', json={})
    assert denied.status_code == 404
    assert denied.json() == {'detail': 'BFF operation not exported by this application'}
