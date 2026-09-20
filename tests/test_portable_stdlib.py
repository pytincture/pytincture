"""Behavior checks against CPython for the documented portable module subset."""
import base64
import csv
import html
from html.parser import HTMLParser
import io
from pathlib import Path
import sys
import types
import uuid

import pytest

from pytincture.browser_profile import verified_stdlib
from pytincture.browser_build import build_browser_bundle

TEMPLATES = Path('pytincture/browser_templates')


def test_optional_dynamic_module_and_transitive_shims_are_bundled(tmp_path):
    import json
    (tmp_path/'app.py').write_text('import importlib\ndef main():\n name="uuid"\n return importlib.import_module(name)\n')
    config=tmp_path/'pyproject.toml'
    config.write_text('[tool.pytincture.browser]\nentrypoint="app:main"\ndynamic-imports=["uuid", "html.parser"]\n')
    manifest=build_browser_bundle(config)
    sources=json.loads((manifest.parent/'sources.json').read_text())['files']
    assert {'uuid.py','html/__init__.py','html/parser.py','html/entities.py'} <= sources.keys()
    assert json.loads(manifest.read_text())['profile']=='pytincture-portable-2'


def template(name):
    module = types.ModuleType('portable_' + name)
    exec(compile((TEMPLATES / (name + '.py.txt')).read_text(), name, 'exec'), module.__dict__)
    return module


def collect(base, pieces, convert=True):
    class Collector(base):
        def __init__(self):
            super().__init__(convert_charrefs=convert)
            self.events = []

        def handle_starttag(self, tag, attrs): self.events.append(('start', tag, attrs))
        def handle_endtag(self, tag): self.events.append(('end', tag))
        def handle_comment(self, text): self.events.append(('comment', text))
        def handle_decl(self, text): self.events.append(('decl', text))
        def handle_pi(self, text): self.events.append(('pi', text))
        def handle_entityref(self, text): self.events.append(('entity', text))
        def handle_charref(self, text): self.events.append(('char', text))
        def handle_data(self, text):
            if self.events and self.events[-1][0] == 'data':
                self.events[-1] = ('data', self.events[-1][1] + text)
            else:
                self.events.append(('data', text))
    parser = Collector()
    for piece in pieces:
        parser.feed(piece)
    parser.close()
    return parser.events, parser.getpos()


@pytest.mark.parametrize('document', [
    '<p title="x > y &amp; z" hidden>a &amp; b &#x41;</p><br/>',
    '<!DOCTYPE html><?test?><div><!--hello-->text</div>',
    '<script>if (a < b && c > d) x="&amp;";</script><style>a>b{}</style>',
    'a &nbsp; &#123; &#x41; &unknown; &amp no-semicolon',
    '<div CLASS=foo title=\'quoted\' data-x="">line\n2</div>',
    '<input value=hello/><img src="x"/><br />',
])
@pytest.mark.parametrize('chunked', [False, True])
@pytest.mark.parametrize('convert', [False, True])
def test_html_parser_matches_reference_callbacks(monkeypatch, document, chunked, convert):
    portable_html = template('html')
    monkeypatch.setitem(sys.modules, 'html', portable_html)
    parser = template('html_parser').HTMLParser
    pieces = list(document) if chunked else [document]
    assert collect(parser, pieces, convert) == collect(HTMLParser, pieces, convert)


def test_html_unescape_handles_all_named_and_invalid_numeric_references():
    portable = template('html')
    values = ['&' + name for name in html.entities.html5]
    values += ['&#0;', '&#13;', '&#x80;', '&#xD800;', '&#x110000;', '&#xFFFF;', '&#9;',
               '&unknown;', '&notit;', '&#x;', '&', '&' + 'a'*70]
    for value in values:
        assert portable.unescape(value) == html.unescape(value), value


def test_uuid_values_and_generation_match_reference(monkeypatch):
    monkeypatch.setitem(sys.modules, 'js', types.SimpleNamespace(crypto=types.SimpleNamespace(randomUUID=lambda: str(uuid.uuid4()))))
    portable = template('uuid')
    original = uuid.UUID('12345678-1234-5678-9234-567812345678')
    for kwargs in ({'hex':str(original)}, {'hex':original.urn}, {'bytes':original.bytes},
                   {'bytes_le':original.bytes_le}, {'fields':original.fields}, {'int':original.int}):
        value = portable.UUID(**kwargs)
        for name in ('hex','int','bytes','bytes_le','fields','urn','variant','version'):
            assert getattr(value,name)==getattr(original,name)
        assert str(value)==str(original)
    assert portable.uuid4().version == 4
    assert str(portable.UUID(str(original),version=4))==str(uuid.UUID(str(original),version=4))
    for bad in ('bad', 'z'*32):
        with pytest.raises(ValueError): portable.UUID(bad)


@pytest.mark.parametrize('length', [0,1,2,3,4,5,7,11,60])
def test_base_encodings_match_reference(length):
    namespace={'const':lambda value:value}
    exec(verified_stdlib('base64'),namespace)
    exec((TEMPLATES/'base64_extensions.py.txt').read_text(),namespace)
    value=bytes(range(length))
    for kind in ('b64','urlsafe_b64','b32','b16'):
        encoded=namespace[kind+'encode'](value)
        assert encoded==getattr(base64,kind+'encode')(value)
        assert namespace[kind+'decode'](encoded)==value


@pytest.mark.parametrize('options', [{}, {'delimiter':'\t'}, {'quoting':csv.QUOTE_ALL},
                                    {'quoting':csv.QUOTE_NONE,'escapechar':'\\'}, {'doublequote':False,'escapechar':'\\'}])
def test_csv_quoting_and_multiline_fields_match_reference(options):
    portable=template('csv')
    rows=[['a,b','say "yes"','multi\nline',''],['back\\slash','plain','last','42']]
    expected=io.StringIO(); actual=io.StringIO()
    csv.writer(expected,**options).writerows(rows)
    portable.writer(actual,**options).writerows(rows)
    assert actual.getvalue()==expected.getvalue()
    assert list(portable.reader(io.StringIO(actual.getvalue()),**options))==rows


def test_contextmanager_exception_cleanup_and_suppression():
    portable=template('contextlib')
    events=[]
    @portable.contextmanager
    def managed(suppress):
        events.append('enter')
        try:
            yield 42
        except ValueError:
            if not suppress:
                raise
        finally:
            events.append('exit')
    with managed(True) as value:
        assert value==42
        raise ValueError('handled')
    with pytest.raises(ValueError):
        with managed(False):
            raise ValueError('propagate')
    assert events==['enter','exit','enter','exit']
