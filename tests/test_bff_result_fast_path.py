"""Wire compatibility and rejection boundaries for native JSON BFF results."""
import json
import math
import random
from datetime import datetime, timezone
from enum import IntEnum

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_serializer

from pytincture.backend import results


def encode(value, **overrides):
    return results.prepare_bff_result(value, **{
        'max_bytes': 1_000_000, 'max_depth': 32, 'max_items': 10_000, **overrides,
    })


@pytest.mark.parametrize('compact', [True, False])
def test_native_json_wire_matches_fastapi_for_random_nested_data(compact):
    rng = random.Random(385)
    scalars = [None, True, False, 0, -1, 10**90, -0.0, 1e-308, 1e308,
               '', 'café', '😀', '\x00\n\t"\\', '\u2028\u2029']

    def value(depth):
        if not depth or rng.randrange(3) == 0:
            return rng.choice(scalars)
        if rng.randrange(2):
            return [value(depth - 1) for _ in range(rng.randrange(5))]
        return {f'key{i}': value(depth - 1) for i in range(rng.randrange(5))}

    for _ in range(150):
        original = value(4)
        converted, payload = encode(original, compact=compact)
        options = dict(ensure_ascii=False, allow_nan=False)
        if compact:
            options['separators'] = (',', ':')
        expected = json.dumps(jsonable_encoder(original), **options).encode('utf-8')
        assert converted == original
        assert payload == expected
        # A conservative bound must never reject a result that actually fits.
        assert encode(original, compact=compact, max_bytes=len(expected))[1] == expected
        if len(expected) > 1:
            with pytest.raises(results.BFFResultLimitExceeded):
                encode(original, compact=compact, max_bytes=len(expected) - 1)


def test_result_is_a_private_snapshot_with_shared_references_preserved_as_values():
    row = {'name': 'before'}
    original = [row, row]
    converted, payload = encode(original)
    row['name'] = 'after'
    assert converted == [{'name': 'before'}, {'name': 'before'}]
    assert json.loads(payload) == converted


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf, {'value': math.nan}])
def test_nonfinite_numbers_remain_rejected(value):
    with pytest.raises(ValueError):
        encode(value)


def test_cycles_and_structural_limits_remain_rejected():
    cycle = []; cycle.append(cycle)
    with pytest.raises(results.BFFResultLimitExceeded, match='circular'):
        encode(cycle)
    with pytest.raises(results.BFFResultLimitExceeded, match='BFF_RESULT_MAX_DEPTH'):
        encode([[[0]]], max_depth=2)
    with pytest.raises(results.BFFResultLimitExceeded, match='BFF_RESULT_MAX_ITEMS'):
        encode({'rows': [{'a': 1}, {'a': 2}]}, max_items=4)
    with pytest.raises(UnicodeEncodeError):
        encode({'value': '\ud800'})


def test_c_encoder_is_not_used_before_a_safe_allocation_bound(monkeypatch):
    original_encode = json.JSONEncoder.encode
    calls = []

    def observed(self, value):
        calls.append(value)
        return original_encode(self, value)

    monkeypatch.setattr(json.JSONEncoder, 'encode', observed)
    assert encode({'ok': True})[1] == b'{"ok":true}'
    assert calls == [{'ok': True}]
    calls.clear()
    # Fits exactly, but escaped/non-ASCII strings have a conservative bound.
    assert encode({'x': 'é'}, max_bytes=10)[1] == b'{"x":"\xc3\xa9"}'
    assert calls == []
    with pytest.raises(results.BFFResultLimitExceeded):
        encode({'x': '\x00' * 10}, max_bytes=20)
    assert calls == []


def test_complex_results_keep_serializers_aliases_and_key_conversion():
    class Number(IntEnum):
        ONE = 1

    class Row(BaseModel):
        name: str = Field(serialization_alias='displayName')

        @field_serializer('name', when_used='json')
        def uppercase(self, value):
            return value.upper()

    value = {'model': Row(name='alice'), 'date': datetime(2026, 1, 1, tzinfo=timezone.utc),
             'enum': Number.ONE, 'mapping': {None: 'empty', 3: 'three'}, '_sa_hidden': 'private'}
    expected = jsonable_encoder(value)
    converted, payload = encode(value)
    assert converted == expected
    assert json.loads(payload) == json.loads(json.dumps(expected))
    # FastAPI excludes these fields, but the pre-conversion budget still applies.
    with pytest.raises(results.BFFResultLimitExceeded):
        encode({'_sa_hidden': list(range(10))}, max_items=5)


