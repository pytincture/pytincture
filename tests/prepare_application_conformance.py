"""Prepare pinned public app/widget checkouts for repeatable release conformance.

Install tests/conformance/requirements.txt in a dedicated Python 3.13 environment
first. This creates new checkouts only; it never changes existing app repositories.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/conformance'


def run(*args, cwd=None, env=None):
    subprocess.run(list(map(str,args)), cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit('Choose a new destination; existing checkouts are never overwritten')
    lock = json.loads((FIXTURES/'applications.lock.json').read_text())
    for item in lock['repositories'].values():
        if item.get('patch'):
            patch = FIXTURES/item['patch']
            if hashlib.sha256(patch.read_bytes()).hexdigest() != item['patch_sha256']:
                raise SystemExit('Conformance patch integrity mismatch: '+item['patch'])
    destination.mkdir(parents=True)
    for name, item in lock['repositories'].items():
        target = destination/name
        run('git','init',target)
        run('git','-C',target,'fetch','--depth','1',item['url'],item['commit'])
        run('git','-C',target,'checkout','--detach','FETCH_HEAD')
        if item.get('patch'):
            run('git','-C',target,'apply','--check',FIXTURES/item['patch'])
            run('git','-C',target,'apply',FIXTURES/item['patch'])
    wheels = destination/'wheels'
    wheels.mkdir()
    env = {**os.environ, 'SOURCE_DATE_EPOCH':str(lock['source_date_epoch'])}
    for name in ('dhxpyt','wapyt'):
        run(sys.executable,'-m','build','--wheel','--no-isolation','--outdir',wheels,cwd=destination/name,env=env)
    run('uv','pip','install','--python',sys.executable,'--no-deps','--reinstall',*sorted(wheels.glob('*.whl')))
    shutil.copy(next(wheels.glob('dhxpyt-*.whl')),destination/'example/example')
    shutil.copy(next(wheels.glob('wapyt-*.whl')),destination/'chat/src/wawesomechat')
    # Pin the speech model revision too. The test server still runs real Whisper.
    from huggingface_hub import snapshot_download
    model = snapshot_download(lock['voice_model']['repository'],revision=lock['voice_model']['revision'],
                              cache_dir=str(Path.home()/'Library/Caches/pytincture-runtime-choice/whisper'),
                              allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.*','preprocessor_config.json'])
    setup = {'example':str(destination/'example'),'chat':str(destination/'chat'),
             'python':sys.executable,'voice_model':model,'repositories':lock['repositories']}
    (destination/'prepared.json').write_text(json.dumps(setup,indent=2)+'\n')
    print('Prepared conformance inputs: '+str(destination/'prepared.json'))


if __name__ == '__main__':
    main()
