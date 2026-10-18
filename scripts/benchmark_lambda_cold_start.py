#!/usr/bin/env python3
"""Compare source-only, prebuilt-BFF and checked-bytecode cold processes locally.

Copies the active environment's site-packages and current framework source into
an isolated staging tree. Does not invoke AWS or include Lambda/Web Adapter cost.
No cProfile/importtime instrumentation. Each sample starts a new Python process;
OS caches may stay warm. Whole-process wall time includes worker shutdown.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import site
import statistics
import subprocess
import sys
import tempfile
import time

WORKER = '''import time
started=time.perf_counter()
import asyncio,json,os
from pytincture import create_app
imported=time.perf_counter()
app=create_app()
constructed=time.perf_counter()
import uvicorn
transport = dict(loop='asyncio',http='h11',ws='none',lifespan='on') if os.environ.get('PYTINCTURE_BENCH_HTTP_ONLY') else {}
uvicorn.Config(app,host='127.0.0.1',port=8080,workers=1,access_log=False,proxy_headers=False,**transport).load()
server_loaded=time.perf_counter()
from pytincture.configuration import configuration_context
async def measure():
    with configuration_context(app.state.pytincture_config):
        async with app.router.lifespan_context(app):
            ready=time.perf_counter()
            print(json.dumps({"import_ms":(imported-started)*1000,
                "factory_ms":(constructed-imported)*1000,
                "server_config_ms":(server_loaded-constructed)*1000,
                "lifespan_ms":(ready-server_loaded)*1000,
                "ready_ms":(ready-started)*1000}),flush=True)
asyncio.run(measure())
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--modules-path', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--output', type=Path, default=Path('/tmp/pytincture-cold-build-benchmark.json'))
    args=parser.parse_args()
    if args.runs < 1:
        parser.error('runs must be positive')
    from pytincture.backend.bff_manifest import build_bff_manifest
    from pytincture.lambda_build import prepare_lambda
    project=Path(__file__).resolve().parents[1]
    results={}
    with tempfile.TemporaryDirectory(prefix='pytincture-cold-') as temporary:
        root=Path(temporary)
        packages=root/'packages'
        ignored=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', 'node_modules', '.venv')
        for source in site.getsitepackages():
            if Path(source).is_dir():
                shutil.copytree(source, packages, dirs_exist_ok=True, ignore=ignored)
        shutil.rmtree(packages/'pytincture', ignore_errors=True)
        shutil.copytree(project/'pytincture', packages/'pytincture', ignore=ignored)
        modules=root/'apps'
        shutil.copytree(args.modules_path.resolve(), modules, ignore=ignored)
        manifest=root/'private'/'bff.json'
        environment=os.environ.copy()
        environment.pop('PYTHONPYCACHEPREFIX', None)
        environment.pop('PYTINCTURE_BFF_PREBUILT_MANIFEST', None)
        environment.update(PYTHONPATH=str(packages), MODULES_PATH=str(modules),
                           PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
        for scenario in ('source_only', 'prebuilt_bff', 'prebuilt_bff_and_bytecode', 'prebuilt_bytecode_http_only'):
            if scenario == 'prebuilt_bff':
                build_bff_manifest(modules, manifest)
                environment['PYTINCTURE_BFF_PREBUILT_MANIFEST']=str(manifest)
            elif scenario == 'prebuilt_bff_and_bytecode':
                prepare_lambda(root, modules_path=modules, manifest_path=manifest)
            elif scenario == 'prebuilt_bytecode_http_only':
                environment['PYTINCTURE_BENCH_HTTP_ONLY']='1'
            samples=[]
            for _ in range(args.runs):
                before=time.perf_counter()
                completed=subprocess.run([sys.executable,'-c',WORKER],cwd=root,env=environment,
                                         capture_output=True,text=True,timeout=60)
                wall=(time.perf_counter()-before)*1000
                if completed.returncode:
                    sys.stderr.write(completed.stderr)
                    raise SystemExit(completed.returncode)
                sample=json.loads(completed.stdout.strip())
                sample['whole_process_ms']=wall
                samples.append(sample)
            median={key: statistics.median(row[key] for row in samples) for key in samples[0]}
            results[scenario]={'median_ms':median,'samples':samples}
            print(scenario, json.dumps(median),flush=True)
    report={'python':sys.version.split()[0], 'runs':args.runs,
            'note':'Local fresh processes with warm OS caches; excludes AWS/Web Adapter; whole_process_ms includes shutdown.',
            'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(args.output.resolve())


if __name__=='__main__':
    main()
