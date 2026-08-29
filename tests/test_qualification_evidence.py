import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_qualification_evidence as qualification


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value) + "\n")


def _inputs(tmp_path: Path):
    artifact_contents = {
        "pytincture-1.0.0rc1-py3-none-any.whl": b"wheel fixture\n",
        "pytincture-1.0.0rc1.tar.gz": b"sdist fixture\n",
        "pytincture-runtime-1.0.0-rc.1.tgz": b"npm fixture\n",
    }
    artifact_hashes = {}
    for filename, contents in artifact_contents.items():
        artifact_path = tmp_path / filename
        artifact_path.write_bytes(contents)
        artifact_hashes[filename] = hashlib.sha256(contents).hexdigest()
    manifest = tmp_path / "SHA256SUMS.json"
    _write_json(
        manifest,
        {
            "python_version": "1.0.0rc1",
            "npm_version": "1.0.0-rc.1",
            "sha256": artifact_hashes,
        },
    )
    result = tmp_path / "acceptance.json"
    _write_json(result, {"status": "passed", "duration_ms": 123})
    return manifest, result


def _args(manifest: Path, result: Path, **overrides):
    values = {
        "exercise": "standalone",
        "status": "success",
        "artifact_manifest": manifest,
        "result": [f"acceptance={result}"],
        "output": manifest.parent / "evidence.json",
        "tested_at": "2026-08-29T01:00:00Z",
        "commit_sha": "a" * 40,
        "evidence_url": "https://github.com/pytincture/pytincture/actions/runs/10",
        "run_id": "10",
        "run_attempt": "2",
        "job": "standalone-wheel-e2e",
        "event": "push",
        "ref": "refs/heads/main",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_builds_portable_evidence_with_artifact_and_result_hashes(tmp_path):
    manifest, result = _inputs(tmp_path)
    evidence = qualification.build_evidence(_args(manifest, result), {})

    assert qualification.validate_evidence(evidence) == []
    assert evidence["status"] == "passed"
    assert evidence["version"] == "1.0.0rc1"
    assert evidence["run"]["attempt"] == 2
    for kind, filename in evidence["artifact_files"].items():
        assert evidence["artifact_sha256"][kind] == hashlib.sha256(
            (tmp_path / filename).read_bytes()
        ).hexdigest()
    assert evidence["result_sha256"]["acceptance"] == hashlib.sha256(
        result.read_bytes()
    ).hexdigest()


def test_uses_github_environment_for_durable_run_identity(tmp_path):
    manifest, result = _inputs(tmp_path)
    args = _args(
        manifest,
        result,
        commit_sha=None,
        evidence_url=None,
        run_id=None,
        run_attempt=None,
        job=None,
        event=None,
        ref=None,
    )
    environment = {
        "GITHUB_SHA": "b" * 40,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "pytincture/pytincture",
        "GITHUB_RUN_ID": "99",
        "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_JOB": "saml-federated-e2e",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/1/merge",
    }

    evidence = qualification.build_evidence(args, environment)

    assert evidence["commit_sha"] == "b" * 40
    assert evidence["evidence_url"].endswith("/actions/runs/99")
    assert evidence["run"] == {
        "id": "99",
        "attempt": 3,
        "job": "saml-federated-e2e",
        "event": "pull_request",
        "ref": "refs/pull/1/merge",
    }


def test_rejects_incomplete_artifact_manifest(tmp_path):
    manifest, result = _inputs(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["sha256"].pop("pytincture-runtime-1.0.0-rc.1.tgz")
    _write_json(manifest, payload)

    try:
        qualification.build_evidence(_args(manifest, result), {})
    except ValueError as exc:
        assert "missing: npm" in str(exc)
    else:
        raise AssertionError("incomplete artifact evidence was accepted")


def test_rejects_artifact_that_does_not_match_manifest(tmp_path):
    manifest, result = _inputs(tmp_path)
    (tmp_path / "pytincture-1.0.0rc1-py3-none-any.whl").write_bytes(b"changed")

    try:
        qualification.build_evidence(_args(manifest, result), {})
    except ValueError as exc:
        assert "artifact digest does not match" in str(exc)
    else:
        raise AssertionError("mismatched artifact evidence was accepted")


def test_rejects_passed_evidence_with_failed_result(tmp_path):
    manifest, result = _inputs(tmp_path)
    _write_json(result, {"status": "failed"})

    try:
        qualification.build_evidence(_args(manifest, result), {})
    except ValueError as exc:
        assert "contradicts passed evidence" in str(exc)
    else:
        raise AssertionError("contradictory evidence was accepted")


def test_failed_exercise_materializes_missing_raw_result(tmp_path):
    manifest, result = _inputs(tmp_path)
    result.unlink()

    evidence = qualification.build_evidence(
        _args(manifest, result, status="failure"), {}
    )

    assert evidence["status"] == "failed"
    assert evidence["results"]["acceptance"]["status"] == "failed"
    assert "not produced" in evidence["results"]["acceptance"]["error"]
    assert evidence["result_sha256"]["acceptance"] == hashlib.sha256(
        result.read_bytes()
    ).hexdigest()


def test_committed_schema_matches_generator_contract():
    schema = json.loads(
        (ROOT / "contracts" / "qualification-evidence-v1.schema.json").read_text()
    )
    assert schema["$id"] == qualification.SCHEMA_ID
    assert set(qualification.EXERCISES) == set(
        schema["properties"]["exercise"]["enum"]
    )


def test_ci_generates_standard_evidence_for_every_qualification_track():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for exercise in qualification.EXERCISES:
        assert f"--exercise {exercise}" in workflow
    assert workflow.count("scripts/build_qualification_evidence.py") == len(
        qualification.EXERCISES
    )
