"""Build application import graphs for Pyodide and MicroPython.

Application and backend modules are parsed, never imported or executed.
"""
from __future__ import annotations

import argparse
import base64
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
from pytincture.browser_profile import (PROFILE, MICROPYTHON_MODULES, pyodide_modules,
                                        import_source, inspect_source, verified_stdlib, CompatibilityError, guard_dynamic_imports)
from pytincture.browser_assets import canonical_json, audit_assets, seal_manifest, inspect_bundle
from pytincture.backend.pages import find_app_string_setting, find_main_window_subclass, entrypoint_definitions
from pytincture.browser_sources import (relative_path as _relative, read_source as _read,
                                        module_name as _module, discover_sources, imported_modules,
                                        validate_import_aliases, browser_source_path, browser_source, bridge_widget_imports)
from pytincture.dataclass import get_bff_manifest, has_bff_export_class

TEMPLATES = Path(__file__).with_name('browser_templates')
VENDOR = Path(__file__).with_name('browser_vendor')


def _widget_wheel(config, directory, package="dhxpyt"):
    if config.get('widget-wheel'):
        return (directory / config['widget-wheel']).resolve().read_bytes()
    try:
        distribution = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(f'Install {package} in the build environment or set widget-wheel') from exc
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name in sorted(distribution.files or [], key=str):
            relative = str(name)
            if relative.startswith(package + '/') and '__pycache__' not in relative:
                # Fixed metadata makes installed-package builds reproducible.
                archive.writestr(zipfile.ZipInfo(relative), Path(distribution.locate_file(name)).read_bytes())
            elif '.dist-info/' in relative and 'license' in relative.lower():
                archive.writestr(zipfile.ZipInfo(relative), Path(distribution.locate_file(name)).read_bytes())
    return result.getvalue()

def _bff_stub(name: str, source: str) -> str:
    """Generate async session stubs from public signatures, never method bodies."""
    operations = get_bff_manifest(name, source=source)
    async_methods = {(cls.name, method.name) for cls in ast.parse(source).body
                     if isinstance(cls, ast.ClassDef) for method in cls.body
                     if isinstance(method, ast.AsyncFunctionDef)}
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
    stream_name = '_pytincture_Stream'
    while stream_name in parameter_names:
        stream_name += '_'
    sentinel = '_UNSET'
    while sentinel in parameter_names:
        sentinel += '_'
    for (class_name, method), operation in operations.items():
        classes.setdefault(class_name, [])
        if operation.get('external') or operation['kind'] != 'method':
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
        call_args = f'{target!r}, {class_name!r}, {method!r}, {json_name}.dumps({payload_name}){method_option}'
        declaration = ", ".join(signature)
        if operation.get('stream', {}).get('enabled'):
            raw = operation['stream'].get('raw', False)
            stream_body = body + [f'        return {stream_name}({target!r}, {class_name!r}, {method!r}, {payload_name}, raw={raw!r}, http_method={http_method!r})']
            classes[class_name].append(f'    def {method}({declaration}):\n' + '\n'.join(stream_body))
        else:
            async_body = body + [f'        return {json_name}.loads(await {js_name}.pytinctureBrowserBff({call_args}))']
            classes[class_name].append(f'    async def {method}_async({declaration}):\n' + '\n'.join(async_body))
            if (class_name, method) in async_methods:
                classes[class_name].append(f'    {method} = {method}_async')
            else:
                sync_body = body + [f'        return {json_name}.loads({js_name}.pytinctureBrowserBffSync({call_args}))']
                classes[class_name].append(f'    def {method}({declaration}):\n' + '\n'.join(sync_body))
    if not classes:
        raise ValueError(f'No supported session methods found in {name}')
    return f'from _pytincture_bff import Stream as {stream_name}\nimport json as {json_name}\nimport js as {js_name}\n{sentinel} = object()\n\n' + '\n\n'.join(
        f'class {name}:\n' + ('\n\n'.join(methods) or '    pass') for name, methods in classes.items()) + '\n'


def _check_imports(sources: dict[str, str], root: Path, vendor_modules=(), engine="micropython") -> None:
    """Catch missing local sources and known unsupported imports before serving."""
    native = MICROPYTHON_MODULES if engine == 'micropython' else pyodide_modules()
    available = {_module(name) for name in sources}
    for name, source in sources.items():
        if name.startswith('_pytincture_') or name in vendor_modules:
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


