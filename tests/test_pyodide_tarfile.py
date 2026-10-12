import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from validate_vendored_pyodide import _validate_stdlib
from patch_vendored_pyodide import patched_module


def vendored_tarfile():
    with zipfile.ZipFile(ROOT / 'pytincture/frontend/pyodide/0.29.3/full/python_stdlib.zip') as archive:
        source = archive.read('tarfile.py')
    module = types.ModuleType('vendored_tarfile')
    exec(compile(source, 'vendored/tarfile.py', 'exec'), module.__dict__)
    return module


@pytest.mark.parametrize('native_links', [False, True])
def test_vendored_tarfile_keeps_links_inside_destination(monkeypatch, native_links):
    module = vendored_tarfile()
    if not native_links:
        monkeypatch.delattr(module.os, 'link', raising=False)
    spec = importlib.util.spec_from_file_location('tarfile_links', ROOT / 'tests/fixtures/tarfile_links.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    assert checks.check_tarfile_links(module)['link_cases'] == 8


@pytest.mark.parametrize('kind', ['cycle', 'directory'])
def test_vendored_tarfile_rejects_non_file_fallbacks(tmp_path, monkeypatch, kind):
    module = vendored_tarfile()
    monkeypatch.delattr(module.os, 'link', raising=False)
    data = io.BytesIO()
    with module.open(fileobj=data, mode='w') as archive:
        source = module.TarInfo('source')
        source.type = module.SYMTYPE if kind == 'cycle' else module.DIRTYPE
        source.linkname = 'source'
        archive.addfile(source)
        link = module.TarInfo('link')
        link.type = module.LNKTYPE
        link.linkname = 'source'
        archive.addfile(link)
    data.seek(0)
    with module.open(fileobj=data, errorlevel=2) as archive:
        with pytest.raises(module.ExtractError, match='cyclic|not a regular file'):
            archive.extract('link', tmp_path, filter='data')


def test_stdlib_verifier_rejects_changes_outside_documented_patch():
    # Use the actual pinned source/patch while keeping the test archive small.
    manifest = json.loads((ROOT / 'security/pyodide-upstream.json').read_text())
    patch = manifest['stdlib_patch']
    import subprocess
    import tempfile
    with zipfile.ZipFile(ROOT / 'pytincture/frontend/pyodide/0.29.3/full/python_stdlib.zip') as archive:
        fixed = archive.read('tarfile.py')
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'tarfile.py'
        source.write_bytes(fixed)
        subprocess.run(['git', 'apply', '--reverse', '--no-index', str(ROOT / patch['patch'])], cwd=directory, check=True)
        original = source.read_bytes()
    assert patched_module(original, patch) == fixed

    def zip_bytes(module, other=b'original'):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('tarfile.py', module)
            archive.writestr('other.py', other)
        return output.getvalue()

    upstream = zip_bytes(original)
    _validate_stdlib(upstream, zip_bytes(fixed), patch)
    with pytest.raises(SystemExit, match='other.py'):
        _validate_stdlib(upstream, zip_bytes(fixed, b'tampered'), patch)
    with pytest.raises(SystemExit, match='tarfile.py'):
        _validate_stdlib(upstream, zip_bytes(original), patch)
