"""Build a browser-only microapp wheel from an approved private app wheel.

Parse app code without importing it. Discover only the UI import graph, stop at
BFF boundaries and replace whole BFF modules with the standard generated proxies.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
from importlib import metadata
import io
import json
import keyword
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from packaging.version import Version

from pytincture.browser_build import _bff_stub, _check_imports
from pytincture.browser_profile import pyodide_modules
from pytincture.browser_sources import discover_sources, module_name, read_source
from pytincture.dataclass import get_bff_manifest
from pytincture.packaged_apps import _load_distribution
from .browser_safety import validate_browser_archive
from .manager import BrowserArtifact, WheelArtifact, _checked_copy, _unpack_wheel


_BRIDGE = '''import json
_context = None
_envelopes = ENVELOPES

def bind(context):
    global _context
    _context = context

async def pytinctureBrowserBff(module, class_name, method, payload, http_method='POST'):
    if _context is None:
        raise RuntimeError('Mount the microapp before calling its backend')
    if http_method != 'POST':
        raise RuntimeError('Generated microapp calls currently require POST')
    values = json.loads(payload)
    if (module, class_name, method) in _envelopes:
        args, kwargs = values['args'], values['kwargs']
    else:
        args, kwargs = [], values
    value = await _context.callBff(module + '.py', class_name, method, args, kwargs)
    if hasattr(value, 'to_py'):
        value = value.to_py()
    return json.dumps(value)

def pytinctureBrowserBffSync(*args, **kwargs):
    raise RuntimeError('Await the generated method_async() microapp BFF call')

class Stream:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('Generated streaming microapp calls are not supported')
'''


def _relocate(source, namespace, local_roots):
    """Keep every app's modules inside its unique browser namespace."""
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if not node.level and node.module and node.module.split('.')[0] in local_roots:
                node.module = namespace + '.' + node.module
            return node

        def visit_Import(self, node):
            result = []
            for alias in node.names:
                if alias.name.split('.')[0] not in local_roots:
                    result.append(ast.Import(names=[alias]))
                elif alias.asname or '.' not in alias.name:
                    result.append(ast.Import(names=[ast.alias(namespace + '.' + alias.name, alias.asname or alias.name)]))
                else:
                    root = alias.name.split('.')[0]
                    result.append(ast.Import(names=[ast.alias(namespace + '.' + root, root)]))
                    result.append(ast.Import(names=[ast.alias(namespace + '.' + alias.name)]))
            return result

        def visit_Call(self, node):
            # Rewriting dynamic imports cannot be proven from the static graph.
            if (isinstance(node.func, ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
                raise ValueError('Use static imports in generated browser wheels')
            return self.generic_visit(node)
    return ast.unparse(ast.fix_missing_locations(Imports().visit(ast.parse(source, feature_version=(3, 13))))) + '\n'


def _wrapper(namespace, module, function):
    return f'''import importlib as _importlib
import inspect as _inspect
from {namespace} import _pytincture_rpc as _rpc

async def mount(context):
    _rpc.bind(context)
    try:
        target = _importlib.import_module({(namespace + '.' + module)!r})
        value = getattr(target, {function!r})(context)
        if _inspect.isawaitable(value):
            await value
    except BaseException:
        _rpc.bind(None)
        raise

async def unmount(context):
    try:
        target = _importlib.import_module({(namespace + '.' + module)!r})
        hook = getattr(target, 'unmount', None)
        if hook:
            value = hook(context)
            if _inspect.isawaitable(value):
                await value
    finally:
        _rpc.bind(None)
'''


def build_browser_wheel(backend: WheelArtifact, output_directory, *, namespace, entrypoint,
                        files=(), resources=()):
    """Return a sanitized BrowserArtifact for approval, not runtime installation.

    ``entrypoint`` names a UI ``module:mount(context)`` inside the private app's
    inventoried resource directory. Only its discovered imports, explicit Python
    files and explicitly selected resources are considered browser inputs.
    """
    if (not isinstance(namespace, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', namespace)
            or keyword.iskeyword(namespace) or namespace == 'pytincture' or namespace in pyodide_modules()):
        raise ValueError('Use a unique lowercase Python package name for the browser namespace')
    if not isinstance(entrypoint, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', entrypoint, re.ASCII):
        raise ValueError('Browser entrypoint must be module:function')
    module, function = entrypoint.split(':')
    files = tuple(files)
    with tempfile.TemporaryDirectory(prefix='pytincture-browser-wheel-') as temporary:
        stage = Path(temporary).resolve()
        private_wheel = stage / Path(backend.path).name
        _checked_copy(backend, private_wheel)
        from .snapshot import verify_wheel
        verify_wheel(private_wheel, {'key': private_wheel.name, 'sha256': backend.sha256})
        site = stage / 'site'; site.mkdir()
        _unpack_wheel(private_wheel, site)
        distributions = list(metadata.distributions(path=[str(site)]))
        if len(distributions) != 1:
            raise ValueError('Backend wheel must contain one distribution')
        packaged = _load_distribution(distributions[0])
        version = str(Version(packaged.version))
        root = packaged.modules_path
        entry = module.replace('.', '/')
        entry += '/__init__.py' if (root / entry / '__init__.py').is_file() else '.py'
        sources, boundaries = discover_sources(root, entry, files, [])
        if entry in boundaries:
            raise ValueError('The browser entrypoint cannot be a BFF implementation')
        private_initializers = {
            str(parent / '__init__.py')
            for boundary in boundaries
            for parent in PurePosixPath(boundary).parents
            if str(parent) != '.'
        }
        if private_initializers.intersection((entry, *files)):
            raise ValueError('BFF package initializers must remain server-side')
        # Rediscover from the UI after removing initializer imports. The first
        # pass may have reached helpers imported only by a private initializer.
        empty_initializers = private_initializers.intersection(sources) - boundaries
        if empty_initializers:
            sources, boundaries = discover_sources(root, entry, files, [],
                                                   empty_initializers=empty_initializers)
        definition = next((node for node in ast.parse(sources[entry]).body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function), None)
        if definition is None:
            raise ValueError('Browser entrypoint must be a declared UI function')
        if (len(definition.args.posonlyargs + definition.args.args) != 1
                or definition.args.vararg or definition.args.kwarg or definition.args.kwonlyargs):
            raise ValueError('Browser entrypoint must accept one context argument')
        for name in list(sources):
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.':
                    sources.setdefault(str(parent / '__init__.py'), '')
        local_roots = {module_name(name).split('.')[0] for name in sources}
        if namespace in local_roots or any(part.startswith('_pytincture_') for name in sources for part in PurePosixPath(name).parts):
            raise ValueError('Browser sources collide with generated framework modules')
        _check_imports({name: '' if name in boundaries else source for name, source in sources.items()}, root,
                       vendor_modules=(), engine='pyodide')
        envelopes = []
        public = {}
        for name, source in sorted(sources.items()):
            if name in boundaries:
                for (cls, method), operation in get_bff_manifest(name, source=source).items():
                    if operation.get('external') or operation['kind'] != 'method':
                        continue
                    if operation.get('stream', {}).get('enabled') or 'POST' not in operation['http_methods']:
                        raise ValueError('Generated microapp proxies currently support non-streaming POST methods')
                    if any(p['kind'] in {'positional_only', 'var_positional', 'var_keyword'} for p in operation['parameters']):
                        envelopes.append((module_name(name).replace('.', '/'), cls, method))
                public[f'{namespace}/{name}'] = _bff_stub(name, source, bridge_module=namespace + '._pytincture_rpc').encode()
            else:
                public[f'{namespace}/{name}'] = _relocate(source, namespace, local_roots).encode()
        for name in list(public):
            for parent in PurePosixPath(name).parents:
                if str(parent) not in {'.', namespace}:
                    public.setdefault(str(parent / '__init__.py'), b'')
        for name in resources:
            if any(part.lower() in {'.py', '.pyi', '.pyc'} for part in PurePosixPath(name).suffixes):
                raise ValueError('Declare Python files as sources, not resources')
            if packaged.bff_manifest and root / name == packaged.bff_manifest:
                raise ValueError('Server BFF parse metadata must remain private')
            public[f'{namespace}/{name}'] = read_source(root, name)
        public[namespace + '/_pytincture_rpc.py'] = _BRIDGE.replace('ENVELOPES', repr(tuple(sorted(envelopes)))).encode()
        public[namespace + '/__init__.py'] = _wrapper(namespace, module, function).encode()
        info = f'{namespace}-{version}.dist-info'
        public[info + '/METADATA'] = f'Metadata-Version: 2.1\nName: {namespace}\nVersion: {version}\nRequires-Python: >=3.13,<3.14\n'.encode()
        public[info + '/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: pytincture\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
        record = io.StringIO(); writer = csv.writer(record)
        for name, data in sorted(public.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
            writer.writerow((name, 'sha256=' + digest, len(data)))
        writer.writerow((info + '/RECORD', '', ''))
        public[info + '/RECORD'] = record.getvalue().encode()
        output = stage / f'{namespace}-{version}-py3-none-any.whl'
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(public.items()):
                entry_info = zipfile.ZipInfo(name); entry_info.compress_type = zipfile.ZIP_DEFLATED
                entry_info.external_attr = 0o644 << 16
                archive.writestr(entry_info, data)
        validate_browser_archive(output, private_root=root)
        destination = Path(output_directory).resolve(); destination.mkdir(parents=True, exist_ok=True)
        target = destination / output.name
        data = output.read_bytes()
        # Publish complete bytes atomically without overwriting immutable versions.
        staged = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, prefix='.browser-', delete=False) as stream:
                staged = Path(stream.name)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            try:
                os.link(staged, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise ValueError('Browser wheel version already exists with different bytes') from None
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return BrowserArtifact(WheelArtifact(target, hashlib.sha256(data).hexdigest()), namespace + ':mount')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-wheel', required=True, type=Path)
    parser.add_argument('--backend-sha256', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--entrypoint', required=True)
    parser.add_argument('--output-directory', required=True, type=Path)
    parser.add_argument('--file', action='append', default=[])
    parser.add_argument('--resource', action='append', default=[])
    args = parser.parse_args()
    result = build_browser_wheel(WheelArtifact(args.backend_wheel, args.backend_sha256),
        args.output_directory, namespace=args.namespace, entrypoint=args.entrypoint,
        files=args.file, resources=args.resource)
    print(json.dumps({'wheel': str(result.wheel.path), 'sha256': result.wheel.sha256,
                      'entrypoint': result.entrypoint, 'runtime_version': result.runtime_version}))


if __name__ == '__main__':
    main()
