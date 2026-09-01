"""Authenticated Pytincture service used by Playwright end-to-end tests."""

from pathlib import Path

import uvicorn

from pytincture import PytinctureConfig, create_app


ROOT = Path(__file__).resolve().parent / "e2e_apps"
WIDGET_VERSION = "0.9.18+backend"
WIDGET_WHEEL = ROOT / f"dhxpyt-{WIDGET_VERSION}-py3-none-any.whl"

if not WIDGET_WHEEL.is_file():
    raise RuntimeError(
        f"Missing {WIDGET_WHEEL.name}; download dhxpyt==0.9.18 without dependencies "
        f"and copy the wheel to {WIDGET_WHEEL.name}"
    )

config = PytinctureConfig(
    modules_path=str(ROOT),
    default_application="e2e_app",
    enable_user_login=True,
    allow_development_auth_origin=True,
    session_secret="pytincture-e2e-session-secret-0123456789abcdef",
    session_https_only=False,
    browser_connect_origins=("https://api.example.test",),
    environment={
        "ALLOWED_EMAILS": "e2e@example.com",
        "AUTH_PASSWORD_HASHES": (
            '{"e2e@example.com":"$argon2id$v=19$m=65536,t=3,p=4$'
            "1nAFATBkZHf7FYm10EoAqw$"
            'bcQeiCVDJV5nH2dSoHhYtUlyLARtmS1ce7UBSUXokYQ"}'
        ),
        "AUTH_USER_CLAIMS": '{"e2e@example.com":{"role":"tester"}}',
        "LOGIN_HELP_TEXT": "E2E credentials: e2e@example.com / demo-password",
        "PYTINCTURE_BROWSER_FILES": '["dynamic_module.py", "e2e.css"]',
        "PYTINCTURE_PUBLIC_ASSET_PATHS": "inline-e2e.html,active.svg",
    },
)

app = create_app(config)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8079, log_level="info")
