"""Build-time compatibility transforms for experimental MicroPython clients."""
import ast
from pytincture.browser_sources import main_only


class BrowserCompatibility(ast.NodeTransformer):
    def __init__(self, *, widget=False, widgets=(), report=None, filename='<source>'):
        self.widget = widget
        self.widgets = widgets
        self.exception_names = []
        self.typing_names = set()
        self.dataclass_names = {'dataclass', 'dataclasses.dataclass'}
        self.asyncio_names = {'asyncio'}
        self.report = report
        self.filename = filename
        self.additional_helpers = set()
        self.time_names = {'time'}
        self.used_names = set()

    def visit(self, node):
        tracked = self.report is not None and hasattr(self, 'visit_' + type(node).__name__)
        before = ast.dump(node, include_attributes=False) if tracked else None
        line = getattr(node, 'lineno', 0)
        result = super().visit(node)
        if tracked and before != (ast.dump(result, include_attributes=False) if isinstance(result, ast.AST) else str(result)):
            self.report.append({'file': self.filename, 'line': line,
                                'rule': 'adapt-' + type(node).__name__, 'severity': 'transformation',
                                'behavior_changing': type(node).__name__ not in {'If', 'arg', 'List', 'Tuple', 'Set'},
                                'message': 'MicroPython profile transformation; see portable-python-profile.md'})
        return result

    def generic_visit(self, node):
        had_body = bool(getattr(node, 'body', None))
        result = super().generic_visit(node)
        if had_body and hasattr(result, 'body') and not result.body and not isinstance(result, ast.Module):
            result.body = [ast.Pass()]
        return result

    def visit_If(self, node):
        if main_only(node.test):
            return [self.visit(child) for child in node.orelse]
        if ast.unparse(node.test) in {'TYPE_CHECKING', 'typing.TYPE_CHECKING'}:
            return [self.visit(child) for child in node.orelse]
        return self.generic_visit(node)

    def visit_Match(self, node):
        raise ValueError(f'line {node.lineno}: match statements require CPython; use if/elif for MicroPython')

    def visit_TryStar(self, node):
        raise ValueError(f'line {node.lineno}: exception groups require CPython')

    def visit_Dict(self, node):
        self.generic_visit(node)
        if None not in node.keys:
            return node
        parts = []
        for key, value in zip(node.keys, node.values):
            parts.append(value if key is None else ast.Dict(keys=[key],values=[value]))
        return ast.Call(func=ast.Name(id='merge_dicts',ctx=ast.Load()),args=parts,keywords=[])

    def _collection_display(self, node):
        self.generic_visit(node)
        if isinstance(getattr(node, 'ctx', None), ast.Store) or not any(isinstance(item, ast.Starred) for item in node.elts):
            return node
        is_set = isinstance(node, ast.Set)
        self.additional_helpers.add('_expand_set' if is_set else '_expand_list')
        result = ast.Constant(None) if is_set else ast.List(elts=[], ctx=ast.Load())
        for item in node.elts:
            values = item.value if isinstance(item, ast.Starred) else ast.Tuple(elts=[item], ctx=ast.Load())
            # Nest calls so each iterator is consumed before evaluating the next
            # expression, including when iteration raises or has side effects.
            result = ast.Call(func=ast.Name(id='_expand_set' if is_set else '_expand_list', ctx=ast.Load()), args=[result, values], keywords=[])
        if isinstance(node, ast.Tuple):
            self.additional_helpers.add('_display_tuple')
            result = ast.Call(func=ast.Name(id='_display_tuple', ctx=ast.Load()), args=[result], keywords=[])
        return ast.copy_location(result, node)

    visit_List = _collection_display
    visit_Tuple = _collection_display
    visit_Set = _collection_display

    def visit_ImportFrom(self, node):
        if node.module == 'time' and any(alias.name == 'monotonic' for alias in node.names):
            rest = [alias for alias in node.names if alias.name != 'monotonic']
            return ([ast.ImportFrom(module='time', names=rest, level=0)] if rest else []) + [
                ast.ImportFrom(module='_pytincture_compat', names=[ast.alias(name='browser_monotonic', asname=alias.asname or 'monotonic')], level=0)
                for alias in node.names if alias.name == 'monotonic']
        if node.module == 'importlib' and all(alias.name == 'resources' for alias in node.names):
            return ast.Import(names=[ast.alias(name='_pytincture_resources', asname=alias.asname or alias.name) for alias in node.names])
        if node.module in ('typing', '__future__'):
            if node.module == 'typing':
                self.typing_names.update(alias.asname or alias.name for alias in node.names)
            return None
        if node.module == 'asyncio' and any(alias.name in {'ensure_future', 'get_running_loop'} for alias in node.names):
            imports = []
            remaining = []
            for alias in node.names:
                if alias.name in {'ensure_future', 'get_running_loop'}:
                    helper = 'spawn' if alias.name == 'ensure_future' else 'browser_event_loop'
                    imports.append(ast.ImportFrom(module='_pytincture_compat', names=[ast.alias(name=helper, asname=alias.asname or alias.name)], level=0))
                else:
                    remaining.append(alias)
            if remaining:
                imports.insert(0, ast.ImportFrom(module='asyncio', names=remaining, level=0))
            return imports
        if node.module == 'pyodide.code' and not node.level:
            if any(alias.name != 'run_js' for alias in node.names):
                raise ValueError('Portable pyodide.code supports only run_js')
            node.module = '_pytincture_compat'
        if node.module == 'pyodide.ffi':
            if any(alias.name not in {'create_proxy', 'create_once_callable', 'to_js', 'JsProxy'} for alias in node.names):
                raise ValueError('Unsupported pyodide.ffi import; supported: create_proxy, create_once_callable, to_js, JsProxy')
            node.module = '_pytincture_compat'
        if node.module == 'pyodide.ffi.wrappers':
            if any(alias.name not in {'add_event_listener', 'remove_event_listener'} for alias in node.names):
                raise ValueError('Portable ffi wrappers support add_event_listener and remove_event_listener')
            node.module = '_pytincture_events'
        if node.module == 'dataclasses':
            for alias in node.names:
                if alias.name == 'dataclass':
                    self.dataclass_names.add(alias.asname or alias.name)
                if alias.name not in {'dataclass', 'field', 'asdict', 'replace', 'fields', 'is_dataclass', 'MISSING'}:
                    raise ValueError('Unsupported browser dataclasses import: ' + alias.name)
            node.module = '_pytincture_dataclasses'
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in self.time_names and node.attr == 'monotonic' and isinstance(node.ctx, ast.Load):
            self.additional_helpers.add('browser_monotonic')
            return ast.copy_location(ast.Name(id='browser_monotonic', ctx=ast.Load()), node)
        return node

    def visit_Import(self, node):
        node.names = [name for name in node.names if name.name != 'traceback']
        for alias in node.names:
            if alias.name == 'dataclasses':
                self.dataclass_names.add((alias.asname or 'dataclasses') + '.dataclass')
                alias.asname = alias.asname or 'dataclasses'
                alias.name = '_pytincture_dataclasses'
            elif alias.name in {'logging', 'inspect'}:
                alias.asname = alias.asname or alias.name
                alias.name = '_pytincture_' + alias.name
        return node if node.names else None

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        node.returns = None
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_AsyncFunctionDef(self, node):
        class Yields(ast.NodeVisitor):
            found = False
            def visit_Yield(self, child):
                self.found = True
            visit_YieldFrom = visit_Yield
            def visit_FunctionDef(self, child):
                pass
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_Lambda(self, child):
                pass
        scan = Yields()
        for child in node.body:
            scan.visit(child)
        if scan.found:
            raise ValueError('Async generators are not supported by MicroPython; use an object with __aiter__ and async __anext__')
        return self.visit_FunctionDef(node)

    def visit_AnnAssign(self, node):
        return ast.Assign(targets=[node.target], value=self.visit(node.value)) if node.value else None

    def visit_Assign(self, node):
        if isinstance(node.value, ast.Subscript) and isinstance(node.value.value, ast.Name) and node.value.value.id in self.typing_names:
            return None
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'TypeVar':
            return None
        return self.generic_visit(node)

    def visit_ClassDef(self, node):
        if self.widget and node.name == 'LoadUICaller':
            return None
        if any(kw.arg == 'metaclass' for kw in node.keywords):
            if not self.widget or any(kw.arg == 'metaclass' and ast.unparse(kw.value) != 'LoadUICaller' for kw in node.keywords):
                raise ValueError('Custom metaclasses are not supported by MicroPython')
            node.keywords = [kw for kw in node.keywords if kw.arg != 'metaclass']
            node.body.insert(0, ast.parse('_pytincture_layout = True').body[0])
        dataclass = any(ast.unparse(d.func if isinstance(d, ast.Call) else d) in self.dataclass_names for d in node.decorator_list)
        if dataclass:
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) in self.dataclass_names:
                    if decorator.args or any(kw.arg not in {'init', 'repr', 'eq', 'kw_only'} for kw in decorator.keywords):
                        raise ValueError('Browser dataclasses support init, repr, eq and kw_only options')
            fields = []
            for member in node.body:
                if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    annotation = ast.unparse(member.annotation)
                    if 'ClassVar' in annotation:
                        continue
                    if 'InitVar' in annotation:
                        raise ValueError('InitVar is not supported by browser dataclasses')
                    default = member.value or ast.Attribute(value=ast.Name(id='_pytincture_dc', ctx=ast.Load()), attr='MISSING', ctx=ast.Load())
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        raise ValueError('Mutable dataclass defaults require field(default_factory=...)')
                    fields.append(ast.Tuple(elts=[ast.Constant(member.target.id), default], ctx=ast.Load()))
            metadata = ast.Assign(targets=[ast.Name(id='__pytincture_fields__',ctx=ast.Store())], value=ast.List(elts=fields,ctx=ast.Load()))
            own_methods = {m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
            node.body = [m for m in node.body if not isinstance(m, ast.AnnAssign) or 'ClassVar' in ast.unparse(m.annotation)]
            node.body.insert(0, metadata)
            for method in ('init', 'repr', 'eq'):
                node.body.insert(0, ast.parse(f'__pytincture_own_{method}__ = {"__" + method + "__" in own_methods}').body[0])
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        node.decorator_list.insert(0, ast.Name(id='initialize_layout', ctx=ast.Load()))
        return node

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.type is None:
            self.additional_helpers.add('browser_base_exception')
            node.type = ast.Name(id='browser_base_exception', ctx=ast.Load())
        if node.name is None:
            name = '_runtime_error'
            while name in self.used_names:
                name += '_'
            self.used_names.add(name)
            node.name = name
        self.exception_names.append(node.name)
        self.generic_visit(node)
        self.exception_names.pop()
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Name) and func.id == 'hasattr' and len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == 'to_py':
            return ast.Call(func=ast.Name(id='can_to_python',ctx=ast.Load()),args=[node.args[0]],keywords=[])
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in self.asyncio_names and func.attr == 'get_running_loop' and not node.args and not node.keywords:
                return ast.Call(func=ast.Name(id='browser_event_loop', ctx=ast.Load()), args=[], keywords=[])
            if func.attr == 'title' and not node.args and not node.keywords:
                return ast.Call(func=ast.Name(id='string_title',ctx=ast.Load()),args=[func.value],keywords=[])
            if ast.unparse(func) == 'traceback.format_exception' and len(node.args) in {1, 3}:
                error = node.args[0] if len(node.args) == 1 else node.args[1]
                return ast.List(elts=[ast.Call(func=ast.Name(id='format_exception', ctx=ast.Load()), args=[error], keywords=[])], ctx=ast.Load())
            if func.attr == 'to_py' and not node.args and not node.keywords:
                return ast.Call(func=ast.Name(id='to_python',ctx=ast.Load()),args=[func.value],keywords=[])
            if isinstance(func.value,ast.Name) and func.value.id in self.asyncio_names and func.attr == 'ensure_future':
                node.func = ast.Name(id='spawn',ctx=ast.Load())
            if isinstance(func.value,ast.Name) and func.value.id == 'traceback' and func.attr == 'format_exc':
                if not self.exception_names or node.args or node.keywords:
                    raise ValueError('traceback.format_exc is supported without arguments inside except blocks')
                node.func = ast.Name(id='format_exception',ctx=ast.Load())
                node.args = [ast.Name(id=self.exception_names[-1],ctx=ast.Load())]
        return node


def adapt(source, *, widget=False, widgets=(), report=None, filename='<source>'):
    tree = ast.parse(source)
    transformer = BrowserCompatibility(widget=widget, widgets=widgets, report=report, filename=filename)
    transformer.used_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.name for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name}
    transformer.time_names.update(alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names if alias.name == 'time')
    transformer.asyncio_names.update(alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names if alias.name == 'asyncio')
    tree = transformer.visit(tree)
    tree.body.insert(0, ast.ImportFrom(module='_pytincture_compat',names=[
        ast.alias(name=name) for name in ('to_python','spawn','format_exception','merge_dicts','initialize_layout','can_to_python','string_title','browser_event_loop', *sorted(transformer.additional_helpers))
    ],level=0))
    tree.body.insert(0, ast.Import(names=[ast.alias(name='_pytincture_dataclasses',asname='_pytincture_dc')]))
    return ast.unparse(ast.fix_missing_locations(tree))+'\n'
