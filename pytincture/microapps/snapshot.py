"""Deterministic aggregate browser packages with an embedded installed inventory."""
from __future__ import annotations
from importlib import metadata
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from packaging.utils import parse_wheel_filename, canonicalize_name
from packaging.specifiers import SpecifierSet
import platform
from .environment_manifest import encode, validate_artifact
from .manager import _unpack_wheel
from .browser_safety import browser_archive_files, validate_browser_archive

INVENTORY = 'pytincture-environment.json'
MAX_BUNDLE = 256 * 1024 * 1024


def verify_wheel(path, artifact, *, python_version=None):
    name, version = validate_artifact(artifact)
    if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Wheel is missing or exceeds byte limit')
    _, _, _, tags = parse_wheel_filename(path.name)
    target_python = python_version or platform.python_version()
    major, minor = target_python.split('.')[:2]
    if not any(tag.interpreter in {f'py{major}', f'py{major}{minor}', f'cp{major}{minor}'} for tag in tags):
        raise ValueError('Wheel Python tag is incompatible with the target interpreter')
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']: raise ValueError('Wheel SHA-256 mismatch')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _unpack_wheel(path, root)
        distributions = list(metadata.distributions(path=[str(root)]))
        if len(distributions) != 1: raise ValueError('Wheel must contain one distribution')
        distribution = distributions[0]
        if python_version is not None:
            if distribution.requires:
                raise ValueError('Browser wheel dependencies must be vendored at build time')
            if list(root.rglob('pytincture-app.json')):
                raise ValueError('Browser artifact contains a backend app manifest')
            validate_browser_archive(path)
        requirement = distribution.metadata.get('Requires-Python')
        if requirement and not SpecifierSet(requirement).contains(target_python):
            raise ValueError('Wheel Requires-Python is incompatible with the target interpreter')
        if canonicalize_name(distribution.metadata['Name']) != name or distribution.version != version:
            from packaging.version import Version
            if canonicalize_name(distribution.metadata['Name']) != name or Version(distribution.version) != Version(version):
                raise ValueError('Wheel filename and metadata disagree')
    return name, version


def _snapshot_files(browser_paths, base_path=None):
    artifacts = ([base_path] if base_path is not None else []) + [browser_paths[key] for key in sorted(browser_paths)]
    files = {}; size = 0
    distributions = {}
    for wheel in artifacts:
        name, version, _, _ = parse_wheel_filename(wheel.name)
        identity = (version, hashlib.sha256(wheel.read_bytes()).hexdigest())
        if name in distributions:
            if distributions[name] != identity: raise ValueError(f'Conflicting browser distribution: {name}')
            continue
        distributions[name] = identity
        for name, data in browser_archive_files(wheel).items():
            if name == INVENTORY: raise ValueError('Wheel collides with the bundle inventory')
            if name in files and files[name] != data: raise ValueError(f'Conflicting browser package files: {name}')
            if name not in files:
                size += len(data)
                if size > MAX_BUNDLE: raise ValueError('Browser bundle exceeds 256 MiB')
                files[name] = data
    return files


def verify_snapshot_files(path, browser_paths, base_path=None):
    """A prebuilt aggregate may contain only files from the approved browser wheels."""
    actual = browser_archive_files(path)
    if INVENTORY not in actual:
        raise ValueError('Browser bundle is missing its inventory')
    inventory = actual.pop(INVENTORY)
    if len(inventory) > 4 * 1024 * 1024:
        raise ValueError('Browser bundle inventory exceeds 4 MiB')
    if actual != _snapshot_files(browser_paths, base_path):
        raise ValueError('Browser bundle contents differ from approved browser wheels')
    return inventory


def build_snapshot(output, selected, releases, browser_paths, base_path=None):
    inventory = {'schema': 1, 'selection': selected['selection'], 'runtime': selected['runtime'],
                 'base': selected['base'], 'apps': {}}
    for app_id in sorted(releases):
        release = releases[app_id]
        inventory['apps'][app_id] = {'version': release['version'], 'release': release['release'],
                                    'sha256': release['browser']['sha256']}
    files = _snapshot_files(browser_paths, base_path)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted({**files, INVENTORY: encode(inventory)}.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    if output.stat().st_size > MAX_BUNDLE: raise ValueError('Compressed bundle exceeds byte limit')
    return inventory
