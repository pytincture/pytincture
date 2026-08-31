import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_npm_release import ReleaseVerificationError
from verify_python_release import verify_python_artifacts


VERSION = "1.0.0rc2"


def _metadata(name: str = "pytincture", version: str = VERSION) -> bytes:
    return f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n".encode()


def _write_python_artifacts(
    root: Path,
    *,
    metadata_name: str = "pytincture",
    metadata_version: str = VERSION,
    filename_version: str = VERSION,
) -> tuple[Path, Path]:
    wheel = root / f"pytincture-{filename_version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(
            f"pytincture-{filename_version}.dist-info/METADATA",
            _metadata(metadata_name, metadata_version),
        )

    sdist = root / f"pytincture-{filename_version}.tar.gz"
    content = _metadata(metadata_name, metadata_version)
    member = tarfile.TarInfo(f"pytincture-{filename_version}/PKG-INFO")
    member.size = len(content)
    with tarfile.open(sdist, mode="w:gz") as archive:
        archive.addfile(member, io.BytesIO(content))

    manifest = {
        "python_version": VERSION,
        "sha256": {
            wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest(),
            sdist.name: hashlib.sha256(sdist.read_bytes()).hexdigest(),
        },
    }
    (root / "SHA256SUMS.json").write_text(json.dumps(manifest), encoding="utf-8")
    return wheel, sdist


def test_python_artifacts_require_exact_identity_version_and_hashes(tmp_path):
    wheel, sdist = _write_python_artifacts(tmp_path)

    assert verify_python_artifacts(tmp_path, VERSION) == (
        wheel.resolve(),
        sdist.resolve(),
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"metadata_name": "attacker-package"}, "project name"),
        ({"metadata_version": "1.0.0rc3"}, "version does not match"),
        ({"filename_version": "1.0.0rc3"}, "filename"),
    ],
)
def test_python_artifacts_reject_wrong_identity_or_version(tmp_path, overrides, message):
    _write_python_artifacts(tmp_path, **overrides)

    with pytest.raises(ReleaseVerificationError, match=message):
        verify_python_artifacts(tmp_path, VERSION)


def test_python_artifacts_reject_hash_mismatch(tmp_path):
    wheel, _sdist = _write_python_artifacts(tmp_path)
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    with pytest.raises(ReleaseVerificationError, match="hash manifest mismatch"):
        verify_python_artifacts(tmp_path, VERSION)


def test_python_artifacts_reject_multiple_distributions(tmp_path):
    _write_python_artifacts(tmp_path)
    (tmp_path / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")

    with pytest.raises(ReleaseVerificationError, match="exactly one"):
        verify_python_artifacts(tmp_path, VERSION)


def test_python_artifacts_reject_symlinked_distribution(tmp_path):
    wheel, _sdist = _write_python_artifacts(tmp_path)
    target = tmp_path / "retained.bin"
    wheel.rename(target)
    wheel.symlink_to(target.name)

    with pytest.raises(ReleaseVerificationError, match="regular file"):
        verify_python_artifacts(tmp_path, VERSION)
