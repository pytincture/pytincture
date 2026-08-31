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
    for name in (
        "health",
        "appcode",
        "public_asset_head",
        "public_asset",
        "bff",
    ):
        assert service[f"{name}_p95_ms"] > 0
        assert service[f"{name}_requests"] > 0
        assert service[f"{name}_concurrency"] > 0
    assert service["saturation_requests"] > 0
    assert service["saturation_concurrency"] > 0
    assert service["saturation_min_rejections"] > 0
    assert service["saturation_recovery_ms"] > 0


def test_ci_runs_and_retains_every_performance_profile():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "python scripts/run_performance_smoke.py" in workflow
    runner = (ROOT / "scripts" / "run_performance_smoke.py").read_text()
    saturation = (ROOT / "scripts" / "adversarial_load_smoke.py").read_text()
    assert "adversarial_load_smoke.py" in runner
    assert "/performance_data/classcall/" in runner
    assert "/performance_data/classcall/" in saturation
    assert "public_asset_head" in runner
    assert "dhxpyt-0.9.16+backend-py3-none-any.whl" in runner
    assert '"/classcall/' not in runner
    assert '"/classcall/' not in saturation
    assert "performance-saturation.json" in workflow
    assert "pytincture-performance-service" in workflow
    assert "pytincture-performance-${{ matrix.browser }}" in workflow


def test_performance_documentation_links_the_versioned_contract():
    documentation = (ROOT / "docs" / "performance.md").read_text()
    assert "contracts/performance-budgets-v1.json" in documentation
