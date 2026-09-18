"""Exercise documentation scope and real cookie-to-bearer delegation boundaries."""
import json

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app

PASSWORD_HASH = ('$argon2id$v=19$m=65536,t=3,p=4$1nAFATBkZHf7FYm10EoAqw$'
                 'bcQeiCVDJV5nH2dSoHhYtUlyLARtmS1ce7UBSUXokYQ')


def service(tmp_path, *, decorated=False, include_session_methods_in_docs=False, development=True, **options):
    (tmp_path / 'demo.py').write_text('APP_TITLE = "Library & Books"\nfrom catalog import Catalog\n')
    (tmp_path / 'other.py').write_text('from catalog import Catalog\n')
    (tmp_path / 'catalog.py').write_text('''from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Catalog:
    def public(self, value: int): return {"value": value}
    def private(self, value: int): return {"private": value}
''')
    if decorated:
        source = (tmp_path / 'catalog.py').read_text()
        source = source.replace('import backend_for_frontend', 'import backend_for_frontend, bff_external')
        source = source.replace('@backend_for_frontend\n', f'@backend_for_frontend(include_session_methods_in_docs={include_session_methods_in_docs!r})\n')
        source = source.replace('    def public(', '    @bff_external\n    def public(')
        (tmp_path / 'catalog.py').write_text(source)
    return create_app(PytinctureConfig(
        modules_path=str(tmp_path), enable_user_login=True,
        enable_bff_api_tokens=True, allow_development_auth_origin=development,
        allowed_hosts=() if development else ("app.example.test",),
        canonical_origin=None if development else "https://app.example.test",
        session_https_only=not development, session_secret='docs-test-secret-0123456789abcdef-abcd',
        environment={
            'ALLOWED_EMAILS': 'reader@example.test',
            'AUTH_PASSWORD_HASHES': json.dumps({'reader@example.test': PASSWORD_HASH}),
            'ALLOWED_NOAUTH_CLASSCALLS': json.dumps([] if decorated else [{
                'application': 'demo', 'file': 'catalog', 'class': 'Catalog', 'function': 'public',
            }]),
        }, **options,
    ))


def client_for(app):
    return TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 1234))


def login(client):
    transaction = client.get('/demo/auth/mcp').json()
    response = client.post('/demo/auth/mcp', json={
        'email': 'reader@example.test', 'password': 'demo-password',
        'login_csrf_token': transaction['login_csrf_token'],
    })
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': client.cookies.get('pytincture-dev-csrf')}


def call(client, method='public', token=None, application='demo', headers=None):
    request_headers = dict(headers or {})
    if token:
        request_headers['Authorization'] = f'Bearer {token}'
    return client.post(f'/{application}/classcall/catalog.py/Catalog/{method}',
                       json={'value': 42}, headers=request_headers)


def test_app_title_and_public_only_docs_have_no_private_schema_escape(tmp_path):
    app = service(tmp_path, api_docs_scope='public')
    with client_for(app) as client:
        docs = client.get('/demo/catalog/bff-docs')
        assert '<title>Library &amp; Books API</title>' in docs.text
        assert 'pyTincture' not in docs.text
        assert 'Methods allowed by my sign-in' not in docs.text
        schema = client.get('/demo/catalog/bff-docs/openapi.json').json()
        assert schema['info']['title'] == 'Library & Books API'
        assert set(schema['paths']) == {'/Catalog/public'}
        assert schema['paths']['/Catalog/public']['post']['x-bff-public'] is True
        assert client.get('/other/catalog/bff-docs/openapi.json').status_code == 404
        for path in ('/bff-docs', '/bff-docs/openapi.json', '/docs', '/redoc', '/openapi.json'):
            assert client.get(path, follow_redirects=False).status_code == 404
        assert app.openapi()['paths'] == {}
        assert call(client).status_code == 200
        assert call(client, 'private').status_code == 401


def test_token_issuance_requires_login_csrf_and_valid_scope(tmp_path):
    with client_for(service(tmp_path)) as client:
        assert client.post('/demo/auth/bff-token', json={'scope': 'public'}).status_code == 401
        headers = login(client)
        assert client.get('/demo/auth/bff-token').json()['authenticated'] is True
        assert client.post('/demo/auth/bff-token', json={'scope': 'session'}).status_code == 403
        assert client.post('/other/auth/bff-token', json={'scope': 'public'}, headers=headers).status_code == 403
        assert client.post('/demo/auth/bff-token', json={'scope': 'admin'}, headers=headers).status_code == 422
        assert client.post('/demo/auth/bff-token', json={'scope': 'public'}, headers={**headers, 'Origin': 'https://evil.test'}).status_code == 403


