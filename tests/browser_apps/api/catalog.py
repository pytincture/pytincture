from pytincture.dataclass import backend_for_frontend, bff_http_methods, bff_stream

PRIVATE_MARKER = 'server-implementation-must-not-be-shipped'
DEFAULT_LIMIT = 3


@backend_for_frontend
class Catalog:
    def lookup(self, sku, *, limit=DEFAULT_LIMIT):
        return {'sku': sku, 'limit': limit}

    def combine(self, first, /, *values, **options):
        return {'total': first + sum(values), 'options': options}

    @bff_http_methods('GET')
    def ping(self):
        return {'ok': True}

    @bff_stream
    def events(self):
        yield {'text': 'café'}
        yield {'text': 'streamed'}

    @bff_stream(raw=True)
    def raw_events(self):
        yield 'raw café'