def _prepare_browser_bundle(config_file, *, application=None, engine="micropython", report=None):
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
               'assets', 'scripts', 'styles', 'heap-bytes', 'discover-imports', 'widget-package',
               'runtimes', 'resources', 'dynamic-imports', 'external-origins', 'required-browser-apis', 'import-aliases'}
    if set(config) - allowed:
        raise ValueError(f'Unknown browser build settings: {sorted(set(config) - allowed)}')
    for key in ('files', 'bff', 'wheels', 'assets', 'scripts', 'styles', 'resources', 'dynamic-imports', 'required-browser-apis'):
        values = config.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f'{key} must be a list of paths')
    if type(config.get('discover-imports', True)) is not bool:
        raise ValueError('discover-imports must be a boolean')
    root = (config_file.parent / config.get('modules-path', '.')).resolve()
    import_aliases = validate_import_aliases(config.get('import-aliases', {}))
    for target in import_aliases.values():
        if browser_source_path(root, target, import_aliases) is None:
            raise ValueError(f'Browser import substitute is missing: {target}')
    app_name = config.get('application') or application or config.get('entrypoint', '').split(':')[0].split('.')[0]
    output = root / _relative(config.get('output', f'browser/{app_name}'))
    if not output.resolve().is_relative_to(root):
        raise ValueError('Browser output escapes modules-path')
    entry = config.get('entrypoint')
    if not entry:
        app_name = config.get('application') or application
        if not app_name:
            raise ValueError('Set application or entrypoint in the browser build configuration')
        source, _ = browser_source(root, app_name + '.py', import_aliases)
        entry_name = find_app_string_setting(app_name + '.py', ('APP_ENTRYPOINT',), ('entrypoint',), source_code=source) or find_main_window_subclass(app_name + '.py', source_code=source)
        if not entry_name:
            raise ValueError(f'Cannot find an entrypoint for {app_name}')
        entry = app_name + ':' + entry_name
    module, separator, entry_name = entry.partition(':')
    if not separator or any(not part.isidentifier() or keyword.iskeyword(part) for part in [*module.split('.'), entry_name]):
        raise ValueError('entrypoint must be module:ClassName or module:async_function')
    resolved_entry = browser_source_path(root, module, import_aliases)
    if resolved_entry is None:
        raise ValueError(f'Entrypoint module is missing: {module}')
    entry_path = resolved_entry[0]
    entry_source, _ = browser_source(root, entry_path, import_aliases)
    definition = entrypoint_definitions(ast.parse(entry_source)).get(entry_name)
    if definition is None:
        raise ValueError(f'Entrypoint {entry} must name a top-level class or function')
    inferred_kind = 'async' if isinstance(definition, ast.AsyncFunctionDef) else 'callable'
    entry_kind = config.get('entry-kind', inferred_kind)
    if entry_kind not in {'mainwindow', 'async', 'callable'}:
        raise ValueError('entry-kind must be mainwindow, callable or async')
    def convert(name, source, *, widget=False, widgets=()):
        if widget:
            source = bridge_widget_imports(source)
            report['findings'].append({'file': name, 'line': 0, 'severity': 'transformation',
                                      'rule': 'widget-asset-bridge', 'behavior_changing': True,
                                      'message': 'Widget JS imports use the scoped bridge; verified loaded asset bytes are not executed/injected twice'})
        findings = inspect_source(name, source, engine, explicit_dynamic=config.get('dynamic-imports', []))
        report['findings'].extend(findings)
        if any(f['severity'] == 'error' for f in findings):
            raise ValueError(f"{name}: " + '; '.join(f['message'] for f in findings if f['severity'] == 'error'))
        if config.get('dynamic-imports'):
            source = guard_dynamic_imports(source, engine=engine, report=report['findings'], filename=name)
        if engine == 'pyodide':
            result = import_source(source)
            report['findings'].append({'file': name, 'line': 0, 'severity': 'supported',
                                      'rule': 'cpython-source', 'message': 'CPython syntax/stdlib preserved; unreachable __main__ launch blocks removed'})
            return result
        return adapt(source, widget=widget, widgets=widgets, report=report['findings'], filename=name)
    sources: dict[str, str] = {}
    hashes = {}
    widgets = set()
    widget_package = config.get('widget-package', 'dhxpyt')
    if not isinstance(widget_package, str) or not widget_package.isidentifier():
        raise ValueError('widget-package must be a Python package name')
    dynamic_files = []
    for name in config.get('dynamic-imports', []):
        if not all(part.isidentifier() for part in name.split('.')):
            raise ValueError('dynamic-imports entries must be literal module names')
        resolved = browser_source_path(root, name, import_aliases)
        if resolved:
            dynamic_files.append(resolved[0])
    substitutions = {}
    raw_sources, boundaries = discover_sources(
        root, entry_path, [*config.get('files', []), *dynamic_files], config.get('bff', []),
        discover=config.get('discover-imports', True),
        import_aliases=import_aliases, substitutions=substitutions,
    )
    report['import_substitutions'] = substitutions
    report['findings'].extend({'file': name, 'line': 0, 'severity': 'transformation',
                              'rule': 'import-substitution', 'behavior_changing': True,
                              'message': f'Explicit browser implementation: {physical} (module identity remains {_module(name)})'}
                             for name, physical in sorted(substitutions.items()))
    for name, source in raw_sources.items():
        if name in boundaries:
            sources[name] = _bff_stub(name, source)
            continue
        for imported, _ in imported_modules(name, source):
            if imported == widget_package or imported.startswith(widget_package + '.'):
                widgets.add(imported.split('.')[1] if '.' in imported else 'layout')
        try:
            sources[name] = convert(name, source)
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
    widget_scripts, widget_styles = [], []
    asset_hook = None
    if widgets or entry_kind == 'mainwindow':
        widgets.add('layout')
        widgets.discard('theme')
        wheel = _widget_wheel(config, config_file.parent, widget_package)
        wheel_digest = hashlib.sha256(wheel).hexdigest()
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            if len(archive.infolist()) > 4096 or sum(i.file_size for i in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError('Widget wheel exceeds 4096 entries or 128 MiB uncompressed')
            for name in archive.namelist():
                if archive.getinfo(name).file_size > 32 * 1024 * 1024:
                    raise ValueError('Widget wheel asset exceeds 32 MiB: ' + name)
                if name.endswith('/') or any(part.startswith('.') for part in name.split('/')):
                    continue
                _relative(name)
                parts = name.split('/')
                if name.startswith(widget_package + '/') and name.endswith('.py'):
                    if name in sources:
                        raise ValueError(f'Duplicate widget module: {name}')
                    source = archive.read(name).decode()
                    if has_bff_export_class(name, source=source):
                        raise ValueError(f'Widget wheel contains backend code: {name}')
                    sources[name] = convert(name, source, widget=True, widgets=widgets)
                if name.startswith(widget_package + '/') and not name.endswith('.py'):
                    artifacts['vendor/' + name] = archive.read(name)
                if name.startswith('dhxpyt/dhxsrc/'):
                    artifacts['vendor/dhxpyt/' + name.removeprefix('dhxpyt/dhxsrc/')] = archive.read(name)
                if '.dist-info/' in name and 'license' in name.lower():
                    artifacts['vendor/' + widget_package + '/' + Path(name).name] = archive.read(name)

        manifest_name = 'vendor/' + widget_package + '/pytincture-assets.json'
        if widget_package != 'dhxpyt' and manifest_name not in artifacts:
            raise ValueError('Widget package requires pytincture-assets.json')
        if manifest_name in artifacts:
            metadata = json.loads(artifacts[manifest_name])
            if metadata.get('schema') != 1 or metadata.get('package') != widget_package:
                raise ValueError('Invalid widget asset manifest')
            asset_hook = metadata.get('asset_loader')
            for asset in metadata['assets']:
                path = 'vendor/' + _relative(asset['path'])
                if not asset['path'].startswith(widget_package + '/') or path not in artifacts:
                    raise ValueError('Widget manifest asset is missing or outside its package')
                if hashlib.sha256(artifacts[path]).hexdigest() != asset['sha256']:
                    raise ValueError(f'Widget manifest hash mismatch: {path}')
                if asset['type'] == 'css':
                    widget_styles.append(path)
                elif asset['type'] == 'javascript':
                    widget_scripts.append(path)
                else:
                    raise ValueError('Unsupported widget manifest asset type')

        for name in ('material-icons.woff2', 'MATERIAL-ICONS-LICENSE'):
            artifacts['vendor/material-icons/' + name] = (VENDOR / name).read_bytes()
        artifacts['vendor/material-icons/material-icons.css'] = b'@font-face{font-family:"Material Icons";font-style:normal;font-weight:400;src:url("material-icons.woff2") format("woff2")} .material-icons{font-family:"Material Icons";font-weight:normal;font-style:normal;font-size:24px;line-height:1;letter-spacing:normal;text-transform:none;display:inline-block;white-space:nowrap;word-wrap:normal;direction:ltr;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;}'

    if widgets:
        icon_root = Path(__file__).with_name('frontend')/'vendor/materialdesignicons'
        for name in ('LICENSE', 'materialdesignicons.css', 'materialdesignicons.css.map', 'fonts/materialdesignicons-webfont.woff2'):
            artifacts['vendor/materialdesignicons/'+name] = _read(icon_root, name)

    dependency_hashes = {}
    for wheel_name in config.get('wheels', []):
        wheel_path = (config_file.parent / wheel_name).resolve()
        dependency_hashes[wheel_path.name] = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        with zipfile.ZipFile(wheel_path) as archive:
            if len(archive.infolist()) > 4096 or sum(i.file_size for i in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError('Dependency wheel exceeds 4096 entries or 128 MiB uncompressed')
            for info in archive.infolist():
                name = info.filename
                if info.file_size > 32 * 1024 * 1024:
                    raise ValueError('Dependency wheel asset exceeds 32 MiB: ' + name)
                if info.is_dir():
                    continue
                _relative(name)
                if name.endswith(('.so', '.pyd', '.dylib')):
                    raise ValueError(f'{wheel_path.name}: native extensions require a compatible legacy browser package installation')
                if name.endswith('.py') and '.dist-info/' not in name:
                    _module(name)
                    if name in sources or name.split('/')[0].startswith('_pytincture_') or name.startswith('dhxpyt/'):
                        raise ValueError(f'Duplicate or reserved wheel module: {name}')
                    if info.file_size > 4 * 1024 * 1024:
                        raise ValueError(f'Wheel module exceeds 4 MiB: {name}')
                    source = archive.read(name).decode()
                    if has_bff_export_class(name, source=source):
                        raise ValueError(f'Browser wheel contains backend code: {name}')
                    sources[name] = convert(name, source)
                elif '.dist-info/' not in name:
                    artifacts['vendor/' + name] = archive.read(name)
                elif 'license' in name.lower():
                    artifacts['licenses/' + wheel_path.stem + '/' + Path(name).name] = archive.read(name)
    for name in config.get('resources', []):
        if name.endswith(('.py', '.pyc', '.pyo')):
            raise ValueError('Package resources cannot contain Python source or bytecode; use files/BFF discovery')
        artifacts['vendor/' + _relative(name)] = _read(root, name)
    for name in config.get('assets', []):
        if name.endswith(('.py', '.pyc', '.pyo')):
            raise ValueError('Public assets cannot contain Python source or bytecode; use files/BFF discovery')
        artifacts['assets/' + _relative(name)] = _read(root, name)
    for template in ('compat', 'dataclasses', 'logging', 'inspect', 'bff', 'resources'):
        sources[f'_pytincture_{template}.py'] = (TEMPLATES / f'{template}.py.txt').read_text()
        findings = inspect_source(f'_pytincture_{template}.py', sources[f'_pytincture_{template}.py'], engine)
        report['findings'].extend(findings)
        if any(f['severity'] == 'error' for f in findings):
            raise ValueError('Framework shim failed profile validation: ' + template + ': ' + '; '.join(f['message'] for f in findings))
    hook_source = ''
    if config.get('dynamic-imports'):
        sources['_pytincture_imports.py'] = ('ALLOWED_MODULES = ' + repr(tuple(sorted(set(config['dynamic-imports'])))) + '\n'
                                            + (TEMPLATES / 'imports.py.txt').read_text())
    if asset_hook is not None:
        if (not isinstance(asset_hook, dict) or set(asset_hook) != {'module', 'function'}
                or not all(isinstance(value, str) for value in asset_hook.values())
                or not all(part.isidentifier() and not keyword.iskeyword(part) for part in [*asset_hook['module'].split('.'), asset_hook['function']])):
            raise ValueError('Widget asset_loader must declare a Python module and function')
        hook_path = next((asset_hook['module'].replace('.', '/')+suffix for suffix in ('.py', '/__init__.py')
                          if asset_hook['module'].replace('.', '/')+suffix in sources), None)
        if hook_path is None:
            raise ValueError('Widget asset_loader module is missing')
        hook = next((node for node in ast.parse(sources[hook_path]).body
                     if isinstance(node, ast.FunctionDef) and node.name == asset_hook['function']), None)
        if hook is None or len(hook.args.posonlyargs + hook.args.args) > len(hook.args.defaults) or any(value is None for value in hook.args.kw_defaults):
            raise ValueError('Widget asset_loader must name a top-level synchronous function callable without arguments')
        hook_source = f"from {asset_hook['module']} import {asset_hook['function']} as _adopt_assets\n_adopt_assets()\n"
    bootstrap = f'import js\n{hook_source}from {module} import {entry_name} as entry\nfrom _pytincture_compat import finish_startup\napp = None\n\nasync def main():\n    global app\n'
    if entry_kind != 'async':
        bootstrap += '    app = entry()\n'
    else:
        bootstrap += '    app = await entry()\n'
    bootstrap += '    await finish_startup()\n    js.window.pytinctureAppReady = True\n'
    sources['_pytincture_bootstrap.py'] = bootstrap
    vendor_modules = set()
    imports = {module.split('.')[0] for name, source in sources.items() for module, _ in imported_modules(name, source)}
    for stdlib in (('copy', 'datetime') if engine == 'micropython' else ()):
        if stdlib in imports and stdlib + '.py' not in sources:
            vendor_modules.add(stdlib + '.py')
            sources[stdlib + '.py'] = verified_stdlib(stdlib)
    if 'copy.py' in vendor_modules:
        if 'types.py' not in sources:
            vendor_modules.add('types.py')
        sources.setdefault('types.py', verified_stdlib('types'))
    if 'copy' in imports or 'datetime' in imports:
        artifacts['vendor/micropython-lib/LICENSE'] = (VENDOR / 'stdlib/LICENSE').read_bytes()
    report['shims'] = sorted(vendor_modules | {name for name in sources if name.startswith('_pytincture_') and name not in {'_pytincture_bootstrap.py', '_pytincture_bff.py'}}) if engine == 'micropython' else []
    _check_imports({**sources, 'declared_dynamic_imports.py': '\n'.join('import '+name for name in config.get('dynamic-imports', []))}, root, vendor_modules, engine)
    if len(sources) > 256 or sum(len(value.encode()) for value in sources.values()) > 8 * 1024 * 1024:
        raise ValueError('Browser source bundle exceeds 256 files or 8 MiB')
    micropython = (config_file.parent / config['micropython-assets']).resolve() if config.get('micropython-assets') else VENDOR
    runtime_inventory = json.loads((VENDOR/'inventory.json').read_text())['packages'][0]['files']
    for name in ('micropython.mjs', 'micropython.wasm'):
        content = _read(micropython, name)
        if hashlib.sha256(content).hexdigest() != runtime_inventory[name]:
            raise ValueError(f'{name}: runtime bytes do not match the pinned portable profile')
        artifacts['vendor/micropython/' + name] = content
    artifacts['vendor/micropython/LICENSE'] = (VENDOR / 'MICROPYTHON-LICENSE').read_bytes()
    heap = config.get('heap-bytes', 16 * 1024 * 1024)
    if type(heap) is not int or not 1024 * 1024 <= heap <= 128 * 1024 * 1024:
        raise ValueError('heap-bytes must be between 1 and 128 MiB')
    manifest = {
        'schema': 2, 'runtimes': [engine], 'host': 'host.js', 'profile': PROFILE,
        'widgetPackages': [widget_package] if widgets else [], 'assetLoader': asset_hook,
        'scripts': ['vendor/dhxpyt/' + name for name in ('suite.js', 'cardflow.js', 'cardpanel.js', 'chat.js', 'kanban.js', 'kanban_board.js', 'ragwidget.js', 'theme.js', 'webgpu.js') if 'vendor/dhxpyt/' + name in artifacts] if widgets else [],
        'styles': ['vendor/dhxpyt/suite.css', 'vendor/dhxpyt/fonts/inter.css', 'vendor/dhxpyt/dhx_custom.css'] + (['vendor/dhxpyt/kanban.css'] if 'vendor/dhxpyt/kanban.css' in artifacts else []) if widgets else [],
        'entrypoint': '_pytincture_bootstrap', 'sources': 'sources.json',
        'micropython': {'module': 'vendor/micropython/micropython.mjs', 'wasm': 'vendor/micropython/micropython.wasm', 'heapBytes': heap},
    }
    if widgets and widget_package != 'dhxpyt':
        manifest['scripts'] = widget_scripts
        manifest['styles'] = widget_styles
    if widgets:
        manifest['styles'].extend(['vendor/material-icons/material-icons.css', 'vendor/materialdesignicons/materialdesignicons.css'])
        manifest['styleIds'] = {'vendor/material-icons/material-icons.css': 'ragchat-material-icons'}
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
    ).encode()
    resources = {name.removeprefix('vendor/'): base64.b64encode(content).decode()
                 for name, content in sorted(artifacts.items())
                 if name.startswith('vendor/' + widget_package + '/') or
                    (name.startswith('vendor/') and name.removeprefix('vendor/') in config.get('resources', [])) or
                    (name.startswith('vendor/') and name.split('/')[1] not in {'dhxpyt', 'material-icons', 'materialdesignicons', 'micropython', 'micropython-lib'})}
    source_name = 'sources.json' if engine == 'micropython' else 'sources-pyodide.json'
    artifacts[source_name] = canonical_json({'files': sources})
    # CPython resources are installed in its real filesystem, preserving importlib.resources.
    artifacts['resources.json'] = canonical_json({'files': resources})
    manifest['sources'] = source_name
    manifest['resources'] = 'resources.json'
    manifest['requiredBrowserApis'] = ['fetch', 'WebAssembly', 'crypto.subtle', *config.get('required-browser-apis', [])]
    if any(not all(part.isidentifier() for part in name.split('.')) for name in manifest['requiredBrowserApis']):
        raise ValueError('required-browser-apis entries must be browser property paths')
    manifest['runtimeRequirements'] = {'pyodide': '0.29.3', 'micropython': '1.29.0-6'}
    report['source_files'] = len(sources)
    report['source_bytes'] = sum(len(value.encode()) for value in sources.values())
    report['native_modules'] = sorted(MICROPYTHON_MODULES if engine == 'micropython' else pyodide_modules())
    report['asset_audit'] = audit_assets(artifacts, config.get('external-origins'))
    report['findings'].extend(report['asset_audit']['findings'])
    artifacts['build.json'] = canonical_json({'entrypoint': entry, 'source_files': len(sources),
        'application_sources': hashes, 'widget_wheel_sha256': wheel_digest,
        'dependency_wheels': dependency_hashes, 'bff_modules': sorted(boundaries)})
    return output, manifest, artifacts


def build_browser_bundle(config_file, *, application=None, check=False, report_path=None):
    config_file = Path(config_file).resolve()
    settings = tomllib.loads(config_file.read_text())['tool']['pytincture']['browser']
    applications = settings.get('apps', {})
    if application is None and len(applications) == 1:
        application = next(iter(applications))
    app_settings = applications.get(application, {})
    requested = app_settings.get('runtimes', settings.get('runtimes', ['pyodide', 'micropython']))
    if not isinstance(requested, list) or not requested or any(not isinstance(r, str) or r not in {'pyodide', 'micropython'} for r in requested) or len(set(requested)) != len(requested):
        raise ValueError('runtimes must be a nonempty list of pyodide and/or micropython')
    report = {'schema': 1, 'profile': PROFILE, 'requested_runtimes': requested, 'runtimes': {},
              'limits': {'source_files': 256, 'source_bytes': 8388608, 'bundle_bytes': 134217728},
              'analysis_limits': 'Static validation cannot prove arbitrary dynamic Python or JS; run the conformance suite.'}
    prepared = {}
    for engine in ('pyodide', 'micropython'):
        result = report['runtimes'][engine] = {'status': 'checking', 'findings': [], 'shims': []}
        try:
            prepared[engine] = _prepare_browser_bundle(config_file, application=application, engine=engine, report=result)
            result['status'] = 'supported'
        except (ValueError, OSError, SyntaxError, KeyError, zipfile.BadZipFile) as exc:
            result['status'] = 'unsupported'
            result['findings'].append({'severity': 'error', 'rule': 'build-validation', 'message': str(exc)})
    if report_path:
        if str(report_path) == '-':
            print(canonical_json(report).decode(), end='')
        else:
            Path(report_path).write_bytes(canonical_json(report))
    if any(report['runtimes'][engine]['status'] != 'supported' for engine in requested):
        raise CompatibilityError(report)
    output, manifest, artifacts = prepared[requested[0]]
    artifacts = dict(artifacts)
    targets = {}
    for engine in requested:
        _, target, files = prepared[engine]
        artifacts[target['sources']] = files[target['sources']]
        targets[engine] = {'sources': target['sources']}
    manifest.update(runtimes=requested, targets=targets)
    artifacts['compatibility.json'] = canonical_json(report)
    manifest = seal_manifest(manifest, artifacts)
    if check:
        return output / 'manifest.json'
    # Immutable content-addressed revisions. Publish the pointer only after all bytes exist.
    output.mkdir(parents=True, exist_ok=True)
    release = output / manifest['assetBase']
    release.mkdir(parents=True, exist_ok=True)
    if not release.resolve().is_relative_to(output.resolve()):
        raise ValueError('Browser release path escapes output')
    for name, content in artifacts.items():
        target = release / name
        if not target.resolve().is_relative_to(output.resolve()):
            raise ValueError('Browser output asset escapes destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != content:
            raise ValueError('Refusing to overwrite immutable browser bundle: ' + name)
        if not target.exists():
            target.write_bytes(content)
    # Root copies are inspection conveniences; the browser uses only the revision above.
    for name in ('sources.json', 'sources-pyodide.json', 'build.json', 'compatibility.json'):
        if name in artifacts:
            target = output/name
            if not target.resolve().is_relative_to(output.resolve()):
                raise ValueError('Browser inspection path escapes output')
            target.write_bytes(artifacts[name])
    with tempfile.NamedTemporaryFile(dir=output, prefix='.manifest-', delete=False) as temporary:
        temporary.write(canonical_json(manifest))
        pointer = Path(temporary.name)
    pointer.replace(output/'manifest.json')
    return output/'manifest.json'


def main() -> None:
    parser = argparse.ArgumentParser(description='Build an experimental Pyodide/MicroPython browser application.')
    parser.add_argument('--compatibility-report', metavar='PATH', help='Write per-runtime JSON diagnostics; use - for stdout')
    parser.add_argument('--inspect', type=Path, help='Verify an existing immutable bundle manifest')
    parser.add_argument('--application', help='App name in a multi-app build configuration')
    parser.add_argument('--all', action='store_true', help='Build every configured application')
    parser.add_argument('--check', action='store_true', help='Validate inputs without writing output')
    parser.add_argument('--config', default='pyproject.toml', help='TOML file containing [tool.pytincture.browser]')
    args = parser.parse_args()
    try:
        if args.inspect:
            print(canonical_json(inspect_bundle(args.inspect)).decode(), end='')
            return
        if args.all:
            config = tomllib.loads(Path(args.config).read_text())['tool']['pytincture']['browser']
            names = list(config.get('apps', {})) or [None]
        else:
            names = [args.application]
        for name in names:
            result = build_browser_bundle(args.config, application=name, check=args.check, report_path=args.compatibility_report)
            if args.compatibility_report != '-':
                print(('Validated: ' if args.check else '') + str(result))
                print('Use --compatibility-report PATH for per-runtime details.' if args.check else 'Compatibility transformations and exclusions: ' + str(result.parent/'compatibility.json'))
    except (KeyError, ValueError, OSError, SyntaxError, zipfile.BadZipFile) as error:
        parser.exit(2, f'Browser build failed: {error}\n')


if __name__ == '__main__':
    main()
