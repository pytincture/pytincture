"""Explicit server-only boundaries for portable builds; no application execution."""
import ast
import keyword


def excluded_module(name, boundaries):
    return any(name == boundary or name.startswith(boundary + '.') for boundary in boundaries)


def excluded_path(name, boundaries):
    """Match source files and package data without matching adjacent packages."""
    parts = name.split('/')
    if parts[-1] == '__init__.py':
        parts.pop()
    elif parts[-1].endswith('.py'):
        parts[-1] = parts[-1][:-3]
    elif parts[-1].endswith(('.so', '.pyd', '.dylib', '.pyc', '.pyo')):
        parts[-1] = parts[-1].split('.')[0]
    else:
        # A resource belongs to its containing package; "yaml.json" is not
        # the Python module yaml, nor is "apps/manifest_loader.css" a child.
        parts.pop()
    return excluded_module('.'.join(parts), boundaries)


def validate_server_only_imports(values):
    if not isinstance(values, list) or any(
        not isinstance(name, str) or not name or any(
            not part.isidentifier() or keyword.iskeyword(part) for part in name.split('.'))
        for name in values
    ):
        raise ValueError('server-only-imports must be a list of Python module names')
    if any(name.split('.')[0].startswith('_pytincture_') for name in values):
        raise ValueError('server-only-imports cannot exclude reserved framework modules')
    return tuple(sorted(set(values)))


def import_names(node, filename):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        package = filename.split('/')[:-node.level] if node.level else []
        base = '.'.join([*package, node.module or '']).rstrip('.')
        return [base, *['.'.join(filter(None, (base, alias.name)))
                        for alias in node.names if alias.name != '*']]
    return []


def excluded_import(node, filename, boundaries):
    if not boundaries:
        return None
    return next((name for name in import_names(node, filename)
                 if excluded_module(name, boundaries)), None)


def guard_server_only_imports(source, *, filename, boundaries, report):
    """Retain imports, but make their declared absence deterministic in both engines.

    Require a local lexical fallback, including inside deferred functions: a
    static builder cannot prove which callbacks will execute during startup.
    A try around a function *definition* does not protect its later execution.
    """
    from pytincture.browser_profile import _import_tree

    class Guard(ast.NodeTransformer):
        protected = False

        def block(self, statements, protected):
            previous = self.protected
            self.protected = protected
            try:
                return self.visit(ast.Module(body=statements, type_ignores=[])).body
            finally:
                self.protected = previous

        def visit_Try(self, node):
            def catches(error):
                return (error is None or isinstance(error, ast.Name)
                        and error.id in {'ImportError', 'Exception', 'BaseException'}
                        or isinstance(error, ast.Tuple) and any(catches(item) for item in error.elts))
            protected = self.protected or any(catches(handler.type) for handler in node.handlers)
            node.body = self.block(node.body, protected)
            # These execute outside this try's handlers, but an outer try applies.
            node.handlers = [self.visit(handler) for handler in node.handlers]
            node.orelse = self.block(node.orelse, self.protected)
            node.finalbody = self.block(node.finalbody, self.protected)
            return node

        def visit_FunctionDef(self, node):
            node.body = self.block(node.body, False)
            return node

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node):
            # Split multi-imports so earlier imports retain their side effects.
            result = []
            for alias in node.names:
                item = ast.copy_location(ast.Import(names=[alias]), node)
                result.extend(self.boundary(item))
            return result

        def visit_ImportFrom(self, node):
            return self.boundary(node)

        def boundary(self, node):
            module = excluded_import(node, filename, boundaries)
            if module is None:
                return [node]
            finding = {'file': filename, 'line': node.lineno, 'rule': 'server-only-import',
                       'severity': 'transformation' if self.protected else 'error',
                       'message': f'{module} is excluded by server-only-imports; ' + (
                           'preserve the import and raise ImportError before loading it' if self.protected else
                           'unguarded browser import: wrap it in try/except ImportError with a browser fallback')}
            report.append(finding)
            if not self.protected:
                raise ValueError(f'{filename}:{node.lineno}: {finding["message"]}')
            finding['behavior_changing'] = True
            error = ast.Raise(exc=ast.Call(func=ast.Name(id='ImportError', ctx=ast.Load()),
                              args=[ast.Constant(f'{module} is server-only (server-only-imports)')], keywords=[]), cause=None)
            return [ast.copy_location(error, node), node]

    tree = Guard().visit(_import_tree(source))
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n'
