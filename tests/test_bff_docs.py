import json

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from pytincture import PytinctureConfig, create_app
from pytincture.dataclass import get_bff_manifest
from pytincture.bff_docs import operation_spec


SOURCE = '''
from typing import TypedDict, NotRequired, Optional, Literal
from pytincture.dataclass import backend_for_frontend

raise RuntimeError("documentation must never execute this module")

class Item(TypedDict):
    name: str
    score: float
    note: NotRequired[str]

class Page(TypedDict):
    items: list[Item]
    cursor: Optional[int]
    status: Literal["ok", "empty"]

@backend_for_frontend
class Catalog:
    def search(self, page: int, /, query: str, limit: int = 20, *, active: bool = True) -> Page:
        """Search the catalog and return a page of items."""
        raise RuntimeError("documentation must never invoke a method")

    def mixed(self, value, token="secret-default"):
        return value
'''


def operation():
    manifest = get_bff_manifest('catalog.py', source=SOURCE)
    return operation_spec('catalog.py', 'Catalog', 'search', manifest['Catalog', 'search'])


def test_docs_describe_real_signature_and_response_without_executing_code(tmp_path):
    (tmp_path / 'demo.py').write_text('from catalog import Catalog\n')
    (tmp_path / 'catalog.py').write_text(SOURCE)
    with TestClient(create_app(PytinctureConfig(modules_path=str(tmp_path)))) as client:
        response = client.get('/demo/catalog/bff-docs/openapi.json')
        assert response.status_code == 200
        schema = response.json()
    call = schema['paths']['/Catalog/search']['post']
    assert call['summary'] == 'Search the catalog and return a page of items.'
    assert '| `query` | `str` | Yes |' in call['description']
    body = call['requestBody']['content']['application/json']
    assert body['examples']['named']['value'] == {'page': 1, 'query': 'string', 'limit': 1, 'active': True}
    returned = call['responses']['200']['content']['application/json']
    assert returned['schema']['properties']['items']['items']['properties']['score']['type'] == 'number'
    assert returned['schema']['properties']['items']['items']['required'] == ['name', 'score']
    assert returned['schema']['properties']['cursor']['anyOf'] == [{'type': 'integer'}, {'type': 'null'}]
    Draft202012Validator(returned['schema']).validate(returned['examples']['shape']['value'])
    assert 'secret-default' not in json.dumps(schema)
    unknown = schema['paths']['/Catalog/mixed']['post']
    assert unknown['requestBody']['content']['application/json']['schema']['properties']['value'].get('type') is None
    assert unknown['responses']['200']['content']['application/json']['schema'] == {}


def test_documented_request_schema_accepts_real_argument_forms_and_rejects_missing_values():
    call = operation()
    body = call['requestBody']['content']['application/json']
    validator = Draft202012Validator(body['schema'])
    Draft202012Validator.check_schema(body['schema'])
    for payload in (
        body['examples']['named']['value'],
        {'page': 1, 'query': 'books'},
        {'page': 1, 'query': 'books', 'limit': 20, 'active': False},
    ):
        validator.validate(payload)
    for payload in (
        {'query': 'books'},
        {'page': 1},
        {'page': 1, 'query': 99},
        {'page': 1, 'query': 'books', 'unexpected': True},
        {'args': [1, 'books'], 'kwargs': {}},
    ):
        assert not validator.is_valid(payload)


def test_annotation_calls_and_recursive_types_are_not_evaluated():
    manifest = get_bff_manifest('danger.py', source='''
from typing import TypedDict
from pytincture.dataclass import backend_for_frontend
class Tree(TypedDict):
    children: list["Tree"]
@backend_for_frontend
class Danger:
    def run(self, value: crash()) -> Tree:
        raise RuntimeError()
''')
    result = manifest['Danger', 'run']
    assert result['parameters'][0]['schema'] == {}
    assert len(json.dumps(result['return_schema'])) < 4000


