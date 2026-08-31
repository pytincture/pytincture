#!/usr/bin/env python3
"""Small dependency-free HTTP load gate for production smoke testing."""

import argparse
import concurrent.futures
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_once(
    url: str,
    timeout: float,
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> tuple[float, int]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError:
        status = 0
    return (time.perf_counter() - started) * 1000, status


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = max(0, math.ceil(percent * len(ordered)) - 1)
    return ordered[index]


def wait_until_ready(url: str, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        _, status = request_once(url, timeout=1)
        if status == 200:
            return
        time.sleep(0.1)
    raise SystemExit(f"service did not become ready within {wait_seconds:g}s: {url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--path", default="/healthz")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--wait-seconds", type=float, default=15)
    parser.add_argument("--p95-budget-ms", type=float, default=500)
    parser.add_argument("--method", choices=("GET", "HEAD", "POST"), default="GET")
    parser.add_argument("--body-json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    body = None
    if args.body_json is not None:
        body = json.dumps(json.loads(args.body_json), separators=(",", ":")).encode()
        if args.method == "GET":
            parser.error("--body-json requires --method POST")

    url = args.base_url.rstrip("/") + "/" + args.path.lstrip("/")
    wait_until_ready(args.base_url.rstrip("/") + "/readyz", args.wait_seconds)
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(
                lambda _: request_once(
                    url,
                    args.timeout_seconds,
                    method=args.method,
                    body=body,
                ),
                range(args.requests),
            )
        )
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in results]
    statuses = [status for _, status in results]
    failures = sum(status < 200 or status >= 300 for status in statuses)
    report = {
        "url": url,
        "method": args.method,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "failures": failures,
        "requests_per_second": round(args.requests / elapsed, 2),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "p95_budget_ms": args.p95_budget_ms,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return int(failures > 0 or report["latency_ms"]["p95"] > args.p95_budget_ms)


if __name__ == "__main__":
    raise SystemExit(main())
