"""Authentication primitives without application-global configuration."""

import json
from dataclasses import dataclass
from typing import Any

SENSITIVE_USER_CLAIM_KEYS = {
    "password",
    "password_hash",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
}

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$afcNkBX8goR7Ng5icg3p9w$"
    "UZsHTGXyFb9XrYQnpjpUvFRKKrc3WdWdH8oKTuGhX8M"
)


@dataclass(frozen=True)
class PasswordHashVerification:
    """Result of verifying one stored hash, including an optional safe upgrade."""

    authenticated: bool
    replacement_hash: str | None = None


def allowed_email(email: str, allowed_emails: str) -> bool:
    """Check a normalized email against an optional comma-separated allowlist."""
    configured = {
        value.strip().casefold() for value in allowed_emails.split(",") if value.strip()
    }
    return not configured or email.casefold() in configured


def _argon2_password_hasher():
    try:
        from argon2 import PasswordHasher
    except ImportError as exc:
        raise RuntimeError(
            "Local password authentication requires optional dependencies; "
            "install pytincture[password]"
        ) from exc
    return PasswordHasher()


def _verify_argon2_hash(encoded_hash: str, password: str) -> bool:
    try:
        from argon2.exceptions import VerificationError
    except ImportError as exc:
        raise RuntimeError(
            "Local password authentication requires optional dependencies; "
            "install pytincture[password]"
        ) from exc

    try:
        return bool(_argon2_password_hasher().verify(encoded_hash, password))
    except (VerificationError, ValueError, TypeError):
        return False


def _verify_bcrypt_hash(encoded_hash: str, password: str) -> bool:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        # bcrypt 5 rejects overlong input before doing expensive work. Use the
        # same Argon2id dummy verification as an unknown account, then deny it.
        _verify_argon2_hash(_DUMMY_PASSWORD_HASH, password)
        return False
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError(
            "Local password authentication requires optional dependencies; "
            "install pytincture[password]"
        ) from exc

    try:
        return bool(bcrypt.checkpw(password_bytes, encoded_hash.encode("utf-8")))
    except (ValueError, TypeError):
        return False


def verify_password_hash(
    password: str,
    encoded_hash: str,
    *,
    generate_replacement: bool = True,
) -> PasswordHashVerification:
    """Verify one hash and optionally return an Argon2id replacement.

    Credential stores that support writes can persist ``replacement_hash``
    after successful authentication. Static ``AUTH_PASSWORD_HASHES`` values
    remain immutable deployment configuration.
    """
    if encoded_hash.startswith("$argon2id$"):
        authenticated = _verify_argon2_hash(encoded_hash, password)
        replacement_hash = None
        if authenticated and generate_replacement:
            hasher = _argon2_password_hasher()
            if hasher.check_needs_rehash(encoded_hash):
                replacement_hash = hasher.hash(password)
        return PasswordHashVerification(authenticated, replacement_hash)
    if encoded_hash.startswith(("$2a$", "$2b$", "$2y$")):
        authenticated = _verify_bcrypt_hash(encoded_hash, password)
        replacement_hash = None
        if authenticated and generate_replacement:
            replacement_hash = _argon2_password_hasher().hash(password)
        return PasswordHashVerification(authenticated, replacement_hash)
    raise RuntimeError("Password hashes must be Argon2id or bcrypt")


def verify_password(email: str, password: str, raw_hashes: str) -> bool:
    """Verify configured credentials with equal oversized/unknown failure work."""
    if not raw_hashes.strip():
        raw_hashes = "{}"
    try:
        password_hashes = json.loads(raw_hashes)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTH_PASSWORD_HASHES must be a JSON object") from exc
    if not isinstance(password_hashes, dict):
        raise RuntimeError("AUTH_PASSWORD_HASHES must be a JSON object")
    configured_hash = password_hashes.get(email) or password_hashes.get(
        email.casefold()
    )
    known_user = isinstance(configured_hash, str)
    encoded_hash = configured_hash if known_user else _DUMMY_PASSWORD_HASH
    if known_user and not encoded_hash.startswith(
        ("$argon2id$", "$2a$", "$2b$", "$2y$")
    ):
        raise RuntimeError(
            "AUTH_PASSWORD_HASHES values must be Argon2id or bcrypt hashes"
        )
    oversized_bcrypt = bool(
        known_user
        and encoded_hash.startswith(("$2a$", "$2b$", "$2y$"))
        and len(password.encode("utf-8")) > 72
    )
    if oversized_bcrypt:
        encoded_hash = _DUMMY_PASSWORD_HASH
    result = verify_password_hash(
        password,
        encoded_hash,
        generate_replacement=False,
    )
    return known_user and not oversized_bcrypt and result.authenticated


def local_user_claims(email: str, raw_claims: str, source_name: str) -> dict[str, Any]:
    """Load non-sensitive profile claims for a normalized email."""
    if not raw_claims.strip():
        return {"email": email}
    try:
        configured = json.loads(raw_claims)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source_name} must contain valid JSON") from exc

    matched: dict[str, Any] | None = None
    if isinstance(configured, list):
        for candidate in configured:
            if (
                isinstance(candidate, dict)
                and str(candidate.get("email") or "").strip().casefold() == email
            ):
                matched = candidate
                break
    elif isinstance(configured, dict):
        for configured_email, candidate in configured.items():
            if str(configured_email).strip().casefold() == email and isinstance(
                candidate, dict
            ):
                matched = candidate
                break
    else:
        raise RuntimeError(
            f"{source_name} must be a user list or email-to-claims object"
        )

    if matched is None:
        return {"email": email}
    claims = {
        str(key): value
        for key, value in matched.items()
        if str(key).casefold() not in SENSITIVE_USER_CLAIM_KEYS
    }
    claims["email"] = email
    return claims


def normalize_roles(value: Any) -> list[str]:
    """Normalize role claims to a stable, sorted lower-case list."""
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = []
    return sorted(
        {str(role).strip().lower() for role in candidates if str(role).strip()}
    )
