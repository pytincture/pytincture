#!/usr/bin/env python3
"""Measure BFF result preparation and warm source loads without an HTTP client.

Run the same script/interpreter against baseline and patched PYTHONPATH values.
Numbers are diagnostic microbenchmarks, not deployment capacity guarantees.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from pytincture.backend import results, source_loading
from pytincture import dataclass as bff_dataclass


def measure(call, iterations):
    for _ in range(10):
        call()
    timings = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        call()
        timings.append((time.perf_counter_ns() - start) / 1_000_000)
    return {'iterations': iterations, 'median_ms': statistics.median(timings),
            'p95_ms': sorted(timings)[int((iterations - 1) * 0.95)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=500)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error('--iterations must be positive')
    page = {'items': [{f'field{j}': f'book-{i}-value-{j}' for j in range(12)}
                      for i in range(100)],
            'total': 10000, 'page': 1, 'page_size': 100, 'has_more': True}
    limits = dict(max_bytes=1_000_000, max_depth=32, max_items=10000)
    report = {
        'python': platform.python_version(), 'platform': platform.platform(),
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ('pytincture', 'fastapi', 'pydantic')},
        'source_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for module in (results, source_loading, bff_dataclass)},
        'limits': limits, 'benchmarks': {},
    }
    for name, value in [('ping', {'value': 1}), ('page_100', page)]:
        converted, payload = results.prepare_bff_result(value, **limits)
        assert json.loads(payload) == converted == value
        report['benchmarks'][name] = {
            'response_bytes': len(payload),
            **measure(lambda: results.prepare_bff_result(value, **limits), args.iterations),
        }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        source = root / 'data.py'
        source.write_text('from pytincture.dataclass import backend_for_frontend\n'
                          '@backend_for_frontend\nclass Data:\n'
                          '    def ping(self, value): return {"value": value}\n'
                          '    def page(self): return []\n')
        with contextlib.redirect_stdout(io.StringIO()):
            report['benchmarks']['warm_source_load'] = measure(
                lambda: source_loading.load_source_module(str(source), 'Data', str(root)),
                args.iterations,
            )
    rendered = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
