import pytest
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from pytincture import PytinctureConfig, create_app


def _policy(client, path="/healthz"):
    return client.get(path).headers["content-security-policy"]


@pytest.mark.parametrize(
    ("origins", "allow_blob", "sources"),
    [
        ((), False, None),
        ((), True, "'self' blob:"),
        (("https://frames.example",), False, "'self' https://frames.example"),
        (("https://frames.example",), True, "'self' blob: https://frames.example"),
    ],
)
def test_frame_settings_only_change_frame_src(tmp_path, origins, allow_blob, sources):
    default_app = create_app(PytinctureConfig(modules_path=str(tmp_path)))
    configured_app = create_app(PytinctureConfig(
        modules_path=str(tmp_path),
        browser_frame_origins=origins,
        browser_allow_blob_frames=allow_blob,
    ))
    with TestClient(default_app) as default, TestClient(configured_app) as configured:
        baseline = _policy(default)
        assert "frame-src" not in baseline
        assert "object-src 'none';" in baseline
        assert "frame-ancestors 'none';" in baseline
        policy = _policy(configured)
        if sources is None:
            assert policy == baseline
        else:
            directive = f"frame-src {sources}; "
            assert directive in policy
            assert policy.replace(directive, "") == baseline
        # Requests to another factory instance must not inherit the opt-in.
        assert _policy(default) == baseline


@pytest.mark.parametrize("origins", [
    '["https://one.example", "https://two.example"]',
    "https://one.example,https://two.example",
])
@pytest.mark.parametrize("allow_blob", ["true", "false"])
def test_frame_settings_round_trip_environment(origins, allow_blob):
    config = PytinctureConfig.from_env({
        "PYTINCTURE_BROWSER_FRAME_ORIGINS": origins,
        "PYTINCTURE_BROWSER_ALLOW_BLOB_FRAMES": allow_blob,
    })
    assert config.browser_frame_origins == ("https://one.example", "https://two.example")
    assert config.browser_allow_blob_frames is (allow_blob == "true")
    restored = PytinctureConfig.from_env(config.to_environ())
    assert restored.browser_frame_origins == config.browser_frame_origins
    assert restored.browser_allow_blob_frames is config.browser_allow_blob_frames


def test_explicit_config_does_not_inherit_frame_opt_in(monkeypatch):
    monkeypatch.setenv("PYTINCTURE_BROWSER_ALLOW_BLOB_FRAMES", "true")
    monkeypatch.setenv("PYTINCTURE_BROWSER_FRAME_ORIGINS", "https://frames.example")
    config = PytinctureConfig()
    assert config.browser_allow_blob_frames is False
    assert config.browser_frame_origins == ()
    assert PytinctureConfig.from_env().browser_allow_blob_frames is True


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None])
def test_blob_frames_requires_explicit_boolean(value):
    with pytest.raises(ValueError, match="browser_allow_blob_frames"):
        PytinctureConfig(browser_allow_blob_frames=value)


@pytest.mark.parametrize("origin", [
    "blob:", "data:", "'self'", "'none'", "https://example; object-src *",
    "https://example\r\nX-Injected: yes", "https://example#fragment",
])
def test_frame_origins_reject_schemes_and_policy_injection(origin):
    with pytest.raises(ValueError):
        PytinctureConfig(browser_frame_origins=(origin,))


def test_frame_origins_reject_duplicate_canonical_origins():
    with pytest.raises(ValueError):
        PytinctureConfig(browser_frame_origins=("https://EXAMPLE.com", "https://example.com"))


def test_frame_opt_in_preserves_response_specific_policy(tmp_path):
    app = create_app(PytinctureConfig(
        modules_path=str(tmp_path), browser_allow_blob_frames=True,
    ))
    restrictive_policy = "sandbox; default-src 'none'; frame-src 'none'"

    @app.get("/frame-tests/restricted-preview")
    async def restricted_preview():
        return HTMLResponse("preview", headers={"Content-Security-Policy": restrictive_policy})

    with TestClient(app) as client:
        assert _policy(client, "/frame-tests/restricted-preview") == restrictive_policy
        assert "frame-src 'self' blob:;" in _policy(client)
