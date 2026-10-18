"""Prepare a staging directory for fast, source-verified Lambda initialization."""
from __future__ import annotations

import argparse
import compileall
import json
import os
from pathlib import Path
import py_compile
import sys


def prepare_lambda(root, *, modules_path, manifest_path, runtime_root='/var/task'):
    """Prebuild BFF metadata and checked-hash bytecode; keep all source files.

    Run after installing deployment dependencies, on the target Python minor
    version. This operates on an unpacked staging tree, never on a live server.
    """
    root = Path(root).expanduser().resolve(strict=True)
    modules = Path(modules_path).expanduser().resolve(strict=True)
    manifest = Path(manifest_path).expanduser().resolve()
    if not root.is_dir() or not modules.is_dir() or not modules.is_relative_to(root):
        raise ValueError('Application modules must be a directory within the deployment root')
    if not manifest.is_relative_to(root) or manifest.is_relative_to(modules):
        raise ValueError('Private BFF manifest must be within the deployment root and outside application modules')
    if sys.pycache_prefix is not None:
        raise ValueError('Unset PYTHONPYCACHEPREFIX when preparing deployment bytecode')
    if sys.flags.optimize:
        raise ValueError('Run without -O: preserve assertions, docstrings and default runtime semantics')
    from pytincture.backend.bff_manifest import build_bff_manifest
    built = build_bff_manifest(modules, manifest)
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not name.startswith('.') and
                         name not in {'__pycache__', 'node_modules'} and
                         not Path(directory, name).is_symlink())
        for name in sorted(files):
            source = Path(directory, name)
            if not name.endswith('.py') or source.is_symlink():
                continue
            # Checked hashes retain source validation even when files are changed
            # with preserved mtimes. -O/-OO and unchecked hashes are never used.
            if not compileall.compile_file(
                str(source), force=True, quiet=2, optimize=0,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
                stripdir=str(root), prependdir=runtime_root,
            ):
                raise ValueError(f'Unable to compile deployment source: {source.relative_to(root)}')
            count += 1
    return {'python': sys.version.split()[0], 'compiled_files': count,
            'invalidation': 'checked-hash', 'runtime_root': runtime_root,
            'manifest': str(built), 'deployment_root': str(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Unpacked deployment directory containing installed dependencies')
    parser.add_argument('--modules-path', required=True, help='Application modules within the deployment root')
    parser.add_argument('--manifest', required=True, help='Private server manifest path within the deployment root')
    parser.add_argument('--runtime-root', default='/var/task', help='Deployed path used in tracebacks')
    args = parser.parse_args()
    print(json.dumps(prepare_lambda(args.root, modules_path=args.modules_path,
                                   manifest_path=args.manifest, runtime_root=args.runtime_root), indent=2))


if __name__ == '__main__':
    main()
