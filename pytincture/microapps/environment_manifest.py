"""An environment lockfile, active browser bundle pointer and fenced build lease."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import PurePosixPath
from packaging.utils import parse_wheel_filename, canonicalize_name, canonicalize_version
from packaging.version import Version
from .manager import _relative

SCHEMA = 'pytincture.environment/v1'
MAX_MANIFEST = 4 * 1024 * 1024


def encode(document):
    return (json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def empty_manifest(environment):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}', environment):
        raise ValueError('Invalid environment id')
    return {'schema': SCHEMA, 'environment': environment, 'revision': 0,
            'runtime': {'engine': 'pyodide', 'version': '0.29.3'},
            'base': None, 'apps': {}, 'active': None, 'history': [], 'build': None, 'build_epoch': 0, 'error': None}


def validate_artifact(artifact, *, version=None):
    if not isinstance(artifact, dict) or set(artifact) != {'key', 'sha256'}:
        raise ValueError('Wheel artifacts require an exact key and SHA-256')
    _relative(artifact['key'])
    if not re.fullmatch(r'[a-f0-9]{64}', artifact['sha256']): raise ValueError('Invalid artifact hash')
    name, wheel_version, _, tags = parse_wheel_filename(PurePosixPath(artifact['key']).name)
    if version is not None and wheel_version != Version(version): raise ValueError('Wheel filename version differs from installed version')
    if not all(tag.abi == 'none' and tag.platform == 'any' for tag in tags):
        raise ValueError('Browser/environment wheels must be pure Python')
    return canonicalize_name(name), str(wheel_version)


def validate_manifest(document, environment=None):
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise ValueError('Invalid environment manifest schema')
    required = set(empty_manifest(document.get('environment', '')))
    if set(document) != required: raise ValueError('Unknown or missing environment manifest fields')
    if environment is not None and document['environment'] != environment: raise ValueError('Wrong environment manifest')
    if type(document['revision']) is not int or document['revision'] < 0: raise ValueError('Invalid manifest revision')
    if type(document['build_epoch']) is not int or document['build_epoch'] < 0: raise ValueError('Invalid build epoch')
    if document['runtime'] != {'engine': 'pyodide', 'version': '0.29.3'}: raise ValueError('Unsupported runtime')
    apps = document['apps']
    if not isinstance(apps, dict) or len(apps) > 128: raise ValueError('Invalid app inventory')
    for app_id, app in apps.items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', app_id) or not isinstance(app, dict) or type(app.get('installed')) is not bool:
            raise ValueError('Apps require a valid id and installed flag')
        if not app['installed']: continue
        if set(app) - {'installed', 'version', 'approval_id', 'backend', 'browser', 'dependencies'}:
            raise ValueError('Unknown installed-app fields')
        Version(app['version'])
        validate_artifact(app['backend'], version=app['version'])
        browser = app['browser']
        if not isinstance(browser, dict) or set(browser) != {'artifact', 'entrypoint'}: raise ValueError('Invalid browser artifact')
        validate_artifact(browser['artifact'], version=app['version'])
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', browser['entrypoint'], flags=re.ASCII):
            raise ValueError('Invalid browser entrypoint')
        if not isinstance(app['approval_id'], str) or not app['approval_id'].strip(): raise ValueError('Approval record is required')
        if not isinstance(app.get('dependencies', []), list) or len(app.get('dependencies', [])) > 64: raise ValueError('Invalid dependency lock')
        for dependency in app.get('dependencies', []): validate_artifact(dependency)
    base = document['base']
    if base is not None:
        if not isinstance(base, dict) or set(base) != {'artifact', 'application', 'entrypoint'}: raise ValueError('Invalid base browser package')
        validate_artifact(base['artifact'])
        if not re.fullmatch(r'[A-Za-z_]\w*', base['application'], flags=re.ASCII) or not re.fullmatch(r'[A-Za-z_]\w*', base['entrypoint'], flags=re.ASCII):
            raise ValueError('Invalid base entrypoint')
    if not isinstance(document['history'], list) or len(document['history']) > 8: raise ValueError('Invalid retained bundle history')
    for active in ([document['active']] if document['active'] is not None else []) + document['history']:
        if not isinstance(active, dict) or set(active) != {'selection', 'key', 'sha256', 'bytes', 'revision', 'inventory'}: raise ValueError('Invalid active bundle')
        _relative(active['key'])
        if not all(re.fullmatch(r'[a-f0-9]{64}', active[field]) for field in ('selection', 'sha256')): raise ValueError('Invalid active hash')
        if type(active['bytes']) is not int or not 0 < active['bytes'] <= 256 * 1024 * 1024: raise ValueError('Invalid bundle size')
        if type(active['revision']) is not int or not 0 <= active['revision'] <= document['revision']: raise ValueError('Invalid active revision')
        inventory = active['inventory']
        if not isinstance(inventory, dict) or set(inventory) != {'lock', 'releases'}:
            raise ValueError('Invalid active inventory')
        lock = inventory['lock']
        if not isinstance(lock, dict) or set(lock) != {'schema', 'environment', 'runtime', 'base', 'apps'}:
            raise ValueError('Invalid active wheel lock')
        locked = empty_manifest(document['environment'])
        locked.update(lock)
        validate_manifest(locked, document['environment'])
        if selection_id(locked) != active['selection']:
            raise ValueError('Active selection does not match wheel lock')
        releases = inventory['releases']
        if not isinstance(releases, dict) or set(releases) != set(lock['apps']):
            raise ValueError('Invalid active release inventory')
        if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in releases.values()):
            raise ValueError('Invalid active release id')
    build = document['build']
    if build is not None:
        if not isinstance(build, dict) or set(build) != {'token', 'selection', 'expires_at', 'epoch'}: raise ValueError('Invalid build lease')
        if not re.fullmatch(r'[a-f0-9]{64}', build['selection']) or not re.fullmatch(r'[a-f0-9]{32}', build['token']): raise ValueError('Invalid lease identity')
        import math
        if type(build['expires_at']) not in (int, float) or not math.isfinite(build['expires_at']): raise ValueError('Invalid lease expiry')
        if type(build['epoch']) is not int or build['epoch'] != document['build_epoch']: raise ValueError('Invalid lease fence')
    if len(encode(document)) > MAX_MANIFEST: raise ValueError('Manifest exceeds byte limit')
    return document


def selection(document):
    # Uninstalled artifacts, bookkeeping revisions and builder heartbeats do not
    # change the package selection or cause a rebuild.
    return {'schema': SCHEMA, 'environment': document['environment'], 'runtime': document['runtime'],
            'base': document['base'], 'apps': {key: value for key, value in sorted(document['apps'].items()) if value['installed']}}


def selection_id(document):
    content = copy.deepcopy(selection(document))
    for app in content['apps'].values():
        app.pop('approval_id', None)
        app['version'] = canonicalize_version(app['version'])
    return hashlib.sha256(encode(content)).hexdigest()


def decode(data, environment):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate manifest key')
            result[key] = value
        return result
    if len(data) > MAX_MANIFEST: raise ValueError('Manifest exceeds byte limit')
    return validate_manifest(json.loads(data, object_pairs_hook=unique), environment)
