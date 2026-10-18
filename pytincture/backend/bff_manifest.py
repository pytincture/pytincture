"""Deployment-owned BFF parse cache. Never load artifacts supplied by clients."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

_MAX_BYTES = 32 * 1024 * 1024
_DIGEST = re.compile(r'^[a-f0-9]{64}$')


def _parser_identity():
    from pytincture import __version__, dataclass, bff_docs
    from pytincture.backend import bff
    digest = hashlib.sha256()
    for module in (dataclass, bff_docs, bff):
        digest.update(Path(module.__file__).read_bytes())
    digest.update(Path(__file__).read_bytes())
    return {'version': __version__, 'parser_sha256': digest.hexdigest(),
            'python': list(sys.version_info[:2])}


def _pack(value):
    # Tagged containers retain tuple/default/policy types without pickle or eval.
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if type(value) in (dict, list, tuple):
        if isinstance(value, dict):
            return ['dict', [[_pack(k), _pack(v)] for k, v in value.items()]]
        return [type(value).__name__, [_pack(item) for item in value]]
    # Unusual literal defaults remain supported through normal source parsing.
    raise ValueError('Manifest contains a literal that requires runtime parsing')


def _unpack(value):
    if type(value) in (str, int, float, bool, type(None)):
        return value
    if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], list):
        raise ValueError('Invalid encoded BFF metadata')
    kind, items = value
    if kind == 'dict':
        result = {}
        for pair in items:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('Invalid encoded BFF mapping')
            key, item = map(_unpack, pair)
            if key in result:
                raise ValueError('Duplicate BFF metadata key')
            result[key] = item
        return result
    if kind in ('list', 'tuple'):
        result = [_unpack(item) for item in items]
        return tuple(result) if kind == 'tuple' else result
    raise ValueError('Invalid BFF metadata type')


def build_bff_manifest(modules_path, output_path):
    """Build an atomic private server artifact without importing application code."""
    from pytincture.backend.bff import BFFRegistry
    registry = BFFRegistry(os.fspath(modules_path))
    if registry.failures:
        raise ValueError('Cannot prebuild BFF manifest: rejected source files: ' +
                         ', '.join(sorted(registry.failures)))
    sources = {}
    for name, (digest, manifest) in sorted(registry._manifest_cache.items()):
        try:
            encoded = _pack(manifest)
            json.dumps(encoded, allow_nan=False)
        except ValueError:
            continue  # Parse unsupported literal types from source at runtime.
        sources[name] = {'sha256': digest, 'manifest': encoded}
    payload = (json.dumps({'schema': 1, 'parser': _parser_identity(), 'sources': sources},
                          sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(payload) > _MAX_BYTES:
        raise ValueError('BFF manifest exceeds 32 MiB')
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.bff-', delete=False) as stream:
            staged = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, target)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return target


def load_bff_manifest(path):
    """Return candidate parses; callers MUST securely verify each source digest."""
    from pytincture.backend.safe_paths import normalize_relative_path
    try:
        with open(path, 'rb') as stream:
            payload = stream.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise ValueError('BFF manifest exceeds 32 MiB')
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('sources'), dict):
            raise ValueError('Invalid BFF manifest schema')
        if data.get('parser') != _parser_identity():
            return {}  # Another framework/parser build: discover from source.
        cache = {}
        for name, entry in data['sources'].items():
            if normalize_relative_path(name) != name or not name.endswith('.py'):
                raise ValueError('Invalid BFF source path')
            if not isinstance(entry, dict) or not isinstance(entry.get('sha256'), str) or not _DIGEST.fullmatch(entry['sha256']):
                raise ValueError('Invalid BFF source digest')
            manifest = _unpack(entry['manifest'])
            if not isinstance(manifest, dict):
                raise ValueError('Invalid BFF file manifest')
            for key, operation in manifest.items():
                if not isinstance(key, tuple) or len(key) != 2 or not all(isinstance(part, str) and part.isidentifier() for part in key):
                    raise ValueError('Invalid BFF operation identifier')
                if not isinstance(operation, dict) or not isinstance(operation.get('policy'), dict):
                    raise ValueError('Invalid BFF operation metadata')
                if '_source_path' in operation or '_source_digest' in operation:
                    raise ValueError('BFF artifact cannot override source identity')
            cache[name] = (entry['sha256'], manifest)
        return cache
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise RuntimeError('Unable to load configured prebuilt BFF manifest') from exc


def main():
    parser = argparse.ArgumentParser(description='Build a private server BFF discovery manifest.')
    parser.add_argument('--modules-path', default='.')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(build_bff_manifest(args.modules_path, args.output))


if __name__ == '__main__':
    main()
