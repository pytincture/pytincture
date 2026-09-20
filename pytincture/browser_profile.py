"""Versioned, static browser profile. This is validation, not execution or proof."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

from pytincture.browser_sources import main_only

PROFILE = 'pytincture-portable-1'
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
                if not isinstance(literal, str) or literal not in explicit_dynamic:
                    add(node, 'dynamic-import', 'Declare each literal dynamic import in dynamic-imports; computed imports are unsupported')
            if engine == 'micropython' and call in {'eval', 'exec', 'compile', 'inspect.signature', 'inspect.getsource', 'inspect.getmembers', 'inspect.stack'}:
                add(node, 'dynamic-reflection', f'{call} is outside the portable MicroPython profile')
            if engine == 'micropython' and call in {'time.perf_counter', 'time.monotonic', 'datetime.datetime.utcnow', 'datetime.utcnow', 'asyncio.to_thread', 'os.getenv', 'os.environ.get'}:
                add(node, 'runtime-api', f'{call} is unavailable in the pinned browser MicroPython; use a portable/browser API')
            if call.startswith('js.') and any(part in call for part in ('.MediaRecorder', '.getUserMedia', '.clipboard', '.gpu', '.indexedDB')):
                add(node, 'browser-api', f'Requires browser API {call}; availability/permissions must be tested on the target browser', 'warning')
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
