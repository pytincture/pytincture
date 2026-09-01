"""Pytincture service configured from a live Keycloak SAML descriptor."""

from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import urlopen
from xml.etree import ElementTree

import uvicorn

from pytincture import PytinctureConfig, create_app


ROOT = Path(__file__).resolve().parent / "e2e_apps"
IDP_BASE_URL = "http://127.0.0.1:8085/realms/pytincture-acceptance"
IDP_METADATA_URL = f"{IDP_BASE_URL}/protocol/saml/descriptor"
WIDGET_VERSION = "0.9.18+backend"
WIDGET_WHEEL = ROOT / f"dhxpyt-{WIDGET_VERSION}-py3-none-any.whl"


def _load_idp_certificate(timeout_seconds: float = 60.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(IDP_METADATA_URL, timeout=5) as response:  # noqa: S310 - fixed local URL
                metadata = response.read()
            root = ElementTree.fromstring(metadata)
            certificate = root.findtext(
                ".//{http://www.w3.org/2000/09/xmldsig#}X509Certificate"
            )
            if certificate and certificate.strip():
                return "".join(certificate.split())
        except (OSError, URLError, ElementTree.ParseError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Keycloak SAML metadata did not become ready: {last_error}")


if not WIDGET_WHEEL.is_file():
    raise RuntimeError(f"Missing backend widget wheel: {WIDGET_WHEEL}")

idp_certificate = _load_idp_certificate()
config = PytinctureConfig(
    modules_path=str(ROOT),
    default_application="e2e_app",
    enable_saml_auth=True,
    allow_development_auth_origin=True,
    saml_idp_entity_id=IDP_BASE_URL,
    saml_idp_sso_url=f"{IDP_BASE_URL}/protocol/saml",
    saml_idp_x509_cert=idp_certificate,
    session_secret="pytincture-saml-acceptance-session-secret-0123456789",
    session_https_only=False,
    environment={
        "ALLOWED_EMAILS": "saml@example.com",
        "PYTINCTURE_BROWSER_FILES": '["dynamic_module.py", "e2e.css"]',
        "PYTINCTURE_PUBLIC_ASSET_PATHS": "inline-e2e.html",
        "SAML_REQUESTED_AUTHN_CONTEXT": "false",
    },
)

app = create_app(config)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8084, log_level="info")
