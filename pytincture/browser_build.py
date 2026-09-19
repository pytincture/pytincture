"""Build application import graphs for Pyodide and MicroPython.

Application and backend modules are parsed, never imported or executed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import keyword
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import tomllib
import zipfile

from pytincture.browser_compatibility import adapt
from pytincture.backend.pages import find_app_string_setting, find_main_window_subclass
from pytincture.browser_sources import (relative_path as _relative, read_source as _read,
                                        module_name as _module, discover_sources, imported_modules)
from pytincture.dataclass import get_bff_manifest, has_bff_export_class

TEMPLATES = Path(__file__).with_name('browser_templates')
VENDOR = Path(__file__).with_name('browser_vendor')


def _widget_wheel(config, directory):
    if config.get('widget-wheel'):
        return (directory / config['widget-wheel']).resolve().read_bytes()
    try:
        distribution = importlib.metadata.distribution('dhxpyt')
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError('Install dhxpyt in the build environment or set widget-wheel') from exc
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name in sorted(distribution.files or [], key=str):
            relative = str(name)
            if relative.startswith('dhxpyt/') and '__pycache__' not in relative:
                # Fixed metadata makes installed-package builds reproducible.
                archive.writestr(zipfile.ZipInfo(relative), Path(distribution.locate_file(name)).read_bytes())
            elif '.dist-info/' in relative and 'license' in relative.lower():
                archive.writestr(zipfile.ZipInfo(relative), Path(distribution.locate_file(name)).read_bytes())
    return result.getvalue()

def _bff_stub(name: str, source: str) -> str:
    """Generate async session stubs from public signatures, never method bodies."""
    operations = get_bff_manifest(name, source=source)
    if not operations:
        raise ValueError(f'No decorated BFF operations found in {name}')
    classes: dict[str, list[str]] = {}
    parameter_names = {p['name'] for op in operations.values() for p in op.get('parameters', [])}
    parameter_names.update(class_name for class_name, _ in operations)
    json_name = '_pytincture_json'
    js_name = '_pytincture_js'
    while json_name in parameter_names:
        json_name += '_'
    while js_name in parameter_names:
        js_name += '_'
    sentinel = '_UNSET'
    while sentinel in parameter_names:
        sentinel += '_'
    for (class_name, method), operation in operations.items():
        classes.setdefault(class_name, [])
        if operation.get('external') or operation['kind'] != 'method':
            continue
        if operation.get('stream', {}).get('enabled'):
            classes[class_name].append(f'    async def {method}_async(self, *args, **kwargs):\n        raise NotImplementedError(\"Streaming BFF calls require the Pyodide package runtime\")')
            continue
        http_method = 'POST' if 'POST' in operation['http_methods'] else 'GET'
        envelope = any(p['kind'] in {'positional_only', 'var_positional', 'var_keyword'} for p in operation['parameters'])
        signature = ['self']
        payload_name = '_pytincture_arguments'
        while payload_name in parameter_names:
            payload_name += '_'
        body = [f'        {payload_name} = {{}}']
        keyword_only = False
        for param in ([] if envelope else operation['parameters']):
            kind = param['kind']
            if kind not in {'positional_or_keyword', 'keyword_only'}:
                raise ValueError(f'{name}:{class_name}.{method}: unsupported parameter kind {kind}')
            if kind == 'keyword_only' and not keyword_only:
                signature.append('*')
                keyword_only = True
            arg = param['name']
            signature.append(arg if param['required'] else f'{arg}={sentinel}')
            if param['required']:
                body.append(f'        {payload_name}[{arg!r}] = {arg}')
            else:
                body.extend([f'        if {arg} is not {sentinel}:', f'            {payload_name}[{arg!r}] = {arg}'])
        target = _module(name).replace('.', '/')
        if envelope:
            signature = ['self', '*args', '**kwargs']
            body = [f"        {payload_name} = {{'args':list(args), 'kwargs':kwargs}}"]
        method_option = '' if http_method == 'POST' else f', {http_method!r}'
        body.append(f'        return {json_name}.loads(await {js_name}.pytinctureBrowserBff({target!r}, {class_name!r}, {method!r}, {json_name}.dumps({payload_name}){method_option}))')
        classes.setdefault(class_name, []).append(
            f'    async def {method}_async({", ".join(signature)}):\n' + '\n'.join(body))
    if not classes:
        raise ValueError(f'No supported session methods found in {name}')
    return f'import json as {json_name}\nimport js as {js_name}\n{sentinel} = object()\n\n' + '\n\n'.join(
        f'class {name}:\n' + ('\n\n'.join(methods) or '    pass') for name, methods in classes.items()) + '\n'


def _check_imports(sources: dict[str, str], root: Path) -> None:
    """Catch missing local sources and known unsupported imports before serving."""
    native = {'js', 'jsffi', 'asyncio', 'array', 'binascii', 'builtins', 'cmath',
              'collections', 'gc', 'hashlib', 'heapq', 'io', 'json', 'math',
              'micropython', 'os', 'random', 're', 'select', 'struct', 'sys', 'time',
              'errno', 'deflate', '__main__'}
    available = {_module(name) for name in sources}
    for name, source in sources.items():
        if name.startswith('_pytincture_'):
            continue
        for node in ast.walk(ast.parse(source, filename=name)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = name.split('/')[:-node.level] if node.level else []
                modules = ['.'.join([*package, node.module or '']).rstrip('.')]
            else:
                continue
            for module in modules:
                if not module or module.split('.')[0] in native or module in available:
                    continue
                relative = module.replace('.', '/')
                if (root / (relative + '.py')).is_file() or (root / relative / '__init__.py').is_file():
                    raise ValueError(f'{name}:{node.lineno}: declare {module} in files or bff, or enable discover-imports')
                raise ValueError(f'{name}:{node.lineno}: unresolved browser import {module}; include a compatible pure-Python wheel or browser source (CPython-only packages are not supported)')


def build_browser_bundle(config_file: str | Path, *, application: str | None = None, check: bool = False) -> Path:
    """Build the [tool.pytincture.browser] table in an app's pyproject.toml."""
    config_file = Path(config_file).resolve()
    config = tomllib.loads(config_file.read_text())['tool']['pytincture']['browser']
    applications = config.pop('apps', {})
    if applications:
        if application is None:
            if len(applications) != 1:
                raise ValueError('Choose an application using --application or build every app with --all')
            application = next(iter(applications))
        if application not in applications:
            raise ValueError(f'Unknown browser application: {application}')
        config.update(applications[application])
        config.setdefault('application', application)
        config.setdefault('output', f'browser/{application}')
    allowed = {'application', 'modules-path', 'output', 'entrypoint', 'entry-kind',
               'files', 'bff', 'widget-wheel', 'micropython-assets', 'wheels',
               'assets', 'scripts', 'styles', 'heap-bytes', 'discover-imports'}
    if set(config) - allowed:
        raise ValueError(f'Unknown browser build settings: {sorted(set(config) - allowed)}')
    for key in ('files', 'bff', 'wheels', 'assets', 'scripts', 'styles'):
        values = config.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f'{key} must be a list of paths')
    if type(config.get('discover-imports', True)) is not bool:
        raise ValueError('discover-imports must be a boolean')
    root = (config_file.parent / config.get('modules-path', '.')).resolve()
    app_name = config.get('application') or application or config.get('entrypoint', '').split(':')[0].split('.')[0]
    output = root / _relative(config.get('output', f'browser/{app_name}'))
    if not output.resolve().is_relative_to(root):
        raise ValueError('Browser output escapes modules-path')
    entry = config.get('entrypoint')
    if not entry:
        app_name = config.get('application') or application
        if not app_name:
            raise ValueError('Set application or entrypoint in the browser build configuration')
        source = _read(root, app_name + '.py').decode()
        entry_name = find_main_window_subclass(app_name + '.py', source_code=source)
        if not entry_name:
            raise ValueError(f'Cannot find an entrypoint for {app_name}')
        entry = app_name + ':' + entry_name
    module, separator, entry_name = entry.partition(':')
    if not separator or any(not part.isidentifier() or keyword.iskeyword(part) for part in [*module.split('.'), entry_name]):
        raise ValueError('entrypoint must be module:ClassName or module:async_function')
    entry_path = module.replace('.', '/') + '.py'
    if not (root / entry_path).is_file():
        entry_path = module.replace('.', '/') + '/__init__.py'
    definition = next((n for n in ast.parse(_read(root, entry_path).decode()).body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == entry_name), None)
    if definition is None:
        raise ValueError(f'Entrypoint {entry} must name a top-level class or function')
    inferred_kind = 'async' if isinstance(definition, ast.AsyncFunctionDef) else 'callable'
    entry_kind = config.get('entry-kind', inferred_kind)
    if entry_kind not in {'mainwindow', 'async', 'callable'}:
        raise ValueError('entry-kind must be mainwindow, callable or async')
    sources: dict[str, str] = {}
    hashes = {}
    widgets = set()
    raw_sources, boundaries = discover_sources(
        root, entry_path, config.get('files', []), config.get('bff', []),
        discover=config.get('discover-imports', True),
    )
    for name, source in raw_sources.items():
        if name in boundaries:
            sources[name] = _bff_stub(name, source)
            continue
        for imported, _ in imported_modules(name, source):
            if imported == 'dhxpyt' or imported.startswith('dhxpyt.'):
                widgets.add(imported.split('.')[1] if '.' in imported else 'layout')
        try:
            sources[name] = adapt(source)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f'{name}: {exc}') from exc
        hashes[name] = hashlib.sha256(source.encode()).hexdigest()
    # Empty package markers support nested modules without publishing server __init__ code.
    for name in list(sources):
        parents = PurePosixPath(name).parents
        for parent in parents:
            if str(parent) != '.':
                sources.setdefault(str(parent / '__init__.py'), '')
    artifacts: dict[str, bytes] = {}
    wheel_digest = None
    if widgets or entry_kind == 'mainwindow':
        widgets.add('layout')
        widgets.discard('theme')
        wheel = _widget_wheel(config, config_file.parent)
        wheel_digest = hashlib.sha256(wheel).hexdigest()
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            for name in archive.namelist():
                if name.endswith('/'):
                    continue
                _relative(name)
                parts = name.split('/')
                if name.startswith('dhxpyt/') and name.endswith('.py'):
                    sources[name] = adapt(archive.read(name).decode(), widget=True, widgets=widgets)
                if name.startswith('dhxpyt/dhxsrc/'):
                    artifacts['vendor/dhxpyt/' + name.removeprefix('dhxpyt/dhxsrc/')] = archive.read(name)
                if '.dist-info/' in name and 'license' in name.lower():
                    artifacts['vendor/dhxpyt/' + Path(name).name] = archive.read(name)

        for name in ('material-icons.woff2', 'MATERIAL-ICONS-LICENSE'):
            artifacts['vendor/material-icons/' + name] = (VENDOR / name).read_bytes()
        artifacts['vendor/material-icons/material-icons.css'] = b'@font-face{font-family:"Material Icons";font-style:normal;font-weight:400;src:url("material-icons.woff2") format("woff2")} .material-icons{font-family:"Material Icons";font-weight:normal;font-style:normal;font-size:24px;line-height:1;letter-spacing:normal;text-transform:none;display:inline-block;white-space:nowrap;word-wrap:normal;direction:ltr;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;}'

    dependency_hashes = {}
    for wheel_name in config.get('wheels', []):
        wheel_path = (config_file.parent / wheel_name).resolve()
        dependency_hashes[wheel_path.name] = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        with zipfile.ZipFile(wheel_path) as archive:
            for info in archive.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                _relative(name)
                if name.endswith(('.so', '.pyd', '.dylib')):
                    raise ValueError(f'{wheel_path.name}: native extensions cannot run in MicroPython')
                if name.endswith('.py') and '.dist-info/' not in name:
                    _module(name)
                    if name in sources or name.split('/')[0].startswith('_pytincture_') or name.startswith('dhxpyt/'):
                        raise ValueError(f'Duplicate or reserved wheel module: {name}')
                    if info.file_size > 4 * 1024 * 1024:
                        raise ValueError(f'Wheel module exceeds 4 MiB: {name}')
                    source = archive.read(name).decode()
                    if has_bff_export_class(name, source=source):
                        raise ValueError(f'Browser wheel contains backend code: {name}')
                    sources[name] = adapt(source)
    for name in config.get('assets', []):
        artifacts['assets/' + _relative(name)] = _read(root, name)
    for template in ('compat', 'dataclasses', 'logging', 'inspect'):
        sources[f'_pytincture_{template}.py'] = (TEMPLATES / f'{template}.py.txt').read_text()
    bootstrap = f'import js\nfrom {module} import {entry_name} as entry\nfrom _pytincture_compat import finish_startup\napp = None\n\nasync def main():\n    global app\n'
    if entry_kind != 'async':
        bootstrap += '    app = entry()\n'
    else:
        bootstrap += '    app = await entry()\n'
    bootstrap += '    await finish_startup()\n    js.window.pytinctureAppReady = True\n'
    sources['_pytincture_bootstrap.py'] = bootstrap
    _check_imports(sources, root)
    if len(sources) > 256 or sum(len(value.encode()) for value in sources.values()) > 8 * 1024 * 1024:
        raise ValueError('Browser source bundle exceeds 256 files or 8 MiB')
    micropython = (config_file.parent / config['micropython-assets']).resolve() if config.get('micropython-assets') else VENDOR
    for name in ('micropython.mjs', 'micropython.wasm'):
        artifacts['vendor/micropython/' + name] = _read(micropython, name)
    artifacts['vendor/micropython/LICENSE'] = (VENDOR / 'MICROPYTHON-LICENSE').read_bytes()
    heap = config.get('heap-bytes', 16 * 1024 * 1024)
    if type(heap) is not int or not 1024 * 1024 <= heap <= 128 * 1024 * 1024:
        raise ValueError('heap-bytes must be between 1 and 128 MiB')
    manifest = {
        'schema': 1, 'runtimes': ['pyodide', 'micropython'], 'host': 'host.js',
        'scripts': ['vendor/dhxpyt/' + name for name in ('suite.js', 'cardflow.js', 'cardpanel.js', 'chat.js', 'kanban.js', 'kanban_board.js', 'ragwidget.js', 'theme.js', 'webgpu.js') if 'vendor/dhxpyt/' + name in artifacts] if widgets else [],
        'styles': ['vendor/dhxpyt/suite.css', 'vendor/dhxpyt/fonts/inter.css', 'vendor/dhxpyt/dhx_custom.css'] + (['vendor/dhxpyt/kanban.css'] if 'vendor/dhxpyt/kanban.css' in artifacts else []) if widgets else [],
        'entrypoint': '_pytincture_bootstrap', 'sources': 'sources.json',
        'micropython': {'module': 'vendor/micropython/micropython.mjs', 'wasm': 'vendor/micropython/micropython.wasm', 'heapBytes': heap},
    }
    for kind in ('scripts', 'styles'):
        manifest[kind].extend('assets/' + _relative(name) for name in config.get(kind, []))
    for asset in manifest['scripts'] + manifest['styles']:
        if asset not in artifacts:
            raise ValueError(f'Widget wheel is missing {asset}')
    entry_path = module.replace('.', '/') + '.py'
    if entry_path not in sources:
        entry_path = module.replace('.', '/') + '/__init__.py'
    title = find_app_string_setting(
        entry_path, ('APP_TITLE', 'APP_LOADING_TITLE'), ('title', 'loading_title'),
        source_code=sources[entry_path],
    ) or entry_name
    artifacts['host.js'] = (TEMPLATES / 'host.js.txt').read_text().replace(
        '__PYTINCTURE_APP_TITLE__', json.dumps(title),
    ).replace('__PYTINCTURE_WIDGETS__', 'true' if widgets else 'false').encode()
    artifacts['sources.json'] = json.dumps({'files': sources}, ensure_ascii=False).encode()
    artifacts['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    artifacts['build.json'] = (json.dumps({'entrypoint': entry, 'source_files': len(sources), 'application_sources': hashes, 'widget_wheel_sha256': wheel_digest, 'dependency_wheels': dependency_hashes, 'bff_modules': sorted(boundaries), 'runtime_sha256': {name: hashlib.sha256(content).hexdigest() for name, content in artifacts.items() if name.startswith('vendor/micropython/')}}, indent=2) + '\n').encode()
    if check:
        return output / 'manifest.json'
    # Complete validation before touching an existing working bundle.
    output.mkdir(parents=True, exist_ok=True)
    for name in artifacts:
        target = output / name
        if not target.resolve().is_relative_to(output.resolve()):
            raise ValueError(f'Browser output asset escapes destination: {name}')
    with tempfile.TemporaryDirectory(prefix='pytincture-browser-') as temporary:
        stage = Path(temporary)
        for name, content in artifacts.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for name in artifacts:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / name, target)
    return output / 'manifest.json'


def main() -> None:
    parser = argparse.ArgumentParser(description='Build an experimental Pyodide/MicroPython browser application.')
    parser.add_argument('--application', help='App name in a multi-app build configuration')
    parser.add_argument('--all', action='store_true', help='Build every configured application')
    parser.add_argument('--check', action='store_true', help='Validate inputs without writing output')
    parser.add_argument('--config', default='pyproject.toml', help='TOML file containing [tool.pytincture.browser]')
    args = parser.parse_args()
    try:
        if args.all:
            config = tomllib.loads(Path(args.config).read_text())['tool']['pytincture']['browser']
            names = list(config.get('apps', {})) or [None]
        else:
            names = [args.application]
        for name in names:
            result = build_browser_bundle(args.config, application=name, check=args.check)
            print(('Validated: ' if args.check else '') + str(result))
    except (KeyError, ValueError, OSError, SyntaxError, zipfile.BadZipFile) as error:
        parser.exit(2, f'Browser build failed: {error}\n')


if __name__ == '__main__':
    main()
