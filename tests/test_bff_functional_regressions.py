"""Behavioral regressions for BFF transport, serialization and source loading."""
import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_serializer

from pytincture.backend.limits import AsyncAdmissionGate
from pytincture.backend.results import BFFResultLimitExceeded, encode_bff_result
from pytincture.backend.source_loading import load_source_module
from pytincture.backend.streaming import as_streaming_response, serialize_stream_item
from pytincture.browser_build import _bff_stub
from pytincture.dataclass import generate_stub_classes, get_bff_manifest, verify_bff_runtime_export


@pytest.mark.parametrize('failure', [OSError, asyncio.CancelledError])
@pytest.mark.parametrize('close_fails', [False, True])
def test_header_disconnect_releases_stream_admission(failure, close_fails):
    async def exercise():
        gate = AsyncAdmissionGate(1, 0, 0.01)
        await gate.acquire()
        finished, closed = [], []

        class Source:
            def __iter__(self): return self
            def __next__(self): pytest.fail('source must not start before headers')
            def close(self):
                closed.append(True)
                if close_fails:
                    raise RuntimeError('cleanup failed')

        def finish(reason, size):
            finished.append((reason, size))
            gate.release()

        response = as_streaming_response(
            Source(), raw=False, media_type='text/event-stream', max_seconds=1,
            max_bytes=100, max_items=10, idle_timeout_seconds=1, on_finish=finish,
        )
        async def send(message):
            assert message['type'] == 'http.response.start'
            raise failure()
        with pytest.raises(RuntimeError if close_fails else failure):
            await response.stream_response(send)
        assert closed == [True]
        assert finished == [('disconnect', 0)]
        await gate.acquire()
        assert gate._semaphore.locked()  # No double release.
        gate.release()
    asyncio.run(exercise())


def test_pydantic_results_preserve_json_serializers_aliases_and_limits():
    class Item(BaseModel):
        name: str = Field(serialization_alias='displayName')
        hidden: str = Field(default='private', exclude=True)

        @field_serializer('name', when_used='json')
        def serialize_name(self, value):
            return value.upper()

    class Envelope(BaseModel):
        item: Item

    value = Envelope(item=Item(name='example'))
    expected = {'item': {'displayName': 'EXAMPLE'}}
    assert jsonable_encoder(value) == expected
    assert json.loads(encode_bff_result(value, max_bytes=100)) == expected
    for limits in ({'max_bytes': 10}, {'max_bytes': 100, 'max_items': 1},
                   {'max_bytes': 100, 'max_depth': 1}):
        with pytest.raises(BFFResultLimitExceeded):
            encode_bff_result(value, **limits)


def test_pydantic_root_models_keep_the_serialized_depth_budget():
    from pydantic import RootModel

    assert encode_bff_result(RootModel[list[int]]([1]), max_bytes=100, max_depth=1) == b'[1]'


@pytest.mark.parametrize('value', ['hello', '123', 'true', 'null', '', 'a\nb', '"quoted"', 'café'])
def test_default_stream_strings_round_trip_without_type_changes(value):
    frame = serialize_stream_item(value)
    assert frame.endswith('\n')
    assert len(frame.splitlines()) == 1
    assert json.loads(frame) == value
    assert serialize_stream_item(value, raw=True) == value


def test_preencoded_bytes_and_raw_streams_keep_their_wire_format():
    assert serialize_stream_item(b'{"ok":true}') == b'{"ok":true}\n'
    assert serialize_stream_item(b'{"ok":true}\n') == b'{"ok":true}\n'
    assert serialize_stream_item(b'raw', raw=True) == b'raw'
    assert serialize_stream_item('a\nb', raw=True) == 'a\nb'


