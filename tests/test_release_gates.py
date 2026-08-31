import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_release_gates as gates
from versioning import npm_version_for_python


def evidence(version, tested_at="2026-02-05T00:00:00Z", **extra):
    return {
        "version": version,
        "tested_at": tested_at,
        "status": "passed",
        "evidence_url": "https://github.com/pytincture/pytincture/actions/runs/1",
        **extra,
    }


def candidate(version, published_at):
    return {
        "version": version,
        "published_at": published_at,
        "commit_sha": "a" * 40,
        "ci_url": "https://github.com/pytincture/pytincture/actions/runs/1",
        "artifacts": {"wheel": "1" * 64, "sdist": "2" * 64, "npm": "3" * 64},
        "status": "passed",
    }


def test_npm_publish_requires_attested_release_provenance():
    release_workflow = (
        ROOT / ".github" / "workflows" / "npm-publish.yml"
    ).read_text()
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "id-token: write" in release_workflow
    assert "NODE_AUTH_TOKEN" not in release_workflow
    assert "run-id:" in release_workflow
    assert "source_run_id:" not in release_workflow
    assert "expected_version:" not in release_workflow
    assert "release_tag:" in release_workflow
    assert "environment: npm" in release_workflow
    assert "gh attestation verify" in release_workflow
    assert "--signer-workflow" in release_workflow
    assert "--source-digest" in release_workflow
    assert "--source-ref" in release_workflow
    assert 'run: npm publish "$NPM_PATH"' in release_workflow
    assert "Attest exact release artifact bytes" in ci_workflow
    assert "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8" in ci_workflow
    assert "Publish validated npm artifact" not in ci_workflow

    action_refs = re.findall(r"^\s+uses:\s+[^@\s]+@([^\s#]+)", release_workflow, re.MULTILINE)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)

    run_blocks = re.findall(
        r"^\s+run:\s*\|\n(?P<body>(?:^\s{10,}.*\n)*)",
        release_workflow,
        re.MULTILINE,
    )
    assert run_blocks
    assert all("${{" not in block for block in run_blocks)


def test_python_publish_requires_oidc_protected_attested_release_provenance():
    release_workflow = (
        ROOT / ".github" / "workflows" / "pypi-publish.yml"
    ).read_text()
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "environment: pypi" in release_workflow
    assert "id-token: write" in release_workflow
    assert "run-id:" in release_workflow
    assert "release_tag:" in release_workflow
    assert "gh attestation verify" in release_workflow
    assert "--signer-workflow" in release_workflow
    assert "--source-digest" in release_workflow
    assert "--source-ref" in release_workflow
    assert "verify_python_release.py" in release_workflow
    assert (
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
        in release_workflow
    )
    assert "PYPI_PASSWORD" not in release_workflow
    assert "TWINE_PASSWORD" not in release_workflow
    assert "TWINE_USERNAME" not in release_workflow
    assert "python-release-artifacts" in ci_workflow
    assert "Publish validated Python artifacts" not in ci_workflow
    assert "PYPI_PASSWORD" not in ci_workflow

    action_refs = re.findall(
        r"^\s+uses:\s+[^@\s]+@([^\s#]+)", release_workflow, re.MULTILINE
    )
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)

    run_blocks = re.findall(
        r"^\s+run:\s*\|\n(?P<body>(?:^\s{10,}.*\n)*)",
        release_workflow,
        re.MULTILINE,
    )
    assert run_blocks
    assert all("${{" not in block for block in run_blocks)


