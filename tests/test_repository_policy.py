import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from repository_policy import expected_payload, validate_policy


def contract():
    return json.loads((ROOT / "contracts" / "repository-policy-v1.json").read_text())


def protected_policy(profile="release"):
    policy = contract()
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": policy["profiles"][profile]["required_checks"],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "require_code_owner_reviews": True,
            "dismiss_stale_reviews": True,
            "require_last_push_approval": True,
        },
        "required_conversation_resolution": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }


def test_release_repository_policy_covers_every_publish_dependency():
    policy = contract()
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for context in policy["profiles"]["release"]["required_checks"]:
        if context.startswith("Python "):
            assert "Python ${{ matrix.python-version }} tests" in workflow
        elif context.startswith("Clean install ("):
            assert "Clean install (${{ matrix.extra }})" in workflow
        elif context.startswith("Pyodide E2E ("):
            assert "Pyodide E2E (${{ matrix.browser }})" in workflow
        elif context.startswith("Quickstart examples ("):
            assert "Quickstart examples (${{ matrix.browser }})" in workflow
        else:
            assert context in workflow


def test_every_repository_policy_profile_requires_secret_scan():
    policy = contract()

    assert all(
        "Repository secret scan" in profile["required_checks"]
        for profile in policy["profiles"].values()
    )


def test_repository_policy_accepts_the_complete_release_profile():
    assert validate_policy(protected_policy(), contract(), "release") == []


def test_repository_policy_rejects_missing_checks_and_admin_bypass():
    actual = protected_policy()
    actual["required_status_checks"]["contexts"].pop()
    actual["enforce_admins"]["enabled"] = False
    actual["required_pull_request_reviews"] = None
    failures = validate_policy(actual, contract(), "release")
    assert any("required status checks are missing" in failure for failure in failures)
    assert any("enforce_admins" in failure for failure in failures)
    assert any("require_code_owner_reviews" in failure for failure in failures)


def test_apply_payload_uses_the_versioned_profile():
    policy = contract()
    payload = expected_payload(policy, "bootstrap")
    assert payload["required_status_checks"]["contexts"] == (
        policy["profiles"]["bootstrap"]["required_checks"]
    )
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"] == {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": True,
        "required_approving_review_count": 1,
        "require_last_push_approval": True,
    }