def test_bearer_tokens_are_app_scoped_expiring_and_public_tokens_cannot_escalate(tmp_path, monkeypatch):
    app = service(tmp_path)
    backend = app.state.pytincture_backend
    with client_for(app) as browser, client_for(app) as api:
        headers = login(browser)
        tokens = {}
        for scope in ('public', 'session'):
            response = browser.post('/demo/auth/bff-token', json={'scope': scope}, headers=headers)
            assert response.status_code == 200, response.text
            assert 'no-store' in response.headers['cache-control']
            assert 0 < response.json()['expires_in'] <= 900
            tokens[scope] = response.json()['access_token']
        assert call(api, token=tokens['public']).json() == {'value': 42}
        assert call(api, 'private', token=tokens['public']).status_code == 403
        assert call(api, 'private', token=tokens['session']).json() == {'private': 42}
        assert not api.cookies
        assert call(api, token=tokens['session'], application='other').status_code == 403
        assert call(api, token=tokens['session'] + 'broken').status_code == 401
        assert call(api, token=tokens['session'], headers={'Origin': 'https://evil.test'}).status_code == 403
        # A stale API token cannot fall back to an otherwise valid cookie.
        assert call(browser, token='invalid', headers=headers).status_code == 401
        with monkeypatch.context() as guard:
            guard.setattr(backend, 'ALLOWED_NOAUTH_CLASSCALLS', [])
            assert call(api, token=tokens['public']).status_code == 403
        with monkeypatch.context() as guard:
            guard.setattr(backend, '_configured_bff_policy_hook', lambda: lambda **kwargs: False)
            assert call(api, 'private', token=tokens['session']).status_code == 403
        with monkeypatch.context() as guard:
            guard.setattr(backend, 'AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS', -1)
            assert call(api, 'private', token=tokens['session']).status_code == 401
        with monkeypatch.context() as guard:
            guard.setattr(backend, 'ENABLE_BFF_API_TOKENS', False)
            assert call(api, token=tokens['public']).status_code == 401
            assert browser.post('/demo/auth/bff-token', json={'scope': 'public'}, headers=headers).status_code == 404
        monkeypatch.setattr(backend, 'ENABLE_BFF_REPLAY_TOKENS', True)
        assert call(api, 'private', token=tokens['session']).status_code == 200
        # Bearer credentials remain reusable; the optional browser proof remains independent.
        monkeypatch.setattr(backend, 'USE_REDIS_INSTANCE', 'true')
        monkeypatch.setattr(backend, '_session_is_revoked', lambda sid: True)
        assert call(api, 'private', token=tokens['session']).status_code == 401
        monkeypatch.setattr(backend, 'USE_REDIS_INSTANCE', 'false')
        import itsdangerous.timed
        clock = itsdangerous.timed.time.time
        monkeypatch.setattr(itsdangerous.timed.time, 'time', lambda: clock() + 901)
        assert call(api, token=tokens['public']).status_code == 401


def test_require_public_token_preserves_browser_calls_and_disabled_docs_do_not_disable_tokens(tmp_path):
    app = service(tmp_path, require_public_bff_token=True, api_docs_mode='disabled')
    with client_for(app) as browser, client_for(app) as api:
        assert call(api).status_code == 401
        assert browser.get('/demo/catalog/bff-docs').status_code == 404
        headers = login(browser)
        assert call(browser, headers=headers).status_code == 200
        assert call(browser).status_code == 403
        token = browser.post('/demo/auth/bff-token', json={'scope': 'public'}, headers=headers).json()['access_token']
        assert call(api, token=token).status_code == 200
        assert call(api, 'private', token=token).status_code == 403


def test_api_access_config_roundtrip_and_invalid_combinations(tmp_path):
    config = PytinctureConfig.from_env({'MODULES_PATH': str(tmp_path),
        'PYTINCTURE_API_DOCS_SCOPE': 'public', 'ENABLE_BFF_API_TOKENS': 'true'})
    assert config.api_docs_scope == 'public' and config.enable_bff_api_tokens
    assert config.to_environ()['ENABLE_BFF_API_TOKENS'] == 'true'
    with pytest.raises(ValueError, match='api_docs_scope'):
        PytinctureConfig(modules_path=str(tmp_path), api_docs_scope='unknown')
    with pytest.raises(ValueError, match='requires'):
        PytinctureConfig(modules_path=str(tmp_path), require_public_bff_token=True)


