"""Stateless per-application identity admission rules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pytincture.backend.auth import normalize_roles
from pytincture.backend.safe_paths import validate_application_name


_RULE_FIELDS = {
    "providers",
    "issuers",
    "tenants",
    "subjects",
    "emails",
    "email_domains",
    "roles",
}
_CASEFOLD_FIELDS = {"emails", "email_domains", "roles"}


def _string_values(application: str, field: str, value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        raise ValueError(
            f"application admission {application}.{field} must be a string or list of strings"
        )
    if not candidates or any(not isinstance(item, str) for item in candidates):
        raise ValueError(
            f"application admission {application}.{field} must contain strings"
        )

    normalized: list[str] = []
    for candidate in candidates:
        item = candidate.strip()
        if field == "email_domains":
            item = item.removeprefix("@").casefold()
        elif field in _CASEFOLD_FIELDS:
            item = item.casefold()
        if not item or item == "*":
            raise ValueError(
                f"application admission {application}.{field} cannot contain empty values or wildcards"
            )
        if field == "emails" and ("@" not in item or item.startswith("@") or item.endswith("@")):
            raise ValueError(
                f"application admission {application}.emails contains an invalid email"
            )
        if field == "email_domains" and (
            "@" in item
            or any(character.isspace() for character in item)
            or item.startswith(".")
            or item.endswith(".")
        ):
            raise ValueError(
                f"application admission {application}.email_domains contains an invalid domain"
            )
        normalized.append(item)
    return tuple(dict.fromkeys(normalized))


def parse_application_admission(
    raw: str | Mapping[str, Any] | None,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Parse and strictly validate an application-to-identity-rules mapping."""

    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("application admission must contain valid JSON") from exc
    elif isinstance(raw, Mapping):
        decoded = dict(raw)
    else:
        raise ValueError("application admission must be a JSON object")
    if not isinstance(decoded, dict):
        raise ValueError("application admission must be a JSON object")

    policies: dict[str, dict[str, tuple[str, ...]]] = {}
    for raw_application, raw_rule in decoded.items():
        if not isinstance(raw_application, str):
            raise ValueError("application admission names must be strings")
        application = raw_application.strip()
        try:
            validate_application_name(application)
        except ValueError as exc:
            raise ValueError(
                f"invalid application admission name: {raw_application!r}"
            ) from exc
        if not isinstance(raw_rule, dict):
            raise ValueError(
                f"application admission rule for {application} must be an object"
            )
        if any(not isinstance(field, str) for field in raw_rule):
            raise ValueError(
                f"application admission rule for {application} has non-string fields"
            )
        unexpected = sorted(set(raw_rule) - _RULE_FIELDS)
        if unexpected:
            raise ValueError(
                f"application admission rule for {application} has unknown fields: "
                f"{', '.join(unexpected)}"
            )
        policies[application] = {
            field: _string_values(application, field, value)
            for field, value in raw_rule.items()
        }
    return policies


def canonical_application_admission(
    raw: str | Mapping[str, Any] | None,
) -> str:
    """Return stable JSON after validating a configured policy."""

    policies = parse_application_admission(raw)
    if not policies:
        return ""
    serializable = {
        application: {
            field: list(values) for field, values in sorted(rule.items())
        }
        for application, rule in sorted(policies.items())
    }
    return json.dumps(serializable, separators=(",", ":"), sort_keys=True)


def identity_is_admitted(
    policies: Mapping[str, Mapping[str, tuple[str, ...]]],
    application: str,
    identity: Mapping[str, Any],
) -> bool:
    """Return whether an authenticated identity satisfies an application's rule.

    An empty policy set is the explicit single-trust compatibility mode. Once
    any application is configured, missing applications fail closed. Values
    within one field are alternatives; every configured field must match.
    """

    if not policies:
        return True
    rule = policies.get(application)
    if rule is None:
        return False

    email = str(identity.get("email") or "").strip().casefold()
    email_domain = email.rpartition("@")[2] if "@" in email else ""
    provider = str(
        identity.get("auth_provider") or identity.get("auth_type") or ""
    ).strip()
    scalar_claims = {
        "providers": provider,
        "issuers": str(identity.get("issuer") or identity.get("iss") or "").strip(),
        "tenants": str(
            identity.get("tenant") or identity.get("tenant_id") or identity.get("tid") or ""
        ).strip(),
        "subjects": str(identity.get("subject") or identity.get("sub") or "").strip(),
        "emails": email,
        "email_domains": email_domain,
    }
    for field, actual in scalar_claims.items():
        expected = rule.get(field)
        if expected is not None and actual not in expected:
            return False

    expected_roles = rule.get("roles")
    if expected_roles is not None:
        actual_roles = set(normalize_roles(identity.get("roles", identity.get("role"))))
        if not actual_roles.intersection(expected_roles):
            return False
    return True
