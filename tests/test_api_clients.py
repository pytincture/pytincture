"""Real credential exchange, exact grants, and immediate client revocation."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from pytincture import PytinctureConfig, create_app
from pytincture.api_clients import create_client, get_client, main, update_client
from test_bff_api_access import call, client_for, login, service


def setup_client(tmp_path, *, grants=None, **options):
    modules = tmp_path / "appcode"
    modules.mkdir()
    registry = str(tmp_path / "clients.sqlite3")
    credentials = create_client(registry, "demo", grants or [
        {"module": "catalog", "class": "Catalog", "methods": ["public"]},
    ], client_id="integration")
    app = service(modules, decorated=True, bff_api_client_registry=registry, **options)
    return app, registry, credentials


def exchange(client, credentials, application="demo", **kwargs):
    return client.post(f"/{application}/auth/client-token", json={
        "grant_type": "client_credentials", "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
    }, **kwargs)


def test_cookie_free_client_access_class_grants_never_expose_session_methods(tmp_path, caplog):
    app, registry, credentials = setup_client(tmp_path, grants=[{"module": "catalog.py", "class": "Catalog"}])
    with client_for(app) as api:
        response = exchange(api, credentials)
        assert response.status_code == 200, response.text
        issued = response.json()
        assert issued["expires_in"] == 900
        assert issued["token_type"] == "Bearer"
        assert "no-store" in response.headers["cache-control"]
        token = issued["access_token"]
        with caplog.at_level("INFO"):
            assert call(api, token=token).json() == {"value": 42}
            assert call(api, "private", token=token).status_code == 403
        assert not api.cookies
        assert call(api, token=token, application="other").status_code == 403
        assert call(api, token=token + "tampered").status_code == 401
        assert call(api).status_code == 401
        assert '"client_id":"integration"' in caplog.text
        assert token not in caplog.text and credentials["client_secret"] not in caplog.text
        # A token does not grant permission to mint user/session tokens.
        assert api.post('/demo/auth/bff-token', json={'scope': 'session'},
            headers={'Authorization': f'Bearer {token}'}).status_code == 401
        schema = api.get('/demo/catalog/bff-docs/openapi.json').json()
        assert schema['paths']['/Catalog/public']['post']['x-bff-client-grant'] == {
            'application': 'demo', 'module': 'catalog', 'class': 'Catalog', 'methods': ['public']}
        assert 'id="api-client-login"' in api.get('/demo/catalog/bff-docs').text
    assert credentials["client_secret"].encode() not in Path(registry).read_bytes()
    assert get_client(registry, 'integration')['grants'] == [{'module': 'catalog', 'class': 'Catalog'}]


def test_rotation_disable_and_grant_changes_revoke_tokens_immediately(tmp_path):
    app, registry, credentials = setup_client(tmp_path)
    with client_for(app) as api:
        old_token = exchange(api, credentials).json()['access_token']
        rotated = update_client(registry, 'integration', 'rotate')
        assert exchange(api, credentials).status_code == 401
        assert call(api, token=old_token).status_code == 401
        credentials.update(rotated)
        token = exchange(api, credentials).json()['access_token']
        assert call(api, token=token).status_code == 200
        update_client(registry, 'integration', 'disable')
        assert exchange(api, credentials).status_code == 401
        assert call(api, token=token).status_code == 401
        update_client(registry, 'integration', 'enable')
        assert call(api, token=token).status_code == 401
        token = exchange(api, credentials).json()['access_token']
        update_client(registry, 'integration', 'set-grants', grants=[
            {'module': 'catalog', 'class': 'Catalog', 'methods': ['private']}])
        assert call(api, token=token).status_code == 401
        token = exchange(api, credentials).json()['access_token']
        assert call(api, token=token).status_code == 403
        assert call(api, 'private', token=token).status_code == 403


def test_credentials_reject_wrong_app_invalid_input_and_expired_tokens(tmp_path, monkeypatch):
    app, registry, credentials = setup_client(tmp_path)
    with client_for(app) as api:
        assert exchange(api, credentials, application='other').status_code == 401
        assert exchange(api, {**credentials, 'client_id': 'missing'}).status_code == 401
        assert exchange(api, {**credentials, 'client_secret': 'wrong'}).status_code == 401
        assert exchange(api, credentials, headers={'Origin': 'https://evil.test'}).status_code == 403
        for body in ([], {}, {'grant_type': 'client_credentials', 'client_id': [], 'client_secret': 'x'},
                     {'grant_type': 'client_credentials', 'client_id': 'integration', 'client_secret': 'x', 'scope': 'session'}):
            assert api.post('/demo/auth/client-token', json=body).status_code == 422
        response = api.post('/demo/auth/client-token', content='broken', headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert api.post('/demo/auth/client-token', content='secret').status_code == 415
        token = exchange(api, credentials).json()['access_token']
        import itsdangerous.timed
        original = itsdangerous.timed.time.time
        monkeypatch.setattr(itsdangerous.timed.time, 'time', lambda: original() + 901)
        assert call(api, token=token).status_code == 401


def test_clients_work_without_user_login_and_with_swagger_disabled(tmp_path):
    app, registry, credentials = setup_client(tmp_path, api_docs_mode='disabled')
    app = create_app(replace(app.state.pytincture_config, enable_user_login=False))
    with client_for(app) as api:
        assert api.get('/demo/catalog/bff-docs').status_code == 404
        token = exchange(api, credentials).json()['access_token']
        assert call(api, token=token).status_code == 200
        assert call(api, 'private', token=token).status_code == 403
        assert call(api).status_code == 401


def test_cookie_session_and_policies_still_work_and_bad_tokens_do_not_fall_back(tmp_path, monkeypatch):
    app, registry, credentials = setup_client(tmp_path)
    with client_for(app) as browser:
        headers = login(browser)
        assert call(browser, 'private', headers=headers).status_code == 200
        token = exchange(browser, credentials).json()['access_token']
        assert call(browser, 'private', token=token, headers=headers).status_code == 403
        assert call(browser, token='invalid', headers=headers).status_code == 401
        seen = []
        def policy(**kwargs):
            seen.append(kwargs['user'])
            return False
        monkeypatch.setattr(app.state.pytincture_backend, '_configured_bff_policy_hook', lambda: policy)
        assert call(browser, token=token).status_code == 403
        assert seen[0]['client_id'] == 'integration'
        assert seen[0]['auth_provider'] == 'api_client'


def test_registry_failure_and_removed_external_decorator_fail_closed(tmp_path):
    app, registry, credentials = setup_client(tmp_path)
    with client_for(app) as api:
        token = exchange(api, credentials).json()['access_token']
        source = tmp_path / 'appcode/catalog.py'
        source.write_text(source.read_text().replace('    @bff_external\n', ''))
        assert call(api, token=token).status_code == 403
        Path(registry).unlink()
        assert call(api, token=token).status_code == 503
        assert exchange(api, credentials).status_code == 503


def test_exact_nested_module_method_grants_and_identity(tmp_path):
    app, registry, credentials = setup_client(tmp_path, grants=[
        {'module': 'services/catalog', 'class': 'Catalog', 'methods': ['identity']}])
    modules = tmp_path / 'appcode'
    (modules / 'services').mkdir()
    (modules / 'services/__init__.py').write_text('')
    (modules / 'demo.py').write_text('from services.catalog import Catalog\nfrom catalog import Catalog as Other\n')
    (modules / 'services/catalog.py').write_text('''from pytincture.dataclass import backend_for_frontend, bff_external
@backend_for_frontend
class Catalog:
    def __init__(self, _user): self.user = _user
    @bff_external
    def identity(self): return self.user
    @bff_external
    def another(self): return "not granted"
''')
    app = create_app(app.state.pytincture_config)
    with client_for(app) as api:
        token = exchange(api, credentials).json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        for module in ('services/catalog', 'services/catalog.py'):
            response = api.post(f'/demo/classcall/{module}/Catalog/identity', json={}, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()['client_id'] == 'integration'
        assert api.post('/demo/classcall/services/catalog/Catalog/another', json={}, headers=headers).status_code == 403
        assert call(api, token=token).status_code == 403


def test_client_registry_configuration_guards_and_exchange_rate_limit(tmp_path):
    app, registry, credentials = setup_client(tmp_path)
    config = app.state.pytincture_config
    assert PytinctureConfig.from_env(config.to_environ()).bff_api_client_registry == registry
    with pytest.raises(ValueError, match='requires enable_bff_api_tokens'):
        replace(config, enable_bff_api_tokens=False)
    with pytest.raises(ValueError, match='outside modules_path'):
        replace(config, bff_api_client_registry=str(tmp_path / 'appcode/clients.db'))
    with pytest.raises(ValueError, match='strong session_secret'):
        replace(config, session_secret='weak')
    from pytincture.backend.saml import SlidingWindowRateLimiter
    app.state.pytincture_backend.AUTH_LOGIN_RATE_LIMITER = SlidingWindowRateLimiter(1, 60)
    with client_for(app) as api:
        assert exchange(api, credentials).status_code == 200
        response = exchange(api, credentials)
        assert response.status_code == 429 and response.headers['Retry-After']


def test_production_client_exchange_requires_https(tmp_path):
    from fastapi.testclient import TestClient
    app, registry, credentials = setup_client(tmp_path, development=False)
    with TestClient(app, base_url='http://app.example.test') as api:
        assert exchange(api, credentials).status_code == 403
    with TestClient(app, base_url='https://app.example.test') as api:
        response = exchange(api, credentials)
        assert response.status_code == 200, response.text
        assert call(api, token=response.json()['access_token']).status_code == 200


def test_registry_cli_lifecycle_and_audit_without_secret_disclosure(tmp_path, capsys):
    registry = str(tmp_path / 'clients.db')
    args = ['--registry', registry]
    main(args + ['create', '--application', 'demo', '--client-id', 'test-client', '--allow', 'services/catalog:Catalog:read'])
    created = json.loads(capsys.readouterr().out)
    assert created['client_secret']
    assert Path(registry).stat().st_mode & 0o077 == 0
    main(args + ['list'])
    listed = capsys.readouterr().out
    assert 'secret' not in listed and 'revision' not in listed
    main(args + ['set-grants', 'test-client', '--allow', 'services/catalog:Catalog'])
    capsys.readouterr()
    main(args + ['rotate', 'test-client'])
    rotated = json.loads(capsys.readouterr().out)
    assert rotated['client_secret'] != created['client_secret']
    for action in ('disable', 'enable'):
        main(args + [action, 'test-client'])
        capsys.readouterr()
    main(args + ['audit'])
    events = json.loads(capsys.readouterr().out)
    assert [event['action'] for event in events] == ['enable', 'disable', 'rotate', 'set-grants', 'create']
    with pytest.raises(sqlite3.IntegrityError):
        create_client(registry, 'demo', [{'module': 'catalog', 'class': 'Catalog'}], client_id='test-client')
    with pytest.raises(ValueError, match='Unknown API client'):
        update_client(registry, 'missing', 'disable')


@pytest.mark.parametrize('grant', [
    {}, {'module': '../catalog', 'class': 'Catalog'}, {'module': 'catalog', 'class': '*'},
    {'module': 'catalog', 'class': 'Catalog', 'methods': []},
    {'module': 'catalog', 'class': 'Catalog', 'methods': ['*']},
    {'module': 'catalog', 'class': 'Catalog', 'methods': ['_private']},
    {'module': 'catalog', 'class': 'Catalog', 'application': '*'},
])
def test_invalid_grants_cannot_broaden_access(tmp_path, grant):
    with pytest.raises(ValueError):
        create_client(str(tmp_path / 'clients.db'), 'demo', [grant])
