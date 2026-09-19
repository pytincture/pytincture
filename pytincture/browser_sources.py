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


def imported_modules(name, source):
    """Include both 'from package import child' and relative import shapes."""
    class RuntimeImports(ast.NodeTransformer):
        def visit_If(self, node):
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


def discover_sources(root, entry, files, bff, *, discover=True):
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
        source = read_source(root, name).decode('utf-8')
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
                if str(parent) != '.' and (root / marker).is_file():
                    pending.append(marker)
            for module, _ in imported_modules(name, source):
                path = module.replace('.', '/')
                for candidate in (path + '.py', path + '/__init__.py'):
                    if (root / candidate).is_file():
                        pending.append(candidate)
                        break
    return selected, boundaries
