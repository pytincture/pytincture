import io
import json
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_npm_release import (
    ReleaseVerificationError,
    verify_npm_artifact,
    verify_release_metadata,
)


REPOSITORY = "pytincture/pytincture"
TAG = "v1.0.0rc2"
SHA = "a" * 40


def release_metadata(**overrides):
    return {
        "tag_name": TAG,
        "draft": False,
        "published_at": "2026-09-01T00:00:00Z",
        **overrides,
    }


def run_metadata(**overrides):
    return {
        "id": 123,
        "repository": {"full_name": REPOSITORY},
        "path": ".github/workflows/ci.yml",
        "event": "release",
        "status": "completed",
        "conclusion": "success",
        "head_branch": TAG,
        "head_sha": SHA,
        **overrides,
    }


def write_npm_artifact(
    root: Path,
    version="1.0.0-rc.2",
    name="@pytincture/runtime",
    extra_member: tarfile.TarInfo | None = None,
) -> Path:
    path = root / f"pytincture-runtime-{version}.tgz"
    package_json = json.dumps({"name": name, "version": version}).encode()
    member = tarfile.TarInfo("package/package.json")
    member.size = len(package_json)
    with tarfile.open(path, mode="w:gz") as package:
        package.addfile(member, io.BytesIO(package_json))
        if extra_member is not None:
            package.addfile(extra_member)
    return path


def test_release_metadata_requires_exact_published_release_run():
    assert verify_release_metadata(
        repository=REPOSITORY,
        release_tag=TAG,
        tag_sha=SHA,
        release=release_metadata(),
        run=run_metadata(),
    ) == ("1.0.0rc2", "1.0.0-rc.2")


@pytest.mark.parametrize(
    ("release", "run", "message"),
    [
        (release_metadata(draft=True), run_metadata(), "must not be a draft"),
        (release_metadata(), run_metadata(event="pull_request"), "release-triggered"),
        (release_metadata(), run_metadata(conclusion="failure"), "did not succeed"),
        (release_metadata(), run_metadata(head_branch="main"), "tag does not match"),
        (release_metadata(), run_metadata(head_sha="b" * 40), "commit does not match"),
        (
            release_metadata(),
            run_metadata(path=".github/workflows/untrusted.yml"),
            "unexpected signer workflow",
        ),
        (
            release_metadata(),
            run_metadata(repository={"full_name": "attacker/fork"}),
            "different repository",
        ),
    ],
)
def test_release_metadata_rejects_untrusted_provenance(release, run, message):
    with pytest.raises(ReleaseVerificationError, match=message):
        verify_release_metadata(
            repository=REPOSITORY,
            release_tag=TAG,
            tag_sha=SHA,
            release=release,
            run=run,
        )


@pytest.mark.parametrize(
    "tag",
    ["1.0.0rc2", "v1.0", "v01.0.0", "v1.0.0.post1", "v1.0.0;echo bad"],
)
def test_release_metadata_rejects_invalid_tags(tag):
    with pytest.raises(ReleaseVerificationError, match="supported Pytincture version"):
        verify_release_metadata(
            repository=REPOSITORY,
            release_tag=tag,
            tag_sha=SHA,
            release=release_metadata(tag_name=tag),
            run=run_metadata(head_branch=tag),
        )


def test_npm_artifact_requires_exact_name_and_version(tmp_path):
    artifact = write_npm_artifact(tmp_path)
    assert verify_npm_artifact(tmp_path, "1.0.0-rc.2") == artifact.resolve()


def test_npm_artifact_rejects_wrong_package_identity(tmp_path):
    write_npm_artifact(tmp_path, name="attacker/package")
    with pytest.raises(ReleaseVerificationError, match="package name"):
        verify_npm_artifact(tmp_path, "1.0.0-rc.2")


def test_npm_artifact_rejects_multiple_tarballs(tmp_path):
    write_npm_artifact(tmp_path)
    write_npm_artifact(tmp_path, version="1.0.0-rc.3")
    with pytest.raises(ReleaseVerificationError, match="exactly one"):
        verify_npm_artifact(tmp_path, "1.0.0-rc.2")


def test_npm_artifact_rejects_special_archive_members(tmp_path):
    link = tarfile.TarInfo("package/link")
    link.type = tarfile.LNKTYPE
    link.linkname = "package/package.json"
    write_npm_artifact(tmp_path, extra_member=link)

    with pytest.raises(ReleaseVerificationError, match="regular file or directory"):
        verify_npm_artifact(tmp_path, "1.0.0-rc.2")


def test_npm_artifact_rejects_filename_version_mismatch(tmp_path):
    write_npm_artifact(tmp_path, version="1.0.0-rc.3")
    with pytest.raises(ReleaseVerificationError, match="filename"):
        verify_npm_artifact(tmp_path, "1.0.0-rc.2")


def test_npm_artifact_rejects_internal_version_mismatch(tmp_path):
    artifact = write_npm_artifact(tmp_path, version="1.0.0-rc.3")
    artifact.rename(tmp_path / "pytincture-runtime-1.0.0-rc.2.tgz")
    with pytest.raises(ReleaseVerificationError, match="package version"):
        verify_npm_artifact(tmp_path, "1.0.0-rc.2")
