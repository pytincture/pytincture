#!/usr/bin/env python3
"""Saturate bounded BFF admission and prove prompt recovery."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from collections import Counter
from pathlib import Path

from load_smoke import request_once, wait_until_ready


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--min-rejections", type=int, default=1)
    parser.add_argument("--recovery-budget-ms", type=float, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    wait_until_ready(f"{base_url}/readyz", 15)
    slow_url = (
        f"{base_url}/performance_data/classcall/"
        "performance_data.py/PerformanceData/slow"
    )
    body = b'{"args":[],"kwargs":{"seconds":0.1}}'
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(
                lambda _: request_once(
                    slow_url,
                    5,
                    method="POST",
                    body=body,
                ),
                range(args.requests),
            )
        )

    statuses = Counter(status for _, status in results)
    recovery_latency, recovery_status = request_once(
        f"{base_url}/performance_data/classcall/"
        "performance_data.py/PerformanceData/ping",
        5,
        method="POST",
        body=b'{"args":[],"kwargs":{"value":2}}',
    )
    health_latency, health_status = request_once(f"{base_url}/healthz", 5)
    report = {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "statuses": {str(key): value for key, value in sorted(statuses.items())},
        "overload_rejections": statuses[503],
        "minimum_overload_rejections": args.min_rejections,
        "recovery_budget_ms": args.recovery_budget_ms,
        "recovery": {
            "bff_status": recovery_status,
            "bff_latency_ms": round(recovery_latency, 3),
            "health_status": health_status,
            "health_latency_ms": round(health_latency, 3),
        },
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    unexpected = set(statuses) - {200, 503}
    return int(
        bool(unexpected)
        or statuses[503] < args.min_rejections
        or statuses[200] == 0
        or recovery_status != 200
        or health_status != 200
        or recovery_latency > args.recovery_budget_ms
        or health_latency > args.recovery_budget_ms
    )


if __name__ == "__main__":
    raise SystemExit(main())