@pytest.mark.parametrize('package_init', [False, True])
def test_nested_bff_relative_imports_are_isolated_by_modules_root(tmp_path, package_init):
    for root_name, value in [('first', 11), ('second', 22)]:
        root = (tmp_path / root_name).resolve()
        package = root / 'pkg' / 'nested'
        package.mkdir(parents=True)
        (root / 'pkg' / '__init__.py').write_text('LABEL = "parent"\n')
        (root / 'pkg' / 'shared.py').write_text(f'VALUE = {value}\n')
        (package / 'helper.py').write_text('from ..shared import VALUE\n')
        target = package / ('__init__.py' if package_init else 'data.py')
        target.write_text(
            'from .helper import VALUE\nfrom .. import LABEL\n'
            'from pytincture.dataclass import backend_for_frontend\n'
            '@backend_for_frontend\nclass Data:\n'
            '    def read(self): return [LABEL, VALUE]\n'
        )
        operation = get_bff_manifest(str(target))[('Data', 'read')]
        loaded = load_source_module(str(target), 'Data', str(root))
        verify_bff_runtime_export(loaded.Data, class_name='Data', member_name='read',
                                  operation=operation, source_path=str(target))
        assert loaded.Data().read() == ['parent', value]


@pytest.fixture
def browser_modules(monkeypatch):
    js = ModuleType('js')
    monkeypatch.setitem(sys.modules, 'js', js)
    stream = ModuleType('_pytincture_bff')
    exec(Path('pytincture/browser_templates/bff.py.txt').read_text(), stream.__dict__)
    monkeypatch.setitem(sys.modules, '_pytincture_bff', stream)
    return js