def test_release_publishers_require_tags_reachable_from_default_branch():
    for workflow_name in ("npm-publish.yml", "pypi-publish.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
        assert "merge_base_commit.sha" in workflow
        assert '[[ "$merge_base" != "$tag_sha" ]]' in workflow
        assert "not reachable from the protected default branch" in workflow


def complete_record():
    version = "1.0.0rc2"
    return {
        "schema_version": 1,
        "minimum_observation_days": 30,
        "observation_started_at": "2026-01-02T00:00:00Z",
        "observation_approval_url": (
            "https://github.com/pytincture/pytincture/issues/143#approval"
        ),
        "release_candidates": [
            candidate("1.0.0rc1", "2026-01-01T00:00:00Z"),
            candidate(version, "2026-01-20T00:00:00Z"),
        ],
        "representative_applications": {
            "standalone": [evidence(version)],
            "authenticated_bff": [evidence(version)],
            "federated_auth": [evidence(version)],
        },
        "upgrade_exercises": [evidence(version)],
        "rollback_exercises": [evidence(version)],
        "performance_reviews": [evidence(version)],
        "repository_policy_reviews": [evidence(version)],
        "security_reviews": [evidence(version, open_critical=0, open_high=0)],
        "defect_audits": [evidence(version, open_p0=0, open_p1=0)],
        "final_decision": {
            "status": "go",
            "decided_at": "2026-02-05T00:00:00Z",
            "approvers": ["release-manager"],
            "notes": "All gates passed.",
        },
    }


@pytest.mark.parametrize(
    ("python_version", "npm_version"),
    [
        ("1.0.0", "1.0.0"),
        ("1.0.0rc1", "1.0.0-rc.1"),
        ("1.0.0a2", "1.0.0-alpha.2"),
        ("1.0.0b3", "1.0.0-beta.3"),
        ("1.0.0rc1.dev2", "1.0.0-rc.1.dev.2"),
        ("1.1.0.dev4", "1.1.0-dev.4"),
    ],
)
def test_python_to_npm_version_mapping(python_version, npm_version):
    assert npm_version_for_python(python_version) == npm_version


def test_unsupported_release_versions_are_rejected():
    with pytest.raises(ValueError):
        npm_version_for_python("1.0.0.post1")


def test_committed_qualification_template_passes_static_controls():
    record = json.loads((ROOT / "release" / "qualification.json").read_text())
    assert gates.validate_static(record) == []


def test_complete_two_rc_record_passes_final_gate():
    assert gates.validate_final(complete_record()) == []


def test_final_gate_rejects_short_observation_and_stale_latest_evidence():
    record = complete_record()
    record["final_decision"]["decided_at"] = "2026-01-25T00:00:00Z"
    record["representative_applications"]["standalone"][0]["version"] = "1.0.0rc1"
    failures = gates.validate_final(record)
    assert any("observation period" in failure for failure in failures)
    assert any("must qualify 1.0.0rc2" in failure for failure in failures)


def test_final_gate_measures_observation_from_explicit_approved_start():
    record = complete_record()
    record["observation_started_at"] = "2026-01-10T00:00:00Z"
    record["final_decision"]["decided_at"] = "2026-02-05T00:00:00Z"

    failures = gates.validate_final(record)

    assert any("observation period is 26.0 days" in failure for failure in failures)


def test_final_gate_rejects_evidence_recorded_before_latest_rc():
    record = complete_record()
    record["representative_applications"]["standalone"][0]["tested_at"] = (
        "2026-01-19T23:59:59Z"
    )
    failures = gates.validate_final(record)
    assert any("must not predate 1.0.0rc2 publication" in failure for failure in failures)


def test_final_gate_preserves_historical_rc_evidence():
    record = complete_record()
    record["performance_reviews"].insert(
        0,
        evidence("1.0.0rc1", tested_at="2026-01-02T00:00:00Z"),
    )

    assert gates.validate_final(record) == []


def test_rc2_release_requires_valid_rc1_evidence(monkeypatch):
    record = complete_record()
    record["release_candidates"] = []
    monkeypatch.setattr(gates, "source_versions", lambda: ("1.0.0rc2", "1.0.0-rc.2"))
    assert "1.0.0rc1 evidence is required before releasing 1.0.0rc2" in gates.validate_release_ref(
        record, "v1.0.0rc2"
    )
