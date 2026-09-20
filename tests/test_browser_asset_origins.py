import pytest
from fastapi.testclient import TestClient
from pytincture import PytinctureConfig, create_app


def test_asset_origins_are_separate_and_round_trip(tmp_path):
    config = PytinctureConfig.from_env({
        'MODULES_PATH': str(tmp_path),
        'PYTINCTURE_BROWSER_SCRIPT_ORIGINS': '["https://scripts.example"]',
        'PYTINCTURE_BROWSER_STYLE_ORIGINS': 'https://styles.example',
        'PYTINCTURE_BROWSER_FONT_ORIGINS': 'https://fonts.example',
    })
    assert PytinctureConfig.from_env(config.to_environ()).browser_font_origins == ('https://fonts.example',)
    with TestClient(create_app(config)) as client:
        csp = client.get('/healthz').headers['content-security-policy']
    directives = {part.strip().split(' ', 1)[0]: part for part in csp.split(';') if part.strip()}
    for kind in ('script', 'style', 'font'):
        origin = f'https://{kind}s.example'
        assert origin in directives[kind+'-src']
        assert all(origin not in value for key, value in directives.items() if key != kind+'-src')


@pytest.mark.parametrize('kind', ['script', 'style', 'font'])
@pytest.mark.parametrize('origin', ['*', 'https://*.example', 'http://example', 'wss://example',
                                   'https://example/path', 'https://example?q=x', 'https://u:p@example'])
def test_asset_origins_reject_broad_or_non_https_sources(kind, origin):
    with pytest.raises(ValueError):
        PytinctureConfig(**{f'browser_{kind}_origins': (origin,)})
