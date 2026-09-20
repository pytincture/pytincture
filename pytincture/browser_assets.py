"""Deterministic portable resource inventory and static dependency auditing."""
import hashlib
import json
import posixpath
import re
from urllib.parse import urlsplit, unquote

from pytincture.configuration import canonical_browser_connect_origins

MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))+'\n').encode()


def audit_assets(artifacts, external_origins=None):
    origins = external_origins or {}
    if not isinstance(origins, dict) or set(origins) - {'script', 'style', 'font', 'connect', 'image'}:
        raise ValueError('external-origins must map script/style/font/connect/image to exact HTTPS origins')
    allowed = {}
    for kind, values in origins.items():
        if not isinstance(values, list):
            raise ValueError('external-origins entries must be lists')
        allowed[kind] = canonical_browser_connect_origins(values)
        if any(not value.startswith('https://') for value in allowed[kind]):
            raise ValueError('external asset origins must be exact HTTPS origins')
    findings = []
    requirements = {kind: set() for kind in ('script', 'style', 'font', 'connect', 'image')}
    def dependency(owner, value, kind):
        value = value.strip()
        if value.startswith(('data:', '#')):
            return
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            origin = f'{parsed.scheme}://{parsed.netloc}'
            if origin not in allowed.get(kind, ()):
                raise ValueError(f'{owner}: external {kind} dependency {value}; bundle it locally or explicitly configure external-origins.{kind}')
            requirements[kind].add(origin)
            findings.append({'file': owner, 'severity': 'warning', 'rule': 'external-runtime-dependency',
                             'message': f'{kind}-src requires {origin}; external bytes are not part of the bundle integrity guarantee'})
            return
        path = posixpath.normpath(posixpath.join(posixpath.dirname(owner), unquote(parsed.path)))
        if parsed.path.startswith('/') or path.startswith('../') or path not in artifacts:
            raise ValueError(f'{owner}: missing local {kind} dependency {value}; include its resource in the bundle')
    for name, content in sorted(artifacts.items()):
        if len(content) > MAX_ASSET_BYTES:
            raise ValueError(f'Browser asset exceeds 32 MiB: {name}')
        if name.endswith('.css'):
            text = re.sub(r'/\*.*?\*/', '', content.decode('utf-8'), flags=re.S)
            for match in re.finditer(r'url\(\s*[\'"]?([^\)\'"\s]+)[\'"]?\s*\)', text, re.I):
                value = match.group(1)
                extension = posixpath.splitext(urlsplit(value).path)[1].lower()
                is_import = re.search(r'@import\s*$', text[:match.start()], re.I) is not None
                kind = 'style' if is_import or extension == '.css' else 'font' if extension in {'.woff', '.woff2', '.ttf', '.otf', '.eot'} else 'image'
                dependency(name, value, kind)
            for match in re.finditer(r'@import\s+[\'"]([^\'"]+)[\'"]', text, re.I):
                dependency(name, match.group(1), 'style')
        elif name.endswith(('.js', '.mjs')):
            text = content.decode('utf-8')
            # Active literal assignments; URLs in licenses/comments are not dependencies.
            for match in re.finditer(r'\.src\s*=\s*[\'"](https?://[^\'"]+)[\'"]', text):
                dependency(name, match.group(1), 'script')
            urls = sorted(set(re.findall(r'[\'"](https?://[^\'"\s]+)[\'"]', text)))
            for url in urls:
                findings.append({'file': name, 'severity': 'warning', 'rule': 'external-url-review',
                                 'message': f'Review literal URL for conditional runtime dependencies: {url[:240]}'})
    if sum(map(len, artifacts.values())) > MAX_BUNDLE_BYTES:
        raise ValueError('Aggregate browser bundle exceeds 128 MiB')
    return {'findings': findings, 'required_origins': {kind: sorted(values) for kind, values in requirements.items()},
            'configured_origins': {kind: list(values) for kind, values in allowed.items()},
            'static_analysis_limits': 'Computed JS/CSS URLs and dynamically generated code require browser conformance tests.'}


def seal_manifest(manifest, artifacts):
    if len(artifacts) > 4096:
        raise ValueError("Browser bundle exceeds 4096 inventory entries")
    if any(len(content) > MAX_ASSET_BYTES for content in artifacts.values()):
        raise ValueError('Browser asset exceeds 32 MiB')
    if sum(map(len, artifacts.values())) > MAX_BUNDLE_BYTES:
        raise ValueError('Aggregate browser bundle exceeds 128 MiB')
    inventory = {name: {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
                 for name, content in sorted(artifacts.items())}
    manifest = {**manifest, 'integrity': inventory}
    identifier = hashlib.sha256(canonical_json(manifest)).hexdigest()
    return {**manifest, 'bundleId': identifier, 'assetBase': f'releases/{identifier}/'}


def inspect_bundle(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    expected = manifest['bundleId']
    digest = hashlib.sha256(canonical_json({key: value for key, value in manifest.items()
                                          if key not in {'bundleId', 'assetBase'}})).hexdigest()
    if expected != digest or manifest['assetBase'] != f'releases/{expected}/':
        raise ValueError('Bundle identifier does not match manifest contents')
    root = (manifest_path.parent / manifest['assetBase']).resolve()
    if not root.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError('Bundle release escapes the manifest directory')
    for name, entry in manifest['integrity'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Bundle integrity path escapes its root')
        data = path.read_bytes()
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError(f'Bundle integrity mismatch: {name}')
    return {'bundle_id': expected, 'assets': len(manifest['integrity']),
            'bytes': sum(entry['bytes'] for entry in manifest['integrity'].values()),
            'runtimes': manifest['runtimes'], 'profile': manifest['profile']}
