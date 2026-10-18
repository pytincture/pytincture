"""Startup may reuse parsing, but must still detect source and policy changes."""
import os

import pytest
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app
from pytincture.backend import bff


SOURCE = '''from pytincture.dataclass import backend_for_frontend, bff_policy
@backend_for_frontend
class Data:
    def read(self): return True
'''
RESTRICTED = SOURCE.replace('    def read', '    @bff_policy(role="admin")\n    def read')


def test_refresh_reuses_only_identical_source_manifests(tmp_path, monkeypatch):
    calls = []
    original = bff.get_bff_manifest

    def tracked(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(bff, 'get_bff_manifest', tracked)
    source = tmp_path / 'data.py'
    source.write_text(SOURCE)
    (tmp_path / 'empty.py').write_text('# no exports')
    registry = bff.BFFRegistry(str(tmp_path), tracked)
    assert len(calls) == 2
    assert registry.refresh(str(tmp_path)) == registry.operations
    assert len(calls) == 2

    # Byte comparison must win even if metadata is preserved.
    before = source.stat()
    source.write_text(SOURCE.replace('True', 'None'))
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    registry.refresh(str(tmp_path))
    assert len(calls) == 3

    source.unlink()
    (tmp_path / 'new.py').write_text(RESTRICTED)
    registry.refresh(str(tmp_path))
    assert len(calls) == 4
    assert ('data.py', 'Data', 'read') not in registry.operations
    assert registry.operations[('new.py', 'Data', 'read')]['policy']
    assert 'data.py' not in registry._manifest_cache

    registry.reload()
    assert len(calls) == 6  # Explicit reload continues to force parsing.


def test_refresh_drops_invalid_exports_and_recovers(tmp_path):
    source = tmp_path / 'data.py'
    source.write_text(SOURCE)
    registry = bff.BFFRegistry(str(tmp_path))
    source.write_text('def broken(')
    assert registry.refresh(str(tmp_path)) == {}
    assert registry.failures == {'data.py': 'invalid_python_syntax'}
    source.write_text(RESTRICTED)
    assert registry.refresh(str(tmp_path))[('data.py', 'Data', 'read')]['policy']
    assert registry.failures == {}


@pytest.mark.parametrize('edit', ['unchanged', 'modified', 'added'])
def test_startup_rejects_policy_exports_without_hook(tmp_path, edit):
    source = tmp_path / 'data.py'
    source.write_text(RESTRICTED if edit == 'unchanged' else SOURCE)
    application = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    if edit == 'modified':
        source.write_text(RESTRICTED)
    elif edit == 'added':
        (tmp_path / 'new.py').write_text(RESTRICTED)
    with pytest.raises(RuntimeError, match='@bff_policy exports require'):
        with TestClient(application):
            pass


def test_startup_accepts_policy_exports_with_installed_hook(tmp_path):
    (tmp_path / 'data.py').write_text(RESTRICTED)
    application = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    application.state.pytincture_backend.set_bff_policy_hook(lambda **kwargs: None)
    with TestClient(application) as client:
        assert client.get('/healthz').status_code == 200
