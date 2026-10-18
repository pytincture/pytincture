"""Bytecode preparation preserves source checks and runtime semantics."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from pytincture.lambda_build import prepare_lambda


def staging(tmp_path):
    root=tmp_path/'staging'
    modules=root/'apps'
    modules.mkdir(parents=True)
    (modules/'demo.py').write_text('value = 1\n')
    return root,modules,root/'private'/'bff.json'


def test_preparation_compiles_checked_hash_bytecode_and_keeps_source(tmp_path):
    root,modules,manifest=staging(tmp_path)
    dependency=root/'dependency.py'
    dependency.write_text('assert True\n__doc__ = "keep me"\n')
    report=prepare_lambda(root,modules_path=modules,manifest_path=manifest)
    assert report['compiled_files']==2
    assert manifest.is_file()
    for source in (modules/'demo.py',dependency):
        assert source.is_file()
        bytecode=Path(importlib.util.cache_from_source(str(source)))
        assert struct.unpack('<I',bytecode.read_bytes()[4:8])[0]==3
    # Same size and mtime: checked hashes must still invalidate the cache.
    source=modules/'demo.py'
    before=source.stat()
    source.write_text('value = 2\n')
    os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
    environment=os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE']='1'
    result=subprocess.run([sys.executable,'-c','import demo; print(demo.value)'],
                          cwd=modules,env=environment,capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='2'


def test_preparation_skips_symlinks_outside_staging(tmp_path):
    root,modules,manifest=staging(tmp_path)
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/'bad.py').write_text('this is not valid python!')
    (root/'linked').symlink_to(outside,target_is_directory=True)
    (root/'linked.py').symlink_to(outside/'bad.py')
    assert prepare_lambda(root,modules_path=modules,manifest_path=manifest)['compiled_files']==1
    assert not (outside/'__pycache__').exists()


def test_preparation_fails_on_invalid_dependency_source(tmp_path):
    root,modules,manifest=staging(tmp_path)
    (root/'broken.py').write_text('def broken(')
    with pytest.raises(ValueError,match='Unable to compile'):
        prepare_lambda(root,modules_path=modules,manifest_path=manifest)


def test_preparation_rejects_manifest_in_browser_modules(tmp_path):
    root,modules,manifest=staging(tmp_path)
    with pytest.raises(ValueError,match='outside application modules'):
        prepare_lambda(root,modules_path=modules,manifest_path=modules/'bff.json')


def test_preparation_rejects_external_modules(tmp_path):
    root,modules,manifest=staging(tmp_path)
    with pytest.raises(ValueError,match='within the deployment root'):
        prepare_lambda(root,modules_path=tmp_path,manifest_path=manifest)
