"""SAML provider catalog parsing and selection."""

import json
import re
from typing import Any

from fastapi import HTTPException


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
