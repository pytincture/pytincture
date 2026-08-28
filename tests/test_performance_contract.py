import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_performance_budget_contract_covers_roadmap_targets():
    contract = json.loads(
        (ROOT / "contracts" / "performance-budgets-v1.json").read_text()
    )
    assert contract["schema_version"] == 1
    browser = contract["browser"]
    service = contract["service"]
    assert {
        "cold_authenticated_start_ms",
        "warm_authenticated_start_ms",
        "authenticated_bff_p95_ms",
        "bff_samples",
    }.issubset(browser)
    for name in ("health", "appcode", "bff"):
        assert service[f"{name}_p95_ms"] > 0
        assert service[f"{name}_requests"] > 0
        assert service[f"{name}_concurrency"] > 0


def test_ci_runs_and_retains_every_performance_profile():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "python scripts/run_performance_smoke.py" in workflow
    assert "pytincture-performance-service" in workflow
    assert "pytincture-performance-${{ matrix.browser }}" in workflow


def test_performance_documentation_links_the_versioned_contract():
    documentation = (ROOT / "docs" / "performance.md").read_text()
    assert "contracts/performance-budgets-v1.json" in documentation
