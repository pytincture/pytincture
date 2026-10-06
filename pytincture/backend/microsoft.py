"""Microsoft organization identities and opt-in multi-tenant OIDC validation."""

from __future__ import annotations

import re
from typing import Mapping


MICROSOFT_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"
MICROSOFT_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
_TENANT_GUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def valid_microsoft_tenant_id(value: str, *, allow_multitenant: bool = False) -> bool:
    candidate = value.strip()
    if candidate.casefold() == "organizations":
        return allow_multitenant
    return bool(
        candidate
        and candidate.casefold() not in {"common", "consumers"}
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", candidate)
    )


def valid_microsoft_organization_identity(claims: Mapping[str, object]) -> bool:
    tenant = claims.get("tid")
    return bool(
        isinstance(tenant, str)
        and _TENANT_GUID.fullmatch(tenant)
        and tenant.casefold() != MICROSOFT_CONSUMER_TENANT
        and claims.get("iss") == MICROSOFT_ISSUER_TEMPLATE.format(tenantid=tenant)
        and isinstance(claims.get("sub"), str)
        and claims["sub"]
    )


def microsoft_organizations_client_cls():
    """Load Authlib only for deployments using the OAuth extra."""
    from authlib.integrations.starlette_client import OAuthError, StarletteOAuth2App
    from authlib.oidc.core import UserInfo
    from httpx import HTTPError

    class MicrosoftOrganizationsClient(StarletteOAuth2App):
        async def authorize_access_token(self, request, **kwargs):
            token = await super().authorize_access_token(request, **kwargs)
            if not token.get("id_token") or not isinstance(token.get("userinfo"), UserInfo):
                raise OAuthError(error="invalid_id_token")
            return token

        async def parse_id_token(self, token, nonce, claims_options=None, **kwargs):
            metadata = await self.load_server_metadata()
            if metadata.get("issuer") != MICROSOFT_ISSUER_TEMPLATE or not nonce:
                raise OAuthError(error="invalid_id_token")

            def validate_organization_issuer(claims, issuer):
                if claims.get("nonce_supported") is False:
                    return False
                if not valid_microsoft_organization_identity(claims):
                    return False
                # Authlib caches the JWKS used for signature validation.
                # Decode and claims validation are synchronous after that
                # fetch (including refresh on an unknown kid), so this is
                # the same key set, without request-specific shared state.
                keys = self.server_metadata.get("jwks", {}).get("keys", [])
                kid = claims.header.get("kid")
                matching = [key for key in keys if key.get("kid") == kid]
                return bool(kid and matching) and all(
                    key.get("issuer") in {MICROSOFT_ISSUER_TEMPLATE, issuer}
                    for key in matching
                )

            try:
                # All standard signature, audience, lifetime, nonce, azp and
                # at_hash checks stay in Authlib. Only its literal issuer check
                # is replaced with the tenant-template and key-scope checks.
                return await super().parse_id_token(
                    token, nonce,
                    claims_options={"iss": {
                        "essential": True, "validate": validate_organization_issuer,
                    }},
                    **kwargs,
                )
            except (HTTPError, TimeoutError, OAuthError):
                raise
            except Exception as exc:
                # Authlib's JOSE exception classes differ across supported
                # releases. Invalid tokens must produce a sanitized 401.
                raise OAuthError(error="invalid_id_token") from exc

    return MicrosoftOrganizationsClient
