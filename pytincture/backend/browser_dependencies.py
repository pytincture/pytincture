"""Copy installed pure-Python client dependencies without importing their code."""
import ast
import importlib.metadata as metadata
import sys
from pathlib import PurePosixPath

from packaging.requirements import Requirement

from pytincture.backend.safe_paths import read_contained_file
from pytincture.dataclass import has_bff_export_class


def pure_python_dependencies(sources, *, max_files, max_file_bytes, max_total_bytes, safe_path):
    local = {name.split('/')[0].removesuffix('.py') for name in sources}
    excluded = local | set(sys.stdlib_module_names) | {'js', 'jsffi', 'pyodide', 'micropip', 'pytincture', 'dhxpyt'}
    imports = set()
    for name, source in sources.items():
        if not name.endswith('.py'):
            continue
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                imports.add(node.module.split('.')[0])
    imports -= excluded
    if not imports:
        return {}, []
    owners = metadata.packages_distributions()
    pending = set()
    for module in imports:
        names = owners.get(module, [])
        if len(names) == 1:
            pending.add(names[0])
    files, records, seen = {}, [], set()
    total = 0
    while pending:
        name = min(pending)
        pending.remove(name)
        if name.lower().replace('_', '-') in seen:
            continue
        seen.add(name.lower().replace('_', '-'))
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        wheel = distribution.read_text('WHEEL') or ''
        owned = sorted(str(path).replace('\\', '/') for path in distribution.files or ())
        if 'Root-Is-Purelib: true' not in wheel or any(path.endswith(('.so', '.pyd', '.dylib')) for path in owned):
            continue
        # Widget loading has its own version/trust/integrity path.
        if any(path.endswith('/pytincture-assets.json') for path in owned) or distribution.metadata.get('Pytincture-Widgetset'):
            continue
        root = distribution.locate_file('')
        selected = {}
        for path in owned:
            parts = PurePosixPath(path).parts
            if not parts or parts[0].removesuffix('.py') in excluded or path.endswith(('.pyc', '.pyo')) or '__pycache__' in parts:
                continue
            if '.dist-info' in parts[0]:
                if parts[-1] not in {'METADATA', 'WHEEL', 'LICENSE', 'LICENSE.txt', 'COPYING'} and 'licenses' not in parts:
                    continue
            elif parts[0].endswith('.data'):
                continue
            if not safe_path(path):
                continue
            content = read_contained_file(root, path, max_bytes=max_file_bytes).content
            if path.endswith('.py') and has_bff_export_class(path, source=content.decode('utf-8')):
                raise ValueError(f'Installed client dependency contains backend code: {name}/{path}')
            if path in sources or path in files:
                raise ValueError(f'Client dependency module collision: {path}')
            total += len(content)
            if total > max_total_bytes or len(files) + len(selected) + 1 > max_files:
                raise ValueError('Installed client dependencies exceed the appcode size/file limit')
            selected[path] = content
        files.update(selected)
        records.append({'distribution': distribution.metadata['Name'], 'version': distribution.version,
                        'files': sorted(selected)})
        for requirement in distribution.requires or ():
            requirement = Requirement(requirement)
            if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
                pending.add(requirement.name)
    return files, records