@pytest.mark.parametrize('verb', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
def test_portable_stubs_send_the_declared_verb(browser_modules, verb):
    calls = []
    def sync(module, cls, method, payload, http_method='POST'):
        calls.append((method, json.loads(payload), http_method))
        return '42'
    async def request(*args): return sync(*args)
    browser_modules.pytinctureBrowserBff = request
    browser_modules.pytinctureBrowserBffSync = sync
    signature = '' if verb == 'GET' else ', value'
    source = (
        'from pytincture.dataclass import backend_for_frontend, bff_http_methods, bff_stream\n'
        '@backend_for_frontend\nclass Data:\n'
        f'    @bff_http_methods({verb!r})\n    def read(self{signature}): pass\n'
        f'    @bff_stream\n    @bff_http_methods({verb!r})\n    def events(self{signature}): pass\n'
    )
    namespace = {}
    exec(_bff_stub('api/data.py', source), namespace)
    instance = namespace['Data']()
    args = () if verb == 'GET' else (7,)
    assert instance.read(*args) == 42
    assert asyncio.run(instance.read_async(*args)) == 42
    assert calls == [('read', {} if verb == 'GET' else {'value': 7}, verb)] * 2
    assert instance.events(*args).arguments[-1] == verb


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('base_async', [False, True])
def test_portable_companions_never_replace_declared_methods(browser_modules, reverse, base_async):
    def request(module, cls, method, payload, *rest): return json.dumps(method)
    async def async_request(*args): return request(*args)
    browser_modules.pytinctureBrowserBff = async_request
    browser_modules.pytinctureBrowserBffSync = request
    methods = [f'    {"async " if base_async else ""}def lookup(self): pass\n',
               '    async def lookup_async(self): pass\n']
    if reverse: methods.reverse()
    source = 'from pytincture.dataclass import backend_for_frontend\n@backend_for_frontend\nclass Data:\n' + ''.join(methods)
    namespace = {}
    exec(_bff_stub('data.py', source), namespace)
    instance = namespace['Data']()
    assert asyncio.run(instance.lookup_async()) == 'lookup_async'
    assert asyncio.run(instance.lookup_async_async()) == 'lookup_async'
    assert (asyncio.run(instance.lookup()) if base_async else instance.lookup()) == 'lookup'


@pytest.mark.parametrize('retry', [False, True])
def test_legacy_transport_names_can_be_exported_without_recursion(browser_modules, monkeypatch, retry):
    calls = []
    browser_modules.document = SimpleNamespace(cookie='')
    browser_modules.window = SimpleNamespace(location=SimpleNamespace(href='https://app.test/demo'))
    browser_modules.AbortController = SimpleNamespace(new=lambda: SimpleNamespace(signal=None, abort=lambda: None))
    browser_modules.TextDecoder = SimpleNamespace(new=lambda: SimpleNamespace(decode=lambda value='', *args: value))
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', SimpleNamespace(to_js=lambda value: value))

    class XHR:
        status = 200
        def open(self, method, url, asynchronous): self.url = url
        def setRequestHeader(self, *args): pass
        def getResponseHeader(self, *args): return None
        def send(self, *args):
            calls.append(self.url.rsplit('/', 1)[-1])
            self.response = json.dumps(calls[-1])
    browser_modules.XMLHttpRequest = SimpleNamespace(new=XHR)
    rejected = set()
    async def fetch(url, options):
        method = url.rsplit('/', 1)[-1]
        calls.append(method)
        status = 409 if retry and method not in rejected else 200
        rejected.add(method)
        async def text(): return json.dumps(method)
        chunks = iter([SimpleNamespace(done=False, value='"chunk"\n'), SimpleNamespace(done=True)])
        async def read(): return next(chunks)
        async def cancel(): pass
        reader = SimpleNamespace(read=read, cancel=cancel)
        return SimpleNamespace(status=status, headers={'X-Pytincture-Replay': 'rejected'},
                               text=text, body=SimpleNamespace(getReader=lambda: reader))
    browser_modules.fetch = fetch
    source = '''from pytincture.dataclass import backend_for_frontend, bff_stream
@backend_for_frontend
class Data:
    async def fetch(self): pass
    def fetch_sync(self): pass
    @bff_stream
    def fetch_stream(self): pass
    async def ping(self): pass
'''
    namespace = {}
    exec(generate_stub_classes('data.py', '', '', application='demo', source_code=source), namespace)
    instance = namespace['Data']()
    assert instance.fetch_sync() == 'fetch_sync'
    async def exercise():
        assert await instance.fetch() == 'fetch'
        assert await instance.ping() == 'ping'
        assert await instance.fetch_sync_async() == 'fetch_sync'
        assert [value async for value in instance.fetch_stream()] == ['chunk']
    asyncio.run(exercise())
    assert set(calls) == {'fetch', 'fetch_sync', 'ping', 'fetch_stream'}


def test_legacy_annotated_attributes_keep_get_access(browser_modules):
    browser_modules.XMLHttpRequest = None
    browser_modules.document = SimpleNamespace(cookie='')
    source = '''from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Data:
    count: int = 3
    label = 'plain'
    fetch_sync: str = 'collision'
    _pytincture_fetch_sync = 'private server data'
'''
    namespace = {}
    exec(generate_stub_classes('data.py', '', '', application='demo', source_code=source), namespace)
    instance = namespace['Data']()
    calls = []
    def transport(url, payload=None, method='GET'):
        calls.append((url.rsplit('/', 1)[-1], method))
        return '3'
    instance._pytincture_fetch_sync = transport
    assert instance.count == instance.label == instance.fetch_sync == 3
    assert calls == [('count', 'GET'), ('label', 'GET'), ('fetch_sync', 'GET')]


def test_pydantic_lazy_fields_remain_bounded_and_keep_nested_serializers():
    from collections.abc import Iterable

    class Item(BaseModel):
        name: str = Field(serialization_alias='displayName')
        @field_serializer('name', when_used='json')
        def serialize_name(self, value): return value.upper()

    class Envelope(BaseModel):
        items: Iterable[Item]

    value = Envelope(items=(Item(name=name) for name in ['first', 'second']))
    assert json.loads(encode_bff_result(value, max_bytes=200)) == {
        'items': [{'displayName': 'FIRST'}, {'displayName': 'SECOND'}],
    }
    pulled, closed = [], []
    def unbounded():
        try:
            while True:
                pulled.append(True)
                yield Item(name='value')
        finally:
            closed.append(True)
    # model_construct preserves the generator, including its close() protocol.
    value = Envelope.model_construct(items=unbounded())
    with pytest.raises(BFFResultLimitExceeded, match='item limit'):
        encode_bff_result(value, max_bytes=1000, max_items=3)
    assert len(pulled) == 4
    assert closed == [True]
