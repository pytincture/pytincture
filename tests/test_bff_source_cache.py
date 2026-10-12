"""Cached source work must not cache live BFF state or weaken verification."""
import hashlib
import os

import pytest

from pytincture.backend import source_loading
from pytincture.backend.safe_paths import UnsafePath
from pytincture.dataclass import get_bff_manifest, verify_bff_runtime_export


SOURCE = '''from pytincture.dataclass import backend_for_frontend
calls = []
@backend_for_frontend
class Data:
    values = []
    def read(self):
        calls.append(self._user)
        self.values.append(self._user)
        return calls, self.values
'''


def load(root, source=SOURCE):
    root = root.resolve()
    target = root / 'data.py'
    target.write_text(source)
    return target, source_loading.load_source_module(str(target), 'Data', str(root))


def test_warm_load_reexecutes_module_and_rebinds_class_attributes(tmp_path):
    path, first = load(tmp_path)
    operation = get_bff_manifest(str(path))[('Data', 'read')]
    verify_bff_runtime_export(first.Data, class_name='Data', member_name='read',
                              operation=operation, source_path=str(path))
    assert first.Data(_user='first').read() == (['first'], ['first'])
    second = source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()))
    assert second is not first and second.Data is not first.Data
    verify_bff_runtime_export(second.Data, class_name='Data', member_name='read',
                              operation=operation, source_path=str(path))
    assert second.Data(_user='second').read() == (['second'], ['second'])
    # Definition caching must not retain the previous class's live attributes.
    attribute = get_bff_manifest(str(path))[('Data', 'values')]
    verify_bff_runtime_export(second.Data, class_name='Data', member_name='values',
                              operation=attribute, source_path=str(path))
    second.Data._pytincture_bff_original.values = []
    with pytest.raises(ValueError, match='attribute'):
        verify_bff_runtime_export(second.Data, class_name='Data', member_name='values',
                                  operation=attribute, source_path=str(path))


def test_warm_compile_cache_still_verifies_digest_with_unchanged_metadata(tmp_path):
    path, first = load(tmp_path)
    previous = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    changed = SOURCE.replace('calls = []', 'calls = {}')
    assert len(changed) == len(SOURCE)
    path.write_text(changed)
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    with pytest.raises(ImportError, match='changed after registry'):
        source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()),
                                          expected_digest=digest)
    second = source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()))
    assert first.calls == [] and second.calls == {}


def test_warm_analysis_cannot_authorize_changed_definition(tmp_path):
    path, first = load(tmp_path)
    operation = get_bff_manifest(str(path))[('Data', 'read')]
    changed = SOURCE.replace('return calls, self.values', 'return self.values, calls')
    path.write_text(changed)
    second = source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()))
    with pytest.raises(ValueError, match='definition'):
        verify_bff_runtime_export(second.Data, class_name='Data', member_name='read',
                                  operation=operation, source_path=str(path))


def test_warm_load_does_not_bypass_removed_or_symlinked_source(tmp_path):
    path, _ = load(tmp_path)
    path.unlink()
    with pytest.raises(UnsafePath):
        source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()))
    outside = tmp_path.parent / (tmp_path.name + '-outside.py')
    outside.write_text(SOURCE)
    path.symlink_to(outside)
    with pytest.raises(UnsafePath):
        source_loading.load_source_module(str(path), 'Data', str(tmp_path.resolve()))


def test_large_source_is_not_retained_in_compile_cache(tmp_path):
    source_loading._cached_compile_source.cache_clear()
    load(tmp_path, SOURCE + '\n#' + 'x' * 70_000)
    assert source_loading._cached_compile_source.cache_info().currsize == 0


@pytest.mark.parametrize('revalidate', [True, False])
def test_warm_source_cache_never_caches_request_policy_decisions(tmp_path, revalidate):
    from fastapi.testclient import TestClient
    from pytincture import PytinctureConfig, create_app

    (tmp_path / 'demo.py').write_text('from data import Data\n')
    (tmp_path / 'data.py').write_text('''from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Data:
    def read(self): return "PRIVATE_RESULT"
''')
    app = create_app(PytinctureConfig(modules_path=str(tmp_path), bff_cache_revalidation=revalidate))
    decisions = iter([True, False, True])
    app.state.pytincture_backend.set_bff_policy_hook(lambda **kwargs: next(decisions))
    with TestClient(app) as client:
        first = client.post('/demo/classcall/data.py/Data/read', json={})
        denied = client.post('/demo/classcall/data.py/Data/read', json={})
        third = client.post('/demo/classcall/data.py/Data/read', json={})
    assert first.status_code == third.status_code == 200
    assert denied.status_code == 403
    assert 'PRIVATE_RESULT' not in denied.text