@pytest.mark.parametrize('include_session_methods_in_docs', [False, True])
def test_external_decorator_controls_docs_and_token_scope_without_allowlist(tmp_path, include_session_methods_in_docs, monkeypatch):
    app = service(tmp_path, decorated=True, include_session_methods_in_docs=include_session_methods_in_docs)
    backend = app.state.pytincture_backend
    assert backend.ALLOWED_NOAUTH_CLASSCALLS == []
    with client_for(app) as browser, client_for(app) as api:
        schema = browser.get('/demo/catalog/bff-docs/openapi.json').json()
        assert set(schema['paths']) == ({'/Catalog/public', '/Catalog/private'} if include_session_methods_in_docs else {'/Catalog/public'})
        assert schema['servers'] == [{'url': '/demo/classcall/catalog'}]
        public = schema['paths']['/Catalog/public']['post']
        assert public['x-bff-external'] is True
        assert public['x-bff-public'] is False
        assert {} not in public['security']
        assert call(api).status_code == 401
        headers = login(browser)
        assert call(browser, headers=headers).status_code == 200
        assert call(browser).status_code == 403
        assert call(browser, 'private', headers=headers).status_code == 200
        issued = browser.post('/demo/auth/bff-token', json={'scope': 'external'}, headers=headers)
        assert issued.status_code == 200, issued.text
        token = issued.json()['access_token']
        for module in ('catalog', 'catalog.py'):
            result = api.post(f'/demo/classcall/{module}/Catalog/public', json={'value': 42}, headers={'Authorization': f'Bearer {token}'})
            assert result.status_code == 200, result.text
        assert call(api, 'private', token=token).status_code == 403
        assert call(browser, 'private', token=token, headers=headers).status_code == 403
        # A decorator cannot silently become anonymous through a legacy grant.
        monkeypatch.setattr(backend, 'ALLOWED_NOAUTH_CLASSCALLS', [{'application': 'demo', 'file': 'catalog', 'class': 'Catalog', 'function': 'public'}])
        assert call(api).status_code == 401
        assert call(browser, headers=headers).status_code == 200
        monkeypatch.setattr(backend, 'ALLOWED_NOAUTH_CLASSCALLS', [])
        # Source changes revoke external access at the registry boundary before import.
        source = (tmp_path / 'catalog.py').read_text().replace('    @bff_external\n', '')
        (tmp_path / 'catalog.py').write_text(source)
        assert call(api, token=token).status_code == 403


def test_public_docs_scope_overrides_include_session_methods_in_docs(tmp_path):
    with client_for(service(tmp_path, decorated=True, include_session_methods_in_docs=True, api_docs_scope='public')) as client:
        schema = client.get('/demo/catalog/bff-docs/openapi.json').json()
        assert set(schema['paths']) == {'/Catalog/public'}


@pytest.mark.parametrize('development,override,includes_session', [
    (True, None, True), (False, None, False),
    (True, False, False), (False, True, True),
])
def test_session_docs_default_follows_development_mode_with_explicit_overrides(
    tmp_path, development, override, includes_session,
):
    app = service(tmp_path, decorated=True, development=development,
                  include_session_methods_in_docs=override)
    with TestClient(app, base_url='http://127.0.0.1' if development else 'https://app.example.test',
                    client=('127.0.0.1', 1234)) as client:
        response = client.get('/demo/catalog/bff-docs/openapi.json')
        assert response.status_code == 200
        assert ('/Catalog/private' in response.json()['paths']) is includes_session
        assert '/Catalog/public' in response.json()['paths']


def test_external_only_docs_override_inherited_development_default(tmp_path):
    app = service(tmp_path, decorated=True, include_session_methods_in_docs=None, api_docs_scope='public')
    with client_for(app) as client:
        schema = client.get('/demo/catalog/bff-docs/openapi.json').json()
        assert set(schema['paths']) == {'/Catalog/public'}


def test_development_email_login_also_enables_inherited_session_docs(tmp_path):
    from dataclasses import replace
    initial = service(tmp_path, decorated=True, include_session_methods_in_docs=None)
    app = create_app(replace(initial.state.pytincture_config,
        allow_development_auth_origin=False, enable_dev_email_login=True))
    with client_for(app) as client:
        schema = client.get('/demo/catalog/bff-docs/openapi.json').json()
        assert set(schema['paths']) == {'/Catalog/public', '/Catalog/private'}
