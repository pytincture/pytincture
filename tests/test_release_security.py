import json
import re
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_vendored_pyodide_sbom as sbom_generator
import inspect_release_artifacts as release_artifacts
import scan_repository_secrets as secret_scanner
from archive_safety import (
    ArchiveSafetyError,
    validate_tar_members,
    validate_zip_members,
)


def test_release_contract_is_an_exact_inventory_and_rejects_extra_files():
    contract = json.loads(
        (ROOT / "contracts" / "release-artifacts-v1.json").read_text(encoding="utf-8")
    )
    assert contract["schema_version"] == 2
    assert set(contract["python"]) >= {
        "wheel_inventory",
        "sdist_inventory",
        "base_dependencies",
        "extras",
    }
    assert set(contract["npm"]) == {"inventory"}
    with pytest.raises(SystemExit, match="unexpected files: injected.py"):
        release_artifacts._check_contents(
            "test artifact", {"expected.py", "injected.py"}, ["expected.py"], "1.0.0"
        )


def test_release_inventory_rejects_sensitive_files_even_if_declared():
    with pytest.raises(SystemExit, match="sensitive files"):
        release_artifacts._check_contents(
            "test artifact", {"config.env", "private.pem"}, ["config.env", "private.pem"], "1.0.0"
        )


@pytest.mark.parametrize(
    "member_type",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
    ],
)
def test_release_tar_validation_rejects_links_devices_and_fifos(member_type):
    member = tarfile.TarInfo("package/untrusted")
    member.type = member_type
    member.linkname = "package/target"

    with pytest.raises(ArchiveSafetyError, match="not a regular file or directory"):
        validate_tar_members([member])


@pytest.mark.parametrize(
    "names",
    [
        ["package/file", "package/file"],
        ["package/file", "../escape"],
        ["package/file", "./package/malformed"],
        ["package/file", "package//malformed"],
    ],
)
def test_release_tar_validation_rejects_duplicates_escapes_and_malformed_names(
    names,
):
    members = [tarfile.TarInfo(name) for name in names]

    with pytest.raises(ArchiveSafetyError):
        validate_tar_members(members)


def test_release_wheel_validation_rejects_symlink_duplicate_and_escape_members():
    symlink = zipfile.ZipInfo("package/link")
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ArchiveSafetyError, match="not a regular file or directory"):
        validate_zip_members([symlink])

    duplicate = [zipfile.ZipInfo("package/file"), zipfile.ZipInfo("package/file")]
    with pytest.raises(ArchiveSafetyError, match="duplicate"):
        validate_zip_members(duplicate)

    with pytest.raises(ArchiveSafetyError, match="unsafe path"):
        validate_zip_members([zipfile.ZipInfo("../escape")])


def test_vendored_sbom_is_generated_and_covers_complete_pyodide_catalog():
    sbom_path = ROOT / "pytincture" / "frontend" / "pyodide" / "0.29.3" / "sbom.json"
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom == sbom_generator.build_sbom()
    lock = json.loads(
        (sbom_path.parent / "full" / "pyodide-lock.json").read_text(encoding="utf-8")
    )
    catalog = {
        item["name"].removeprefix("pyodide-index:")
        for item in sbom["packages"]
        if item["name"].startswith("pyodide-index:")
    }
    assert catalog == set(lock["packages"]) - {"micropip"}
    assert {item["name"] for item in sbom["packages"]} >= {
        "packaging (embedded in micropip)",
        "mousebender (embedded in micropip)",
    }


def test_upstream_pyodide_manifest_pins_official_release_archive():
    manifest = json.loads(
        (ROOT / "security" / "pyodide-upstream.json").read_text(encoding="utf-8")
    )
    assert manifest["pyodide_version"] == "0.29.3"
    assert manifest["source"]["url"].startswith(
        "https://github.com/pyodide/pyodide/releases/download/0.29.3/"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["source"]["sha256"])
    assert set(manifest["exact_core_files"]) == {
        "pyodide.js",
        "pyodide.asm.js",
        "pyodide.asm.wasm",
        "python_stdlib.zip",
    }


