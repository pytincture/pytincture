"""The same real imports and fallbacks must work in both portable runtimes."""
try:
    import app_settings
except ImportError:
    SETTINGS = 'browser settings'
else:
    raise AssertionError('Server settings reached the browser')

try:
    from server_package import config
except ImportError:
    CONFIG = 'browser config'

# Pyodide ships decimal. A declared server-only import must still be absent.
try:
    from decimal import Decimal
except ImportError:
    Decimal = None


def validate():
    assert SETTINGS == 'browser settings'
    assert CONFIG == 'browser config'
    assert Decimal is None
    try:
        import json, yaml
    except ImportError:
        assert json.loads('42') == 42
    else:
        raise AssertionError('Server yaml reached the browser')
    try:
        from fastapi.responses import Response
    except ImportError as error:
        assert 'server-only' in str(error)
    else:
        raise AssertionError('Server framework reached the browser')
