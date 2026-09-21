"""Static dependency discovery for browser builds; never executes app imports."""
import ast
from pathlib import Path, PurePosixPath
import keyword
import re

from pytincture.dataclass import has_bff_export_class
from pytincture.backend.browser_packages import browser_asset_path_is_safe


def relative_path(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_@./-]+', value):
        raise ValueError(f'Expected a relative public path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p.startswith('.') for p in path.parts):
        raise ValueError(f'Expected a relative public path: {value!r}')
    return value


def read_source(root, name):
    path = root / relative_path(name)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Browser input escapes its root: {name}')
    if not browser_asset_path_is_safe(name):
        raise ValueError(f'Unsafe browser input: {name}')
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f'Browser input exceeds 4 MiB: {name}')
    return path.read_bytes()


def module_name(name):
    relative_path(name)
    if not name.endswith('.py'):
        raise ValueError(f'Browser source must be Python: {name}')
    parts = name[:-3].split('/')
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        raise ValueError(f'Invalid Python module path: {name}')
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def validate_import_aliases(aliases):
    if not isinstance(aliases, dict):
        raise ValueError('import-aliases must map Python module names to browser module names')
    for original, target in aliases.items():
        for name in (original, target):
            if not isinstance(name, str) or not name or any(not p.isidentifier() or keyword.iskeyword(p) for p in name.split('.')):
                raise ValueError('import-aliases must contain Python module names')
            if name.split('.')[0].startswith('_pytincture_'):
                raise ValueError('import-aliases cannot use reserved framework modules')
        if original == target or target.startswith(original + '.'):
            raise ValueError('import-aliases cannot map a module to itself or its descendants')
    for name in aliases:
        aliased_module(name, aliases)
    return aliases


def aliased_module(name, aliases):
    seen = set()
    while True:
        key = next((key for key in sorted(aliases, key=len, reverse=True)
                    if name == key or name.startswith(key + '.')), None)
        if key is None:
            return name
        if key in seen:
            raise ValueError('Cyclic import-aliases are unsupported')
        seen.add(key)
        name = aliases[key] + name[len(key):]


def browser_source_path(root, module, aliases=None):
    target = aliased_module(module, aliases or {}).replace('.', '/')
    for suffix in ('.py', '/__init__.py'):
        if (root / (target + suffix)).is_file():
            return module.replace('.', '/') + suffix, target + suffix
    return None


def browser_source(root, name, aliases=None):
    resolved = browser_source_path(root, module_name(name), aliases)
    physical = resolved[1] if resolved else name
    source = read_source(root, physical).decode('utf-8')
    if physical != name:
        # A substitute executes under the requested module name. Make its
        # relative imports absolute first, preserving the implementation's
        # own package context without importing the original server module.
        tree = ast.parse(source, filename=physical)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                package = physical.split('/')[:-node.level]
                if not package:
                    raise ValueError(f'{physical}: relative import escapes its package')
                node.module = '.'.join([*package, node.module or '']).rstrip('.')
                for original in sorted(aliases or {}, key=len, reverse=True):
                    if module_name(name) == original or module_name(name).startswith(original + '.'):
                        implementation = aliased_module(original, aliases)
                        if node.module == implementation or node.module.startswith(implementation + '.'):
                            node.module = original + node.module[len(implementation):]
                            break
                node.level = 0
        source = ast.unparse(tree) + '\n'
    return source, physical


