"""Independent real-browser reproduction of the reported application imports."""
import base64
import contextlib
import csv
from html import escape, unescape
from html.parser import HTMLParser
import io
import string
import uuid
from uuid import UUID, uuid4
import zipfile
from pathlib import Path
import js
from pyodide.ffi import create_proxy
from pyodide.ffi.wrappers import add_event_listener, remove_event_listener


class Collector(HTMLParser):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.events = []

    def handle_starttag(self, tag, attrs):
        self.events.append(('start', tag, attrs))

    def handle_endtag(self, tag):
        self.events.append(('end', tag))

    def handle_data(self, text):
        self.events.append(('data', text))

    def handle_comment(self, text):
        self.events.append(('comment', text))


def validate():
    assert string.ascii_letters == string.ascii_lowercase + string.ascii_uppercase
    assert string.digits == '0123456789' and '!' in string.punctuation
    value = uuid.uuid4()
    assert isinstance(value, UUID) and value.version == 4
    assert UUID(str(value)) == UUID(hex=value.hex) == UUID(bytes=value.bytes)
    assert uuid4() != value
    assert value.variant == uuid.RFC_4122 and len(value.hex) == 32
    payload = b'\x00\xffportable\xfe'
    assert base64.b64decode(base64.b64encode(payload)) == payload
    assert base64.urlsafe_b64decode(base64.urlsafe_b64encode(payload)) == payload
    assert base64.b32decode(base64.b32encode(payload)) == payload
    assert base64.b16decode(base64.b16encode(payload)) == payload
    assert base64.b64decode('cHl0aW5jdHVyZQ==', validate=True) == b'pytincture'
    denied = False
    try:
        base64.b64decode(b'@@', validate=True)
    except Exception:
        denied = True
    assert denied
    parser = Collector()
    parser.feed('<p title="a > b &amp; c">Hi &am')
    parser.feed('p; bye &#x41;</p><!--ok--><br/>')
    parser.close()
    assert parser.events[0] == ('start', 'p', [('title', 'a > b & c')])
    assert ''.join(event[1] for event in parser.events if event[0] == 'data') == 'Hi & bye A'
    assert ('comment', 'ok') in parser.events and ('end', 'br') in parser.events
    assert unescape('&nbsp;&NotEqualTilde;&#x80;&#0;') == '\xa0\u2242\u0338\u20ac\ufffd'
    assert escape('<&"') == '&lt;&amp;&quot;'
    events = []
    @contextlib.contextmanager
    def managed():
        events.append('enter')
        try:
            yield 42
        finally:
            events.append('exit')
    with managed() as answer:
        assert answer == 42
    assert events == ['enter', 'exit']
    with contextlib.suppress(ValueError):
        raise ValueError('suppressed')
    with contextlib.nullcontext('ok') as answer:
        assert answer == 'ok'
    data = [['a,b', 'multi\nline', 'say "hello"', ''], ['plain', '42', '', 'end']]
    output = io.StringIO()
    csv.writer(output).writerows(data)
    assert list(csv.reader(io.StringIO(output.getvalue()))) == data
    with zipfile.ZipFile(io.BytesIO(Path('/sample/archive.zip').read_bytes())) as archive:
        assert archive.namelist() == ['data.txt']
        assert archive.read('data.txt') == b'portable archive'
    node = js.document.createElement('button')
    node.id = 'portable-listener'
    node.textContent = 'Event listener'
    node.setAttribute('data-count', '0')
    def listener(event):
        node.setAttribute('data-count', str(int(node.getAttribute('data-count')) + 1))
    add_event_listener(node, 'click', listener)
    def remove():
        remove_event_listener(js.document.getElementById('portable-listener'), 'click', listener)
    js.window.removePortableListener = create_proxy(remove)
    js.document.body.appendChild(node)