def test_named_json_calls_and_generated_client_packets_share_validation(tmp_path):
    (tmp_path / 'demo.py').write_text('from catalog import Catalog\n')
    (tmp_path / 'catalog.py').write_text('''
from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Catalog:
    def page(self, page: int, page_size: int = 2) -> dict:
        return {"page": page, "page_size": page_size}
    def positional(self, first: str, /, *values: int, enabled: bool = True):
        return {"first": first, "values": values, "enabled": enabled}
''')
    with TestClient(create_app(PytinctureConfig(modules_path=str(tmp_path)))) as client:
        path = '/demo/classcall/catalog.py/Catalog/page'
        for payload in ({'page': 1, 'page_size': 2}, {'args': [1], 'kwargs': {'page_size': 2}}, {'args': [], 'kwargs': {'page': 1, 'page_size': 2}}):
            response = client.post(path, json=payload)
            assert response.status_code == 200, response.text
            assert response.json() == {'page': 1, 'page_size': 2}
        for payload in ({'page': 'bad'}, {'page': 1, 'unexpected': True}, {}, {'args': [], 'kwargs': {}}):
            assert client.post(path, json=payload).status_code == 400
        assert client.post(path, content='{"page":1,"page":2}', headers={'Content-Type': 'application/json'}).status_code == 400
        response = client.post('/demo/classcall/catalog.py/Catalog/positional', json={'first': 'start', 'values': [1, 2], 'enabled': False})
        assert response.status_code == 200, response.text
        assert response.json() == {'first': 'start', 'values': [1, 2], 'enabled': False}


def test_nested_module_docs_use_extensionless_base_and_do_not_mix_modules(tmp_path):
    (tmp_path / 'demo.py').write_text('from services.catalog import Catalog\nfrom internal.catalog import Catalog as Other\n')
    (tmp_path / 'elsewhere.py').write_text('APP_TITLE = "Other app"\n')
    for folder, method in [('services', 'search'), ('internal', 'private')]:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'catalog.py').write_text(f'''from pytincture.dataclass import backend_for_frontend
@backend_for_frontend
class Catalog:
    def {method}(self, value: str): return {{"value": value, "module": "{folder}"}}
''')
    app = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    with TestClient(app, root_path='/mounted') as client:
        schema = client.get('/demo/services/catalog/bff-docs/openapi.json').json()
        assert schema['servers'] == [{'url': '/mounted/demo/classcall/services/catalog'}]
        assert set(schema['paths']) == {'/Catalog/search'}
        assert schema['tags'] == [{'name': 'Catalog'}]
        assert '.py' not in json.dumps(schema)
        operation = schema['paths']['/Catalog/search']['post']
        assert operation['parameters'] == []
        # Execute uses server + relative operation; the old URL remains compatible.
        for module in ('services/catalog', 'services/catalog.py'):
            for body in ({'value': 'hello'}, {'args': [], 'kwargs': {'value': 'hello'}}):
                response = client.post(f'/demo/classcall/{module}/Catalog/search', json=body)
                assert response.status_code == 200, response.text
                assert response.json() == {'value': 'hello', 'module': 'services'}
        for module in ('catalog', 'services', 'services/catalog.py', 'services//catalog', 'services/%2E%2E/catalog'):
            assert client.get(f'/demo/{module}/bff-docs').status_code == 404
        assert client.get('/elsewhere/services/catalog/bff-docs').status_code == 404
        assert client.get('/demo/internal/catalog/bff-docs/openapi.json').json()['paths'].keys() == {'/Catalog/private'}
        for path in ('/demo/bff-docs', '/demo/bff-docs/openapi.json', '/bff-docs', '/bff-docs/openapi.json', '/docs', '/redoc', '/openapi.json'):
            assert client.get(path, follow_redirects=False).status_code == 404
        html = client.get('/demo/services/catalog/bff-docs')
        assert '/mounted/demo/services/catalog/bff-docs/openapi.json?' in html.text
        assert 'data-auth-base="/mounted/demo/auth"' in html.text
        assert 'no-store' in html.headers['cache-control']