def bridge_widget_imports(source, *, package=None, report=None, filename="<widget>"):
    """Keep old widget loaders, but give them the host's asset-aware JS view."""
    class Bridge(ast.NodeTransformer):
        def visit_Import(self, node):
            result = []
            for alias in node.names:
                if alias.name == 'js':
                    result.append(ast.ImportFrom(module='js', names=[ast.alias(name='pytinctureWidgetBridge', asname=alias.asname or 'js')], level=0))
                else:
                    result.append(ast.Import(names=[alias]))
            return result

        def visit_ImportFrom(self, node):
            if node.module == 'pyodide.code' and not node.level:
                wrapped = [ast.alias(name='widget_run_js', asname=a.asname or a.name)
                           for a in node.names if a.name == 'run_js']
                rest = [a for a in node.names if a.name != 'run_js']
                if wrapped:
                    return ([ast.ImportFrom(module=node.module, names=rest, level=0)] if rest else []) + [
                        ast.ImportFrom(module='_pytincture_compat', names=wrapped, level=0)]
            if node.module != 'js' or node.level or any(alias.name == '*' for alias in node.names):
                return node
            result = [ast.ImportFrom(module='js', names=[ast.alias(name='pytinctureWidgetBridge', asname='_pytincture_widget_js')], level=0)]
            result.extend(ast.Assign(targets=[ast.Name(id=alias.asname or alias.name, ctx=ast.Store())],
                                     value=ast.Attribute(value=ast.Name(id='_pytincture_widget_js', ctx=ast.Load()), attr=alias.name, ctx=ast.Load()))
                          for alias in node.names)
            return result
    tree = ast.parse(source)
    if package:
        # Compatibility for conventional pre-ownership Widgetsets. Guard inside
        # the function, before any resource reads/encoding (also covers aliases).
        # Do not infer arbitrary application functions to be asset loaders.
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                '_try_inject_inter_fonts', '_try_inject_icon_fonts',
            }:
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                used.update(n.arg for n in ast.walk(node) if isinstance(n, ast.arg))
                helper = '_pytincture_font_host'
                while helper in used:
                    helper += '_'
                guard = ast.parse(f"import js as {helper}\n"
                                  f"if getattr({helper}, 'pytinctureAssets', None) is not None and "
                                  f"{helper}.pytinctureAssets.isPackageReady({package!r}):\n    return\n").body
                index = int(bool(node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(node.body[0].value, ast.Constant)
                                 and isinstance(node.body[0].value.value, str)))
                node.body[index:index] = guard
                if report is not None:
                    report.append({'file': filename, 'line': node.lineno, 'severity': 'transformation',
                                   'rule': 'widget-font-ownership', 'behavior_changing': True,
                                   'message': f'{node.name} skips resource encoding only when {package} assets are ready'})
    return ast.unparse(ast.fix_missing_locations(Bridge().visit(tree))) + '\n'


def main_only(test):
    """An imported module cannot enter a conventional __main__ guard."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(main_only(value) for value in test.values)
    return (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.left, ast.Name) and test.left.id == '__name__'
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == '__main__')


def imported_modules(name, source):
    """Include both 'from package import child' and relative import shapes."""
    class RuntimeImports(ast.NodeTransformer):
        def visit_If(self, node):
            if main_only(node.test):
                return [self.visit(child) for child in node.orelse]
            if ast.unparse(node.test) in {'TYPE_CHECKING', 'typing.TYPE_CHECKING'}:
                return node.orelse
            return self.generic_visit(node)

    tree = RuntimeImports().visit(ast.parse(source, filename=name))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            package = name.split('/')[:-node.level] if node.level else []
            base = '.'.join([*package, node.module or '']).rstrip('.')
            if base:
                yield base, node.lineno
            for alias in node.names:
                if alias.name != '*':
                    yield '.'.join(filter(None, (base, alias.name))), node.lineno


def discover_sources(root, entry, files, bff, *, discover=True, import_aliases=None, substitutions=None):
    selected = {}
    boundaries = set()
    explicit = set(files)
    pending = list(dict.fromkeys([entry, *files, *bff]))
    while pending:
        name = pending.pop(0)
        if name in selected:
            continue
        module_name(name)
        if name.split('/')[0].startswith('_pytincture_') or name.startswith('dhxpyt/'):
            raise ValueError(f'Reserved browser source: {name}')
        source, physical = browser_source(root, name, import_aliases)
        if physical != name and substitutions is not None:
            substitutions[name] = physical
        selected[name] = source
        if len(selected) > 256:
            raise ValueError('Browser source bundle exceeds 256 files')
        if has_bff_export_class(name, source=source):
            if name in explicit:
                raise ValueError(f'{name} contains backend code; declare it in bff instead of files')
            boundaries.add(name)
            # Backend imports and package initializers remain entirely server-side.
            continue
        if name in bff:
            raise ValueError(f'No decorated BFF operations found in {name}')
        if discover:
            for parent in PurePosixPath(name).parents:
                marker = str(parent / '__init__.py')
                if str(parent) != '.':
                    resolved = browser_source_path(root, str(parent).replace('/', '.'), import_aliases)
                    if resolved and resolved[0].endswith('/__init__.py'):
                        pending.append(resolved[0])
            for module, _ in imported_modules(name, source):
                resolved = browser_source_path(root, module, import_aliases)
                if resolved:
                    pending.append(resolved[0])
    return selected, boundaries


def has_nested_fstring(node):
    """Check expressions, not the JoinedStr used for an ordinary format spec."""
    return isinstance(node, ast.FormattedValue) and any(
        isinstance(child, ast.JoinedStr) for child in ast.walk(node.value)
    )
