"""SAML provider configuration and bounded response pre-validation."""

import base64
import binascii
import json
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any

from fastapi import HTTPException


XMLDSIG_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
ALLOWED_XML_SIGNATURE_TRANSFORMS = frozenset(
    {
        "http://www.w3.org/2000/09/xmldsig#enveloped-signature",
        "http://www.w3.org/2001/10/xml-exc-c14n#",
        "http://www.w3.org/2001/10/xml-exc-c14n#WithComments",
        "http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
        "http://www.w3.org/TR/2001/REC-xml-c14n-20010315#WithComments",
        "http://www.w3.org/2006/12/xml-c14n11",
        "http://www.w3.org/2006/12/xml-c14n11#WithComments",
    }
)
_FORBIDDEN_XML_DECLARATION = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def validate_saml_response_xml(encoded_response: str, max_decoded_bytes: int) -> bytes:
    """Decode and safely inspect a POST-binding response before xmlsec sees it."""
    if not isinstance(encoded_response, str) or not encoded_response:
        raise ValueError("SAMLResponse is required")
    if max_decoded_bytes <= 0:
        raise ValueError("SAML response limit must be greater than zero")
    # Reject oversized base64 before allocating its decoded representation.
    max_encoded_bytes = ((max_decoded_bytes + 2) // 3) * 4
    if len(encoded_response) > max_encoded_bytes:
        raise ValueError("SAMLResponse exceeds the decoded size limit")
    try:
        xml_payload = base64.b64decode(encoded_response, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("SAMLResponse must contain valid base64") from exc
    if len(xml_payload) > max_decoded_bytes:
        raise ValueError("SAMLResponse exceeds the decoded size limit")
    if _FORBIDDEN_XML_DECLARATION.search(xml_payload):
        raise ValueError("SAMLResponse cannot contain DTD or entity declarations")

    try:
        from lxml import etree
    except ImportError as exc:  # pragma: no cover - guarded by the SAML extra
        raise RuntimeError("SAML response validation requires pytincture[saml]") from exc
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        recover=False,
    )
    try:
        root = etree.fromstring(xml_payload, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError("SAMLResponse must contain bounded, well-formed XML") from exc

    guarded_elements = {
        f"{{{XMLDSIG_NAMESPACE}}}Transform",
        f"{{{XMLDSIG_NAMESPACE}}}CanonicalizationMethod",
    }
    for element in root.iter():
        if element.tag not in guarded_elements:
            continue
        algorithm = element.get("Algorithm", "")
        if algorithm not in ALLOWED_XML_SIGNATURE_TRANSFORMS:
            raise ValueError("SAMLResponse contains a disallowed signature transform")
    return xml_payload


class SlidingWindowRateLimiter:
    """Small bounded in-process limiter used as ACS defense in depth."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = 10_000,
        clock=time.monotonic,
    ):
        if limit <= 0 or window_seconds <= 0 or max_keys <= 0:
            raise ValueError("rate-limit values must be greater than zero")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.clock = clock
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            attempts = self._entries.get(key)
            if attempts is None:
                if len(self._entries) >= self.max_keys:
                    self._entries.popitem(last=False)
                attempts = deque()
                self._entries[key] = attempts
            else:
                self._entries.move_to_end(key)
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0]) + 0.999))
                return False, retry_after
            attempts.append(now)
            return True, 0


def split_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def provider_value(
    provider: dict[str, Any] | None,
    *keys: str,
    default: Any = "",
) -> Any:
    if provider:
        for key in keys:
            value = provider.get(key)
            if value not in (None, ""):
                return value
    return default


def normalize_provider_id(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_-]+", "-", value.strip()).strip("-")
    return normalized.lower()


def normalize_provider(
    raw_provider: dict[str, Any], fallback_id: str
) -> dict[str, Any]:
    provider = dict(raw_provider)
    provider_id = normalize_provider_id(str(provider.get("id") or fallback_id))
    if not provider_id:
        raise RuntimeError("SAML provider id cannot be empty")
    provider["id"] = provider_id
    provider["label"] = str(
        provider.get("label") or provider.get("name") or f"Login with {provider_id}"
    )
    logo_url = (
        provider.get("logo_url")
        or provider.get("logo")
        or provider.get("logoUrl")
        or ""
    )
    provider["logo_url"] = str(logo_url)
    return provider


class SAMLProviderCatalog:
    """Parsed provider configuration with deterministic route metadata."""

    def __init__(
        self,
        configured: str | list[dict[str, Any]] | dict[str, dict[str, Any]] | None,
        *,
        default_label: str = "Login with SAML",
        default_logo_url: str = "",
    ):
        self.providers = self._load(configured, default_label, default_logo_url)

    @staticmethod
    def _load(configured, default_label, default_logo_url):
        if isinstance(configured, str):
            configured = configured.strip()
            if configured:
                try:
                    configured = json.loads(configured)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "Invalid JSON in SAML_PROVIDERS environment variable"
                    ) from exc
            else:
                configured = None

        providers: list[dict[str, Any]] = []
        if isinstance(configured, dict):
            for provider_id, provider_data in configured.items():
                if not isinstance(provider_data, dict):
                    raise RuntimeError("Each SAML_PROVIDERS entry must be an object")
                providers.append(normalize_provider(provider_data, str(provider_id)))
        elif isinstance(configured, list):
            for index, provider_data in enumerate(configured):
                if not isinstance(provider_data, dict):
                    raise RuntimeError("Each SAML_PROVIDERS entry must be an object")
                providers.append(
                    normalize_provider(provider_data, f"provider-{index + 1}")
                )
        elif configured is not None:
            raise RuntimeError("SAML_PROVIDERS must be a JSON object or array")

        if not providers:
            return [
                {
                    "id": "default",
                    "label": default_label or "Login with SAML",
                    "logo_url": default_logo_url,
                }
            ]
        ids = [provider["id"] for provider in providers]
        if len(ids) != len(set(ids)):
            duplicate = next(value for value in ids if ids.count(value) > 1)
            raise RuntimeError(f"Duplicate SAML provider id: {duplicate}")
        return providers

    def get(self, provider_id: str | None = None) -> dict[str, Any]:
        if provider_id is None:
            if len(self.providers) == 1:
                return self.providers[0]
            raise HTTPException(status_code=400, detail="SAML provider id is required")
        normalized_id = normalize_provider_id(provider_id)
        for provider in self.providers:
            if provider["id"] == normalized_id:
                return provider
        raise HTTPException(
            status_code=404,
            detail=f"SAML provider '{provider_id}' not found",
        )

    def login_buttons(self) -> list[dict[str, str]]:
        use_provider_routes = (
            len(self.providers) > 1 or self.providers[0]["id"] != "default"
        )
        return [
            {
                "href": (
                    f"auth/saml/{provider['id']}/login"
                    if use_provider_routes
                    else "auth/saml/login"
                ),
                "label": provider.get("label") or "Login with SAML",
                "logo_url": provider.get("logo_url") or "",
            }
            for provider in self.providers
        ]


def allowed_roles(
    provider: dict[str, Any] | None,
    default_roles: list[str],
) -> list[str]:
    configured = provider_value(
        provider,
        "allowed_roles",
        "allowedRoles",
        default=None,
    )
    roles = split_csv(configured) if configured is not None else default_roles
    return [role.lower() for role in roles]


def role_attribute_keys(
    provider: dict[str, Any] | None,
    default_keys: list[str],
) -> list[str]:
    configured = provider_value(
        provider,
        "role_attribute_keys",
        "roleAttributeKeys",
        default=None,
    )
    return split_csv(configured) if configured is not None else default_keys
