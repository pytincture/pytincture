"""Authenticated Pytincture service used by Playwright end-to-end tests."""

from pathlib import Path
import json
import os

import uvicorn

from pytincture import PytinctureConfig, create_app
from pytincture.api_clients import create_client


ROOT = Path(__file__).resolve().parent / "e2e_apps"
WIDGET_VERSION = "0.9.18+backend"
WIDGET_WHEEL = ROOT / f"dhxpyt-{WIDGET_VERSION}-py3-none-any.whl"

if not WIDGET_WHEEL.is_file():
    raise RuntimeError(
        f"Missing {WIDGET_WHEEL.name}; download dhxpyt==0.9.18 without dependencies "
        f"and copy the wheel to {WIDGET_WHEEL.name}"
    )

PRIVATE = ROOT.parent / '.e2e-private'
PRIVATE.mkdir(mode=0o700, exist_ok=True)
REGISTRY = PRIVATE / 'clients.sqlite3'
client_credentials = create_client(str(REGISTRY), 'e2e_app', [
    {'module': 'e2e_data', 'class': 'E2EData', 'methods': ['sync_call']},
])
with os.fdopen(os.open(PRIVATE / 'client.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
    json.dump(client_credentials, handle)

config = PytinctureConfig(
    modules_path=str(ROOT),
    default_application="e2e_app",
    enable_user_login=True,
    enable_bff_api_tokens=True,
    bff_api_client_registry=str(REGISTRY),
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
