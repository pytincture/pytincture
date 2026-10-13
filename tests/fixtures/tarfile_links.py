"""Archive behavior exercised on the actual vendored module and in Pyodide."""

import io
import json
import os
from pathlib import Path
import tempfile


def check_tarfile_links(tarfile):
    cases = 0
    for filter_name in ('data', 'tar'):
        for compressed in (False, True):
            for selected_only in (False, True):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / 'extracted'
                    destination.mkdir()
                    outside = root / 'escape'
                    outside.write_text('outside sentinel')
                    os.chmod(outside, 0o600)
                    os.utime(outside, (123456789, 123456789))
                    before = outside.stat()
                    data = io.BytesIO()
                    with tarfile.open(fileobj=data, mode='w:gz' if compressed else 'w') as archive:
                        member = tarfile.TarInfo('a/escape')
                        member.size = 5
                        archive.addfile(member, io.BytesIO(b'decoy'))
                        member = tarfile.TarInfo('a/b/s')
                        member.type = tarfile.SYMTYPE
                        member.linkname = '../escape'
                        archive.addfile(member)
                        member = tarfile.TarInfo('s')
                        member.type = tarfile.LNKTYPE
                        member.linkname = 'a/b/s'
                        archive.addfile(member)
                        member = tarfile.TarInfo('chain')
                        member.type = tarfile.LNKTYPE
                        member.linkname = 's'
                        archive.addfile(member)
                        member = tarfile.TarInfo('ordinary-link')
                        member.type = tarfile.SYMTYPE
                        member.linkname = 'a/escape'
                        archive.addfile(member)
                    data.seek(0)
                    with tarfile.open(fileobj=data) as archive:
                        members = [archive.getmember('s'), archive.getmember('chain')] if selected_only else None
                        archive.extractall(destination, members=members, filter=filter_name)
                    for name in ('s', 'chain'):
                        assert (destination / name).read_bytes() == b'decoy'
                        assert not (destination / name).is_symlink()
                    if not selected_only:
                        assert (destination / 'ordinary-link').is_symlink()
                        assert (destination / 'ordinary-link').read_bytes() == b'decoy'
                    assert outside.read_text() == 'outside sentinel'
                    after = outside.stat()
                    assert (after.st_mode, after.st_mtime) == (before.st_mode, before.st_mtime)
                    cases += 1
    # Ordinary streaming extraction must remain supported without seeking.
    with tempfile.TemporaryDirectory() as directory:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as archive:
            member = tarfile.TarInfo('regular.txt')
            member.size = 5
            archive.addfile(member, io.BytesIO(b'hello'))
        data.seek(0)
        with tarfile.open(fileobj=data, mode='r|') as archive:
            archive.extractall(directory, filter='data')
        assert (Path(directory) / 'regular.txt').read_text() == 'hello'
    return {'link_cases': cases, 'streaming': True, 'native_hardlinks': hasattr(os, 'link')}
