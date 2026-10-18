#!/usr/bin/env python3
"""Profile a fresh Pytincture process without starting a network listener.

Run with the same Python, dependencies, environment and MODULES_PATH as the
service. --target module:factory profiles a deployment's own no-argument app
factory; otherwise uses pytincture.create_app(). Reports contain timings and
code locations, never configuration values. Import and function cumulative
rows overlap: do not add them together. Profiling adds overhead.
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import functools
import importlib
import inspect
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import subprocess
import sys
import time

MARKER = 'PYTINCTURE_STARTUP_PROFILE='


def worker(args):
    profile = cProfile.Profile()
    phases = {}
    hooks = []
    profile.enable()
    started = time.perf_counter()
    module_name, _, attribute = args.target.partition(':')
    module = importlib.import_module(module_name)
    phases['target_import_ms'] = (time.perf_counter() - started) * 1000
    before_factory = time.perf_counter()
    app = getattr(module, attribute)()
    phases['app_factory_ms'] = (time.perf_counter() - before_factory) * 1000

    # Preserve Starlette's sync/async hook behavior while timing each callback.
    def wrap(callback):
        name = f'{callback.__module__}.{callback.__qualname__}'
        if inspect.iscoroutinefunction(callback):
            @functools.wraps(callback)
            async def timed():
                before = time.perf_counter()
                try:
                    return await callback()
                finally:
                    hooks.append({'hook': name, 'ms': (time.perf_counter() - before) * 1000})
        else:
            @functools.wraps(callback)
            def timed():
                before = time.perf_counter()
                try:
                    return callback()
                finally:
                    hooks.append({'hook': name, 'ms': (time.perf_counter() - before) * 1000})
        return timed

    app.router.on_startup[:] = [wrap(hook) for hook in app.router.on_startup]

    async def lifespan():
        before = time.perf_counter()
        async with app.router.lifespan_context(app):
            phases['startup_lifespan_ms'] = (time.perf_counter() - before) * 1000
            phases['total_to_ready_ms'] = (time.perf_counter() - started) * 1000
            profile.disable()
        # Shutdown is deliberately excluded from startup measurements.

    asyncio.run(lifespan())
    stats = pstats.Stats(profile)
    functions = []
    for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        functions.append({'file': filename, 'line': line, 'function': name,
                          'calls': calls, 'self_ms': own * 1000,
                          'cumulative_ms': cumulative * 1000})
    report = {'python': platform.python_version(), 'platform': platform.platform(),
              'phases': phases, 'startup_hooks': hooks,
              'functions_by_cumulative': sorted(functions, key=lambda row: row['cumulative_ms'], reverse=True)[:args.top],
              'functions_by_self': sorted(functions, key=lambda row: row['self_ms'], reverse=True)[:args.top]}
    print(MARKER + json.dumps(report))


def parse_imports(log):
    rows = []
    for line in log.splitlines():
        if not line.startswith('import time:'):
            continue
        parts = line.removeprefix('import time:').split('|', 2)
        if len(parts) != 3:
            continue
        try:
            own, cumulative = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        rows.append({'module': parts[2].strip(), 'self_ms': own / 1000,
                     'cumulative_ms': cumulative / 1000})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default='pytincture:create_app')
    parser.add_argument('--modules-path', type=Path, help='Application source root used by the service')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--top', type=int, default=30)
    parser.add_argument('--output', type=Path, default=Path('/tmp/pytincture-startup-profile.json'))
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if ':' not in args.target or args.runs < 1 or args.top < 1:
        parser.error('target must be module:factory; runs and top must be positive')
    if args.worker:
        worker(args)
        return
    if args.modules_path:
        os.environ['MODULES_PATH'] = str(args.modules_path.resolve())
    reports = []
    for index in range(args.runs):
        command = [sys.executable, '-X', 'importtime', str(Path(__file__).resolve()),
                   '--worker', '--target', args.target, '--top', str(args.top)]
        before = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
        if result.returncode:
            # Let callers see the actual error without silently reporting partial success.
            sys.stderr.write(result.stderr)
            raise SystemExit(result.returncode)
        payloads = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
        if len(payloads) != 1:
            raise RuntimeError('Worker did not return exactly one startup report')
        report = json.loads(payloads[0])
        report['subprocess_wall_ms_including_shutdown'] = (time.perf_counter() - before) * 1000
        imports = parse_imports(result.stderr)
        report['imports_by_cumulative'] = sorted(imports, key=lambda row: row['cumulative_ms'], reverse=True)[:args.top]
        report['imports_by_self'] = sorted(imports, key=lambda row: row['self_ms'], reverse=True)[:args.top]
        reports.append(report)
        print(f'Run {index + 1}: ' + ', '.join(f'{key}={value:.1f}' for key, value in report['phases'].items()))
    summary = {key: statistics.median(run['phases'][key] for run in reports) for key in reports[0]['phases']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'target': args.target, 'median_ms': summary, 'runs': reports}, indent=2) + '\n')
    print('\nMedian phases (ms):', json.dumps(summary, indent=2))
    print('\nSlowest imports in last run (inclusive ms; rows overlap):')
    for row in reports[-1]['imports_by_cumulative'][:15]:
        print(f"{row['cumulative_ms']:9.1f}  {row['module']}")
    print(f'\nFull report: {args.output.resolve()}')


if __name__ == '__main__':
    main()