def test_fallback_does_not_consume_a_generator_twice():
    seen = []

    def source():
        for i in range(3):
            seen.append(i)
            yield i

    converted, payload = encode({'items': source()})
    assert seen == [0, 1, 2]
    assert converted == {'items': [0, 1, 2]}
    assert json.loads(payload) == converted


@pytest.mark.parametrize('method', ['oversize', 'deep', 'wide'])
def test_rejected_result_never_sends_success_headers_or_result_content(tmp_path, method):
    from fastapi.testclient import TestClient
    from pytincture import PytinctureConfig, create_app

    (tmp_path / 'demo.py').write_text('from data import Data\n')
    (tmp_path / 'data.py').write_text('''from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Data:
    def oversize(self): return {"secret": "PRIVATE_RESULT" * 100}
    def deep(self): return [[["PRIVATE_RESULT"]]]
    def wide(self): return ["PRIVATE_RESULT"] * 10
''')
    app = create_app(PytinctureConfig(modules_path=str(tmp_path),
        bff_result_max_bytes=128, bff_result_max_depth=2, bff_result_max_items=5))
    statuses = []
    frames = []

    async def observed(scope, receive, send):
        async def capture(message):
            if message['type'] == 'http.response.start':
                statuses.append(message['status'])
            elif message['type'] == 'http.response.body':
                frames.append(message.get('body', b''))
            await send(message)
        await app(scope, receive, capture)

    with TestClient(observed) as client:
        response = client.post(f'/demo/classcall/data.py/Data/{method}', json={})
    assert response.status_code == 413
    assert statuses == [413]
    assert b'PRIVATE_RESULT' not in b''.join(frames)


def test_native_snapshot_bounds_container_growth_during_traversal(monkeypatch):
    original = [1]
    add_items = results._ResultBudget.add_items
    changed = False

    def grow(self, count):
        nonlocal changed
        add_items(self, count)
        if not changed:
            changed = True
            original.extend(range(10))

    monkeypatch.setattr(results._ResultBudget, 'add_items', grow)
    with pytest.raises(results.BFFResultLimitExceeded, match='BFF_RESULT_MAX_ITEMS'):
        encode(original, max_items=5)


def test_native_snapshot_filters_keys_added_during_traversal(monkeypatch):
    original = {'ok': True}
    add_items = results._ResultBudget.add_items

    def grow(self, count):
        add_items(self, count)
        original['_sa_private'] = 'must be filtered'

    monkeypatch.setattr(results._ResultBudget, 'add_items', grow)
    assert encode(original)[0] == {'ok': True}


def test_timed_out_sync_call_does_not_start_result_preparation(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from pytincture import PytinctureConfig, create_app

    (tmp_path / 'demo.py').write_text('from data import Data\n')
    (tmp_path / 'data.py').write_text('''import time
from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Data:
    def slow(self):
        time.sleep(0.3)
        return "PRIVATE_RESULT"
    def ping(self): return "ok"
''')
    app = create_app(PytinctureConfig(modules_path=str(tmp_path), bff_call_timeout_seconds=0.1))
    backend = app.state.pytincture_backend
    prepared = []
    original = backend.prepare_bff_result

    def observe(value, **kwargs):
        prepared.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(backend, 'prepare_bff_result', observe)
    with TestClient(app) as client:
        response = client.post('/demo/classcall/data.py/Data/slow', json={})
        assert response.status_code == 504
        assert 'PRIVATE_RESULT' not in response.text
        time.sleep(0.35)
        assert prepared == []
        assert client.post('/demo/classcall/data.py/Data/ping', json={}).json() == 'ok'
    assert prepared == ['ok']
