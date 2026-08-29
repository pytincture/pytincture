"""SAML provider configuration and bounded response pre-validation."""

import base64
import binascii
import hashlib
import hmac
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


def _replay_marker(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("SAML response and assertion IDs are required")
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


class InMemorySAMLTransactionStore:
    """Thread-safe one-time SAML state for a single-worker deployment."""

    def __init__(
        self,
        *,
        clock=time.monotonic,
        max_transactions: int = 10_000,
        max_replay_markers: int = 20_000,
    ):
        if max_transactions <= 0 or max_replay_markers < 2:
            raise ValueError("SAML store bounds must allow transactions and replay IDs")
        self.clock = clock
        self.max_transactions = max_transactions
        self.max_replay_markers = max_replay_markers
        self._transactions: dict[str, tuple[float, dict[str, Any]]] = {}
        self._replay_markers: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._transactions = {
            key: value for key, value in self._transactions.items() if value[0] > now
        }
        self._replay_markers = {
            key: expires_at
            for key, expires_at in self._replay_markers.items()
            if expires_at > now
        }

    def create(self, transaction_id: str, record: dict[str, Any], ttl_seconds: int) -> bool:
        now = self.clock()
        with self._lock:
            self._prune(now)
            if transaction_id in self._transactions:
                return False
            if len(self._transactions) >= self.max_transactions:
                del self._transactions[next(iter(self._transactions))]
            self._transactions[transaction_id] = (
                now + ttl_seconds,
                dict(record),
            )
            return True

    def peek(self, transaction_id: str) -> dict[str, Any] | None:
        now = self.clock()
        with self._lock:
            self._prune(now)
            stored = self._transactions.get(transaction_id)
            return dict(stored[1]) if stored else None

    def consume(
        self,
        transaction_id: str,
        expected: dict[str, Any],
        response_id: str,
        assertion_id: str,
        marker_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        response_marker = f"response:{_replay_marker(response_id)}"
        assertion_marker = f"assertion:{_replay_marker(assertion_id)}"
        now = self.clock()
        with self._lock:
            self._prune(now)
            stored = self._transactions.get(transaction_id)
            if stored is None:
                return None
            record = stored[1]
            if any(
                not hmac.compare_digest(str(record.get(key, "")), str(value))
                for key, value in expected.items()
            ):
                return None
            if (
                response_marker in self._replay_markers
                or assertion_marker in self._replay_markers
            ):
                return None
            del self._transactions[transaction_id]
            expires_at = now + marker_ttl_seconds
            while len(self._replay_markers) > self.max_replay_markers - 2:
                del self._replay_markers[next(iter(self._replay_markers))]
            self._replay_markers[response_marker] = expires_at
            self._replay_markers[assertion_marker] = expires_at
            return dict(record)


class RedisSAMLTransactionStore:
    """Redis-backed atomic SAML state shared by workers and replicas."""

    _CONSUME_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if not value then return {0, 'missing'} end
local record = cjson.decode(value)
local expected = cjson.decode(ARGV[1])
for key, expected_value in pairs(expected) do
    if tostring(record[key] or '') ~= tostring(expected_value) then
        return {0, 'mismatch'}
    end
end
if redis.call('EXISTS', KEYS[2]) == 1 or redis.call('EXISTS', KEYS[3]) == 1 then
    return {0, 'replay'}
end
redis.call('DEL', KEYS[1])
redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
redis.call('SET', KEYS[3], '1', 'EX', ARGV[2])
return {1, value}
"""

    def __init__(
        self,
        redis_url: str = "",
        redis_token: str = "",
        *,
        redis_client: Any = None,
        key_prefix: str = "saml-handshake:",
    ):
        if redis_client is None:
            try:
                from upstash_redis import Redis
            except ImportError as exc:  # pragma: no cover - guarded by redis extra
                raise RuntimeError(
                    "Shared SAML transactions require pytincture[redis]"
                ) from exc
            redis_client = Redis(url=redis_url, token=redis_token)
        self._redis = redis_client
        self._prefix = key_prefix

    def _transaction_key(self, transaction_id: str) -> str:
        return f"{self._prefix}transaction:{transaction_id}"

    def ping(self) -> bool:
        result = self._redis.ping()
        return result is True or result == "PONG"

    def create(self, transaction_id: str, record: dict[str, Any], ttl_seconds: int) -> bool:
        result = self._redis.set(
            self._transaction_key(transaction_id),
            json.dumps(record, separators=(",", ":"), sort_keys=True),
            nx=True,
            ex=ttl_seconds,
        )
        return result is True or result == "OK"

    def peek(self, transaction_id: str) -> dict[str, Any] | None:
        value = self._redis.get(self._transaction_key(transaction_id))
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else None

    def consume(
        self,
        transaction_id: str,
        expected: dict[str, Any],
        response_id: str,
        assertion_id: str,
        marker_ttl_seconds: int,
    ) -> dict[str, Any] | None:
        keys = [
            self._transaction_key(transaction_id),
            f"{self._prefix}response:{_replay_marker(response_id)}",
            f"{self._prefix}assertion:{_replay_marker(assertion_id)}",
        ]
        result = self._redis.eval(
            self._CONSUME_SCRIPT,
            keys=keys,
            args=[
                json.dumps(expected, separators=(",", ":"), sort_keys=True),
                str(marker_ttl_seconds),
            ],
        )
        if not isinstance(result, (list, tuple)) or not result or int(result[0]) != 1:
            return None
        value = result[1]
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else None


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
