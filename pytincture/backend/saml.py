"""SAML provider configuration and bounded response pre-validation."""

import base64
import binascii
import json
import re
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException


XMLDSIG_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
SAML_ASSERTION_NAMESPACE = "urn:oasis:names:tc:SAML:2.0:assertion"
SAML_PROTOCOL_NAMESPACE = "urn:oasis:names:tc:SAML:2.0:protocol"
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
ALLOWED_XML_SIGNATURE_METHODS = frozenset(
    {
        "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384",
        "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512",
        "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256",
        "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384",
        "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512",
    }
)
ALLOWED_XML_DIGEST_METHODS = frozenset(
    {
        "http://www.w3.org/2001/04/xmlenc#sha256",
        "http://www.w3.org/2001/04/xmldsig-more#sha384",
        "http://www.w3.org/2001/04/xmlenc#sha512",
    }
)
_FORBIDDEN_XML_DECLARATION = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _parse_bounded_xml(xml_payload: bytes | str):
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
        return etree.fromstring(xml_payload, parser=parser)
    except (etree.XMLSyntaxError, TypeError, ValueError) as exc:
        raise ValueError("SAMLResponse must contain bounded, well-formed XML") from exc


def _validate_signature_algorithms(root) -> None:
    algorithm_elements = {
        f"{{{XMLDSIG_NAMESPACE}}}SignatureMethod": (
            ALLOWED_XML_SIGNATURE_METHODS,
            "signature",
        ),
        f"{{{XMLDSIG_NAMESPACE}}}DigestMethod": (
            ALLOWED_XML_DIGEST_METHODS,
            "digest",
        ),
    }
    for element in root.iter():
        allowed, label = algorithm_elements.get(element.tag, (None, None))
        if allowed is None:
            continue
        if element.get("Algorithm", "") not in allowed:
            raise ValueError(f"SAMLResponse contains a disallowed {label} algorithm")


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

    root = _parse_bounded_xml(xml_payload)

    encrypted_assertion_tag = f"{{{SAML_ASSERTION_NAMESPACE}}}EncryptedAssertion"
    if any(element.tag == encrypted_assertion_tag for element in root.iter()):
        # python3-saml 1.16 decrypts before signature processing, which would
        # hide transform algorithms from this preflight. Fail closed until a
        # toolkit can expose the decrypted tree before xmlsec sees it.
        raise ValueError("encrypted SAML assertions are not supported")

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

    _validate_signature_algorithms(root)
    return xml_payload


def validate_authenticated_saml_correlation(
    response_xml: str | bytes,
    request_id: str,
) -> None:
    """Require request correlation to be covered by a toolkit-validated signature."""
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("SAML request correlation is required")
    root = _parse_bounded_xml(response_xml)
    # This is the toolkit's decrypted, validated document, so it also covers
    # algorithms that were not visible in the original encrypted assertion.
    _validate_signature_algorithms(root)
    if root.tag != f"{{{SAML_PROTOCOL_NAMESPACE}}}Response":
        raise ValueError("SAML response root is invalid")
    if root.get("InResponseTo") != request_id:
        raise ValueError("SAML response correlation does not match the request")

    signature_tag = f"{{{XMLDSIG_NAMESPACE}}}Signature"
    if root.find(signature_tag) is not None:
        # process_response() has already cryptographically validated this exact
        # direct-child Response signature, which covers Response.InResponseTo.
        return

    assertion_tag = f"{{{SAML_ASSERTION_NAMESPACE}}}Assertion"
    subject_confirmation_data_tag = (
        f"{{{SAML_ASSERTION_NAMESPACE}}}Subject/"
        f"{{{SAML_ASSERTION_NAMESPACE}}}SubjectConfirmation/"
        f"{{{SAML_ASSERTION_NAMESPACE}}}SubjectConfirmationData"
    )
    for assertion in root.findall(assertion_tag):
        if assertion.find(signature_tag) is None:
            continue
        if any(
            element.get("InResponseTo") == request_id
            for element in assertion.findall(subject_confirmation_data_tag)
        ):
            # The assertion signature validated by process_response() covers
            # this SubjectConfirmationData correlation value.
            return
    raise ValueError(
        "SAML InResponseTo is not covered by a validated Response signature "
        "or signed assertion correlation"
    )


def _parse_saml_timestamp(value: str) -> float:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("SAML response contains an invalid expiration timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("SAML response expiration timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def saml_assertion_expirations(response_xml: str | bytes) -> list[float]:
    """Return signed-response assertion expiry bounds not exposed by the toolkit."""
    root = _parse_bounded_xml(response_xml)
    assertion_tag = f"{{{SAML_ASSERTION_NAMESPACE}}}Assertion"
    expiry_attributes = {
        f"{{{SAML_ASSERTION_NAMESPACE}}}Conditions": "NotOnOrAfter",
        f"{{{SAML_ASSERTION_NAMESPACE}}}SubjectConfirmationData": "NotOnOrAfter",
        f"{{{SAML_ASSERTION_NAMESPACE}}}AuthnStatement": "SessionNotOnOrAfter",
    }
    expirations: list[float] = []
    for assertion in root.findall(assertion_tag):
        for element in assertion.iter():
            attribute = expiry_attributes.get(element.tag)
            if attribute and element.get(attribute):
                expirations.append(_parse_saml_timestamp(element.get(attribute)))
    return expirations


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

    @classmethod
    def allow_all(
        cls,
        checks: tuple[tuple["SlidingWindowRateLimiter", str], ...],
    ) -> tuple[bool, int]:
        """Atomically admit every bucket or leave all bucket counts unchanged."""
        if not checks:
            return True, 0
        unique_limiters = {id(limiter): limiter for limiter, _ in checks}
        limiters = sorted(unique_limiters.values(), key=id)
        for limiter in limiters:
            limiter._lock.acquire()
        try:
            snapshots: dict[
                tuple[int, str],
                tuple[SlidingWindowRateLimiter, str, list[float], list[float]],
            ] = {}
            for limiter, key in checks:
                now = limiter.clock()
                cutoff = now - limiter.window_seconds
                snapshot_key = (id(limiter), key)
                snapshot = snapshots.get(snapshot_key)
                if snapshot is None:
                    active = [
                        timestamp
                        for timestamp in limiter._entries.get(key, ())
                        if timestamp > cutoff
                    ]
                    staged: list[float] = []
                    snapshot = (limiter, key, active, staged)
                    snapshots[snapshot_key] = snapshot
                else:
                    _, _, active, staged = snapshot
                    active[:] = [timestamp for timestamp in active if timestamp > cutoff]
                    staged[:] = [timestamp for timestamp in staged if timestamp > cutoff]
                if len(active) + len(staged) >= limiter.limit:
                    oldest = active[0] if active else staged[0]
                    retry_after = max(
                        1,
                        int(limiter.window_seconds - (now - oldest) + 0.999),
                    )
                    return False, retry_after
                staged.append(now)

            for limiter, key, active, staged in snapshots.values():
                if key not in limiter._entries and len(limiter._entries) >= limiter.max_keys:
                    limiter._entries.popitem(last=False)
                limiter._entries[key] = deque((*active, *staged))
                limiter._entries.move_to_end(key)
            return True, 0
        finally:
            for limiter in reversed(limiters):
                limiter._lock.release()

    def allow(self, key: str) -> tuple[bool, int]:
        return self.allow_all(((self, key),))


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
