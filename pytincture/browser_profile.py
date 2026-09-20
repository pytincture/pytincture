"""Versioned, static browser profile. This is validation, not execution or proof."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

from pytincture.browser_sources import main_only

PROFILE = 'pytincture-portable-2'
MICROPYTHON_MODULES = frozenset('js jsffi asyncio array binascii builtins cmath collections gc hashlib heapq io json math micropython os random re select struct sys time errno deflate __main__'.split())
VENDOR = Path(__file__).with_name('browser_vendor')


def pyodide_modules():
    archive = Path(__file__).with_name('frontend')/'pyodide/0.29.3/full/python_stdlib.zip'
    with zipfile.ZipFile(archive) as package:
        names = {name.split('/')[0].removesuffix('.pyc').removesuffix('.py') for name in package.namelist()}
    # Built-in/linked modules of the pinned browser CPython distribution.
    return names | set('js pyodide _pyodide sys builtins math cmath time gc errno array binascii hashlib struct itertools functools operator _thread _io _ast _abc _collections _functools _operator _sre _string _weakref _random _sha2 _socket zlib unicodedata'.split())


def import_source(source):
    """Keep CPython semantics, removing only unreachable server launch guards."""
    class ImportsOnly(ast.NodeTransformer):
        def visit_If(self, node):
            if main_only(node.test) or ast.unparse(node.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
                module = ast.Module(body=node.orelse, type_ignores=[])
                return self.visit(module).body
            return self.generic_visit(node)
    return ast.unparse(ast.fix_missing_locations(ImportsOnly().visit(ast.parse(source))))+'\n'


def guard_dynamic_imports(source, *, engine, report, filename):
    """Guard recognized import_module calls; preserve other CPython importlib APIs."""
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname or a.name: a.name for a in node.names})
        elif isinstance(node, ast.ImportFrom) and not node.level:
            aliases.update({a.asname or a.name: (node.module or '')+'.'+a.name for a in node.names})
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | set(aliases)
    helper = '_pytincture_checked_import'
    while helper in names:
        helper += '_'
    class Guard(ast.NodeTransformer):
        used = False

        def visit_Call(self, node):
            self.generic_visit(node)
            first, _, tail = ast.unparse(node.func).partition('.')
            if aliases.get(first, first) + ('.'+tail if tail else '') == 'importlib.import_module':
                node.func = ast.Name(id=helper, ctx=ast.Load())
                self.used = True
                report.append({'file': filename, 'line': node.lineno, 'rule': 'dynamic-import-guard',
                               'severity': 'transformation', 'behavior_changing': True,
                               'message': 'Resolve and check the resulting module against dynamic-imports before importing'})
            return node

        def visit_Import(self, node):
            if engine == 'micropython':
                for alias in node.names:
                    if alias.name == 'importlib':
                        alias.asname = alias.asname or 'importlib'
                        alias.name = '_pytincture_imports'
            return node

        def visit_ImportFrom(self, node):
            if engine == 'micropython' and node.module == 'importlib' and not node.level:
                guarded = [a for a in node.names if a.name == 'import_module']
                others = [a for a in node.names if a.name != 'import_module']
                if guarded:
                    return ([ast.ImportFrom(module='importlib', names=others, level=0)] if others else []) + [ast.ImportFrom(module='_pytincture_imports', names=guarded, level=0)]
            return node
    transformer = Guard()
    tree = transformer.visit(tree)
    if transformer.used:
        # Keep docstrings and __future__ imports in their required positions.
        index = 0
        while index < len(tree.body) and (isinstance(tree.body[index], ast.Expr) and isinstance(tree.body[index].value, ast.Constant)
                or isinstance(tree.body[index], ast.ImportFrom) and tree.body[index].module == '__future__'):
            index += 1
        tree.body.insert(index, ast.ImportFrom(module='_pytincture_imports', names=[ast.alias(name='import_module', asname=helper)], level=0))
    return ast.unparse(ast.fix_missing_locations(tree))+'\n'


def inspect_source(name, source, engine, *, explicit_dynamic=()):
    tree = ast.parse(import_source(source))
    findings = []
    def add(node, rule, message, severity='error'):
        findings.append({'file': name, 'line': node.lineno, 'rule': rule,
                         'severity': severity, 'message': message})
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname or a.name: a.name for a in node.names})
        elif isinstance(node, ast.ImportFrom):
            aliases.update({a.asname or a.name: (node.module or '')+'.'+a.name for a in node.names})
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call = ast.unparse(node.func)
            first, _, tail = call.partition('.')
            call = aliases.get(first, first) + ('.'+tail if tail else '')
            if call in {'__import__', 'importlib.import_module'}:
                literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                if call == 'importlib.import_module' and not isinstance(literal, str) and explicit_dynamic:
                    add(node, 'dynamic-import-guard', 'Computed import is checked against dynamic-imports at runtime', 'supported')
                elif not isinstance(literal, str) or literal not in explicit_dynamic:
                    add(node, 'dynamic-import', 'Declare permitted modules in dynamic-imports; computed imports require an explicit allowlist and importlib.import_module')
            if engine == 'micropython' and call in {'eval', 'exec', 'compile', 'inspect.signature', 'inspect.getsource', 'inspect.getmembers', 'inspect.stack'}:
                add(node, 'dynamic-reflection', f'{call} is outside the portable MicroPython profile')
            if engine == 'micropython' and call in {'os.getenv', 'os.environ.get'}:
                add(node, 'browser-environment', 'Uses the browser-local environment, initially empty; build/server environment values are never copied', 'supported')
            if engine == 'micropython' and call == 'time.monotonic':
                add(node, 'browser-monotonic', 'Uses performance.now() in seconds, independent of wall-clock changes', 'supported')
            if engine == 'micropython' and call in {'time.perf_counter', 'datetime.datetime.utcnow', 'datetime.utcnow', 'asyncio.to_thread'}:
                add(node, 'runtime-api', f'{call} is unavailable in the pinned browser MicroPython; use a portable/browser API')
            if call.startswith('js.') and any(part in call for part in ('.MediaRecorder', '.getUserMedia', '.clipboard', '.gpu', '.indexedDB')):
                add(node, 'browser-api', f'Requires browser API {call}; availability/permissions must be tested on the target browser', 'warning')
        if (engine == 'micropython' and isinstance(node, (ast.List, ast.Tuple, ast.Set))
                and not isinstance(getattr(node, 'ctx', None), ast.Store)
                and any(isinstance(element, ast.Starred) for element in node.elts)):
            add(node, 'iterable-display', 'Collection unpacking is expanded in evaluation order for MicroPython', 'supported')
        if engine == 'micropython' and isinstance(node, (ast.Match, ast.TryStar)):
            add(node, 'runtime-syntax', f'{type(node).__name__} requires CPython')
    return findings


def verified_stdlib(name):
    inventory = json.loads((VENDOR/'stdlib/inventory.json').read_text())
    content = (VENDOR/'stdlib'/f'{name}.py.txt').read_bytes()
    if hashlib.sha256(content).hexdigest() != inventory['modules'][name]:
        raise ValueError(f'MicroPython standard-library integrity mismatch: {name}')
    return content.decode()


class CompatibilityError(ValueError):
    def __init__(self, report):
        self.report = report
        errors = [f"{engine}: {finding.get('file', '')}:{finding.get('line', 0)}: {finding['message']}"
                  for engine, result in report['runtimes'].items()
                  if engine in report['requested_runtimes']
                  for finding in result['findings'] if finding['severity'] == 'error']
        super().__init__('; '.join(errors))