def test_secret_scanner_detects_seeded_credentials_without_returning_values(tmp_path):
    seeded = tmp_path / "seeded.txt"
    seeded.write_text(
        "aws=" + "AKIA" + "A" * 16 + "\n"
        "github=" + "ghp_" + "b" * 36 + "\n"
        "-----BEGIN " + "PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    findings = secret_scanner.scan_paths([seeded])
    assert {finding.rule for finding in findings} == {
        "aws-access-key",
        "github-token",
        "private-key",
    }
    assert all(not hasattr(finding, "value") for finding in findings)


def test_uv_lock_covers_registry_inputs_with_sha256_hashes():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    registry_packages = [
        package for package in lock["package"] if "registry" in package.get("source", {})
    ]
    assert registry_packages
    for package in registry_packages:
        distributions = ([package["sdist"]] if "sdist" in package else []) + package.get(
            "wheels", []
        )
        assert distributions, package["name"]
        assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", item["hash"]) for item in distributions)


def test_ci_uses_frozen_python_inputs_and_release_security_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv lock --check" in workflow
    assert "uv sync --frozen" in workflow
    assert "uv run --frozen" in workflow
    assert "install_locked_artifact.py" in workflow
    assert "validate_vendored_pyodide.py --verify-upstream" in workflow
    assert "gitleaks_8.30.1_linux_x64.tar.gz" in workflow
    assert "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in workflow
    assert "/tmp/gitleaks git --redact --no-banner --exit-code 1 ." in workflow
    assert "scan_repository_secrets.py" in workflow
    assert "npm ci --ignore-scripts" in workflow
    assert "\n          npm ci\n" not in workflow


def test_release_reproducibility_uses_an_independent_clean_build():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "git archive --format=tar HEAD" in workflow
    assert "cd .reproducible-source" in workflow
    assert workflow.count("uv sync --frozen --extra dev") >= 2
    assert workflow.count("npm ci --ignore-scripts") >= 2
    assert "inspect_release_artifacts.py" in workflow
    assert "cmp dist/*.whl .reproducible-source/dist/*.whl" in workflow
    assert "cmp dist/*.tar.gz .reproducible-source/dist/*.tar.gz" in workflow
    assert "cmp dist/*.tgz .reproducible-source/dist/*.tgz" in workflow


def test_external_security_controls_are_explicit_and_not_runtime_state():
    controls = json.loads(
        (ROOT / "security" / "external-controls.json").read_text(encoding="utf-8")
    )

    assert controls["framework_controls"] == {
        "repository_policy_contract_requires_secret_scan": True,
        "complete_builtin_widget_wheel_digest_verified": True,
        "self_hosted_verified_pyodide_is_default": True,
        "external_pyodide_requires_explicit_unsafe_opt_in": True,
        "npm_install_lifecycle_scripts_disabled_in_ci": True,
        "independent_clean_frozen_release_rebuilds_compared": True,
        "redis_required": False,
        "sticky_routing_required": False,
    }
    assert controls["remaining_external_controls"]
    assert all(
        item["framework_cannot_self_attest"] is True
        for item in controls["remaining_external_controls"]
    )


def test_docs_do_not_recommend_an_unsupported_mutable_container_image():
    documentation = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in documentation)

    assert "pytincture/pytincture:latest" not in combined.casefold()
    assert re.search(
        r"\bdocker\s+(?:run|pull)\b[^\n]*:latest\b",
        combined,
        flags=re.IGNORECASE,
    ) is None
    assert "does not currently publish an official container image" in combined


def test_frozen_lock_has_no_drift():
    result = subprocess.run(
        ["uv", "lock", "--check"], cwd=ROOT, check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
