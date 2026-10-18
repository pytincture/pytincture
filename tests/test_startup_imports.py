"""Fresh-process checks: test collection must not mask eager extra imports."""

import os
import secrets
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('entrypoint', ['factory', 'legacy'])
def test_disabled_extras_do_not_attempt_optional_imports(tmp_path, entrypoint):
    code = '''
import importlib.abc
import sys

blocked = ('fastmcp', 'authlib', 'httpx', 'onelogin', 'xmlsec', 'lxml')
class RejectOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('Disabled feature imported ' + fullname)
sys.meta_path.insert(0, RejectOptionalImports())
if sys.argv[1] == 'factory':
    from pytincture import create_app
    application = create_app()
else:
    from pytincture.backend.app import app as application
import asyncio
async def startup():
    async with application.router.lifespan_context(application):
        pass
asyncio.run(startup())
assert not any(name.split('.')[0] in blocked for name in sys.modules)
'''
    environment = os.environ.copy()
    environment.update({
        'MODULES_PATH': str(tmp_path),
        'ENABLE_MCP': 'false',
        'ENABLE_GOOGLE_AUTH': 'false',
        'ENABLE_MICROSOFT_AUTH': 'false',
        'ENABLE_SAML_AUTH': 'false',
        'ENABLE_USER_LOGIN': 'false',
        'ENABLE_DEV_EMAIL_LOGIN': 'false',
    })
    result = subprocess.run(
        [sys.executable, '-c', code, entrypoint],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('provider', ['google', 'microsoft'])
def test_enabled_oauth_loads_its_dependencies(tmp_path, provider):
    pytest.importorskip('authlib.integrations.starlette_client')
    from pytincture import PytinctureConfig, create_app
    application = create_app(PytinctureConfig(
        modules_path=str(tmp_path),
        **{f'enable_{provider}_auth': True,
           f'{provider}_client_id': 'test-client',
           f'{provider}_client_secret': 'test-secret'},
        microsoft_tenant_id='tenant-123',
        allowed_hosts=('app.example.test',),
        canonical_origin='https://app.example.test',
        session_secret=secrets.token_hex(32),
    ))
    backend = application.state.pytincture_backend
    from authlib.integrations.starlette_client import OAuth
    assert backend.OAuth is OAuth
    assert getattr(backend.oauth, provider) is not None
    assert backend.FastMCP is None
    timeout = backend._oauth_client_kwargs('openid')['timeout']
    assert timeout.connect == backend.OAUTH_CONNECT_TIMEOUT_SECONDS
    assert timeout.read == backend.OAUTH_READ_TIMEOUT_SECONDS
    assert timeout.write == backend.OAUTH_WRITE_TIMEOUT_SECONDS
    assert timeout.pool == backend.OAUTH_POOL_TIMEOUT_SECONDS


def test_enabled_saml_loads_its_dependencies(tmp_path):
    saml_auth = pytest.importorskip('onelogin.saml2.auth')
    from pytincture import PytinctureConfig, create_app
    application = create_app(PytinctureConfig(
        modules_path=str(tmp_path), enable_saml_auth=True,
        allowed_hosts=('app.example.test',),
        canonical_origin='https://app.example.test',
        session_secret=secrets.token_hex(32),
        saml_idp_entity_id='https://idp.example.test/metadata',
        saml_idp_sso_url='https://idp.example.test/sso',
        saml_idp_x509_cert='test-certificate',
    ))
    backend = application.state.pytincture_backend
    assert backend.OneLogin_Saml2_Auth is saml_auth.OneLogin_Saml2_Auth
    assert backend.OneLogin_Saml2_Settings is not None
    assert backend.FastMCP is None
    assert backend.OAuth is None


def test_package_import_does_not_load_server_or_desktop_launcher():
    result = subprocess.run(
        [sys.executable, '-c',
         'import pytincture,sys; '
         'assert not {"fastapi","uvicorn","uvloop","multiprocessing"}.intersection(sys.modules)'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
