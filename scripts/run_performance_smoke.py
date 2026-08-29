#!/usr/bin/env python3
"""Run the versioned service performance profiles and retain JSON evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUDGETS = ROOT / "contracts" / "performance-budgets-v1.json"
PROFILES = {
    "health": {"path": "/healthz"},
    "appcode": {"path": "/e2e_app/appcode/appcode.pyt"},
    "bff": {
        "path": "/classcall/performance_data.py/PerformanceData/ping",
        "method": "POST",
        "body_json": '{"kwargs":{"value":1}}',
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()

    contract = json.loads(BUDGETS.read_text())
    if contract.get("schema_version") != 1:
        raise SystemExit("unsupported performance budget contract")
    budgets = contract["service"]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for name, profile in PROFILES.items():
        command = [
            sys.executable,
            str(ROOT / "scripts" / "load_smoke.py"),
            "--base-url",
            args.base_url,
            "--path",
            profile["path"],
            "--requests",
            str(budgets[f"{name}_requests"]),
            "--concurrency",
            str(budgets[f"{name}_concurrency"]),
            "--p95-budget-ms",
            str(budgets[f"{name}_p95_ms"]),
            "--output",
            str(args.output_dir / f"performance-{name}.json"),
        ]
        if profile.get("method"):
            command.extend(("--method", profile["method"]))
        if profile.get("body_json"):
            command.extend(("--body-json", profile["body_json"]))
        subprocess.run(command, check=True)

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "adversarial_load_smoke.py"),
            "--base-url",
            args.base_url,
            "--requests",
            str(budgets["saturation_requests"]),
            "--concurrency",
            str(budgets["saturation_concurrency"]),
            "--min-rejections",
            str(budgets["saturation_min_rejections"]),
            "--recovery-budget-ms",
            str(budgets["saturation_recovery_ms"]),
            "--output",
            str(args.output_dir / "performance-saturation.json"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
