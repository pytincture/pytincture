"""Typed, validated configuration for isolated Pytincture applications."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping, Optional
from urllib.parse import urlparse

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from pytincture.backend.application_admission import canonical_application_admission
from pytincture.backend.safe_paths import validate_application_name
from pytincture.backend.storage import validate_redis_url
from pytincture.backend.widget_trust import canonical_widget_trust_policy


_MICROSOFT_SHARED_TENANTS = {"common", "organizations", "consumers"}


def _is_literal_loopback_host(value: str) -> bool:
    candidate = value.strip()
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        pass
    try:
        parsed = urlparse(f"//{candidate}")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return False
        parsed.port
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _is_literal_loopback_origin(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        parsed.port
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _valid_microsoft_tenant_id(value: str) -> bool:
    normalized = value.strip().casefold()
    return bool(
        normalized
        and normalized not in _MICROSOFT_SHARED_TENANTS
        and re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",
            value.strip(),
        )
    )


def _canonical_browser_connect_origin(value: str) -> str:
    candidate = str(value)
    if not candidate or candidate != candidate.strip():
        raise ValueError("browser connect origins must not contain surrounding whitespace")
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid browser connect origin: {candidate!r}") from exc
    if (
        parsed.scheme.lower() not in {"https", "wss"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or "*" in candidate
    ):
        raise ValueError(
            "browser connect origins must be exact HTTPS or WSS origins "
            "without credentials, wildcards, paths, queries, or fragments"
        )

    hostname = parsed.hostname
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
            hostname,
        ):
            raise ValueError(f"invalid browser connect origin host: {hostname!r}")
        canonical_host = hostname.casefold()
    else:
        canonical_host = f"[{address.compressed}]" if address.version == 6 else str(address)

    canonical = f"{parsed.scheme.lower()}://{canonical_host}"
    if port is not None:
        canonical += f":{port}"
    if candidate.casefold() != canonical.casefold():
        raise ValueError(
            "browser connect origins must use canonical origin-only syntax"
        )
    return canonical


def canonical_browser_connect_origins(values: object) -> tuple[str, ...]:
    origins = tuple(values or ())
    canonical = tuple(_canonical_browser_connect_origin(value) for value in origins)
    if len(set(canonical)) != len(canonical):
        raise ValueError("browser connect origins must not contain duplicates")
    return canonical


def canonical_widget_public_index_specs(values: object) -> tuple[str, ...]:
    """Return exact normalized widget specs explicitly allowed from PyPI."""

    specs: list[str] = []
    for raw in tuple(values or ()):
        if not isinstance(raw, str) or raw != raw.strip():
            raise ValueError(
                "widget public-index allowlist entries must be exact name==version strings"
            )
        distribution, separator, version_text = raw.partition("==")
        if not separator or "==" in version_text:
            raise ValueError(
                "widget public-index allowlist entries must be exact name==version strings"
            )
        try:
            normalized_distribution = canonicalize_name(
                distribution, validate=True
            )
            version = Version(version_text)
        except (InvalidVersion, ValueError) as exc:
            raise ValueError(
                "widget public-index allowlist entries must be exact name==version strings"
            ) from exc
        specs.append(f"{normalized_distribution}=={version}")
    if len(set(specs)) != len(specs):
        raise ValueError("widget public-index allowlist must not contain duplicates")
    return tuple(specs)


def _setting(default, env: str, description: str, *, repr: bool = True):
    return field(
        default=default,
        repr=repr,
        metadata={"env": env, "description": description},
    )


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"expected a boolean value, received {value!r}")


def modules_path_appears_writable(path: str | Path) -> bool:
    """Best-effort check using the effective service account and mount flags."""

    candidate = Path(path)
    try:
        stat = os.statvfs(candidate)
        readonly_flag = getattr(os, "ST_RDONLY", 1)
        if stat.f_flag & readonly_flag:
            return False
    except (AttributeError, OSError):
        pass
    try:
        return os.access(candidate, os.W_OK, effective_ids=True)
    except (NotImplementedError, TypeError):
        return os.access(candidate, os.W_OK)


def _csv(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    return tuple(str(item).strip() for item in value if str(item).strip())


def _json_or_csv(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return _csv(value)
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return _csv(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("expected a JSON list of strings")
    return tuple(decoded)


@dataclass(frozen=True, slots=True)
class PytinctureConfig:
    """Configuration for one Pytincture ASGI application.

    Constructing the dataclass uses explicit values and safe defaults.
    ``from_env()`` applies process/provided environment values, then keyword
    overrides. Explicit keyword overrides therefore have highest precedence.
    """

    modules_path: str = _setting(".", "MODULES_PATH", "Application module root.")
    require_readonly_modules_path: bool = _setting(
        False,
        "PYTINCTURE_REQUIRE_READONLY_MODULES_PATH",
        "Fail startup when the effective service account can write the module root.",
    )
    default_application: Optional[str] = _setting(
        None, "PYTINCTURE_DEFAULT_APPLICATION", "Optional application for the root redirect."
    )
    favicon_folder: Optional[str] = _setting(
        None, "PYTINCTURE_FAVICON_FOLDER", "Optional favicon file/directory."
    )
    cors_allowed_origins: tuple[str, ...] = _setting(
        (), "CORS_ALLOWED_ORIGINS", "Allowed browser origins."
    )
    browser_connect_origins: tuple[str, ...] = _setting(
        (),
        "PYTINCTURE_BROWSER_CONNECT_ORIGINS",
        "Exact additional HTTPS/WSS origins permitted by browser connect-src.",
    )
    allowed_hosts: tuple[str, ...] = _setting(
        (), "PYTINCTURE_ALLOWED_HOSTS", "Allowed HTTP Host header names."
    )
    canonical_origin: Optional[str] = _setting(
        None,
        "PYTINCTURE_CANONICAL_ORIGIN",
        "Canonical external HTTP(S) origin for authentication callbacks.",
    )
    enable_user_login: bool = _setting(False, "ENABLE_USER_LOGIN", "Enable local user login.")
    enable_dev_email_login: bool = _setting(
        False, "ENABLE_DEV_EMAIL_LOGIN", "Enable loopback-only development email login."
    )
    enable_google_auth: bool = _setting(False, "ENABLE_GOOGLE_AUTH", "Enable Google OAuth.")
    enable_microsoft_auth: bool = _setting(
        False, "ENABLE_MICROSOFT_AUTH", "Enable Microsoft OAuth."
    )
    enable_saml_auth: bool = _setting(False, "ENABLE_SAML_AUTH", "Enable SAML authentication.")
    application_admission: str | Mapping[str, object] = _setting(
        "",
        "AUTH_APPLICATION_ADMISSION",
        "JSON per-application identity admission rules.",
        repr=False,
    )
    google_client_id: str = _setting("", "GOOGLE_CLIENT_ID", "Google OAuth client id.")
    google_client_secret: str = _setting(
        "", "GOOGLE_CLIENT_SECRET", "Google OAuth client secret.", repr=False
    )
    microsoft_client_id: str = _setting(
        "", "MICROSOFT_CLIENT_ID", "Microsoft OAuth client id."
    )
    microsoft_client_secret: str = _setting(
        "", "MICROSOFT_CLIENT_SECRET", "Microsoft OAuth client secret.", repr=False
    )
    microsoft_tenant_id: str = _setting(
        "", "MICROSOFT_TENANT_ID", "Required Microsoft Entra tenant id."
    )
    saml_providers: str = _setting(
        "", "SAML_PROVIDERS", "JSON object or array of SAML identity providers.", repr=False
    )
    saml_idp_entity_id: str = _setting(
        "", "SAML_IDP_ENTITY_ID", "Default SAML identity-provider entity id."
    )
    saml_idp_sso_url: str = _setting(
        "", "SAML_IDP_SSO_URL", "Default SAML identity-provider sign-in URL."
    )
    saml_idp_x509_cert: str = _setting(
        "", "SAML_IDP_X509_CERT", "Default SAML identity-provider certificate."
    )
    saml_transaction_ttl_seconds: int = _setting(
        600,
        "SAML_RELAY_STATE_TTL_SECONDS",
        "Maximum lifetime of a browser-bound one-time SAML transaction.",
    )
    saml_response_max_bytes: int = _setting(
        512 * 1024,
        "SAML_RESPONSE_MAX_BYTES",
        "Maximum decoded SAML response size before signature processing.",
    )
    saml_acs_rate_limit_attempts: int = _setting(
        60,
        "SAML_ACS_RATE_LIMIT_ATTEMPTS",
        "Maximum SAML ACS attempts per peer in one window.",
    )
    saml_acs_rate_limit_window_seconds: int = _setting(
        60,
        "SAML_ACS_RATE_LIMIT_WINDOW_SECONDS",
        "SAML ACS rate-limit window in seconds.",
    )
    saml_validation_max_concurrency: int = _setting(
        2,
        "SAML_VALIDATION_MAX_CONCURRENCY",
        "Concurrent SAML XML/signature validations per worker.",
    )
    saml_validation_max_queue: int = _setting(
        8,
        "SAML_VALIDATION_MAX_QUEUE",
        "Maximum queued SAML validations per worker.",
    )
    saml_validation_queue_timeout_seconds: float = _setting(
        1.0,
        "SAML_VALIDATION_QUEUE_TIMEOUT_SECONDS",
        "Maximum SAML validation admission wait.",
    )
    saml_validation_timeout_seconds: float = _setting(
        10.0,
        "SAML_VALIDATION_TIMEOUT_SECONDS",
        "Maximum wait for one SAML validation stage.",
    )
    session_secret: str = _setting(
        "", "SAML_SECRET_KEY", "Session signing secret.", repr=False
    )
    previous_session_secrets: tuple[str, ...] = _setting(
        (), "AUTH_SESSION_PREVIOUS_SECRET_KEYS", "Previous signing keys accepted during rotation.", repr=False
    )
    session_max_age_seconds: int = _setting(
        28800, "AUTH_SESSION_MAX_AGE_SECONDS", "Session idle lifetime."
    )
    session_absolute_max_age_seconds: int = _setting(
        86400, "AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS", "Absolute authenticated session lifetime."
    )
    session_https_only: Optional[bool] = _setting(
        None, "AUTH_SESSION_HTTPS_ONLY", "Secure-cookie requirement; derived when omitted."
    )
    session_same_site: str = _setting("lax", "AUTH_SESSION_SAME_SITE", "Cookie SameSite policy.")
    session_max_claim_count: int = _setting(
        32,
        "AUTH_SESSION_MAX_CLAIM_COUNT",
        "Maximum keys retained in an authenticated session identity.",
    )
    session_max_identity_bytes: int = _setting(
        2048,
        "AUTH_SESSION_MAX_IDENTITY_BYTES",
        "Maximum canonical JSON bytes retained for an authenticated identity.",
    )
    session_max_cookie_bytes: int = _setting(
        3800,
        "AUTH_SESSION_MAX_COOKIE_BYTES",
        "Maximum signed browser-session cookie value bytes.",
    )
    max_request_body_bytes: int = _setting(
        2 * 1024 * 1024, "MAX_REQUEST_BODY_BYTES", "Maximum request body size."
    )
    enable_browser_logs: bool = _setting(
        True,
        "ENABLE_BROWSER_LOGS",
        "Accept bounded browser diagnostics for authenticated services.",
    )
    allow_noauth_browser_logs: bool = _setting(
        False,
        "ALLOW_NOAUTH_BROWSER_LOGS",
        "Explicitly expose bounded browser diagnostics in no-auth services.",
    )
    browser_log_max_bytes: int = _setting(
        4096,
        "BROWSER_LOG_MAX_BYTES",
        "Maximum browser diagnostic request bytes.",
    )
    browser_log_rate_limit_attempts: int = _setting(
        60,
        "BROWSER_LOG_RATE_LIMIT_ATTEMPTS",
        "Browser diagnostic requests allowed per peer and window.",
    )
    browser_log_rate_limit_window_seconds: int = _setting(
        60,
        "BROWSER_LOG_RATE_LIMIT_WINDOW_SECONDS",
        "Browser diagnostic rate-limit window in seconds.",
    )
    api_docs_mode: str = _setting(
        "public",
        "PYTINCTURE_API_DOCS_MODE",
        "API documentation mode: public, authenticated, or disabled.",
    )
    uvicorn_access_log: bool = _setting(
        False,
        "PYTINCTURE_UVICORN_ACCESS_LOG",
        "Enable sanitized path-only Uvicorn access logs.",
    )
    login_rate_limit_attempts: int = _setting(
        20, "AUTH_LOGIN_RATE_LIMIT_ATTEMPTS", "Password attempts per peer and window."
    )
    login_rate_limit_window_seconds: int = _setting(
        60, "AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "Password rate-limit window."
    )
    login_email_max_chars: int = _setting(
        320, "AUTH_LOGIN_EMAIL_MAX_CHARS", "Maximum submitted email length."
    )
    login_password_max_chars: int = _setting(
        1024, "AUTH_LOGIN_PASSWORD_MAX_CHARS", "Maximum submitted password length."
    )
    login_csrf_ttl_seconds: int = _setting(
        600,
        "AUTH_LOGIN_CSRF_TTL_SECONDS",
        "Lifetime of a one-time password-login CSRF transaction.",
    )
    password_hash_max_concurrency: int = _setting(
        2, "AUTH_PASSWORD_HASH_MAX_CONCURRENCY", "Concurrent password hash checks per worker."
    )
    password_hash_queue_timeout_seconds: float = _setting(
        1.0, "AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS", "Password hash admission wait."
    )
    password_hash_timeout_seconds: float = _setting(
        15.0, "AUTH_PASSWORD_HASH_TIMEOUT_SECONDS", "Maximum credential-verifier runtime."
    )
    bff_call_timeout_seconds: float = _setting(
        30.0, "BFF_CALL_TIMEOUT_SECONDS", "Non-streaming BFF timeout."
    )
    bff_max_concurrency: int = _setting(
        32, "BFF_MAX_CONCURRENCY", "Concurrent admitted BFF calls per worker."
    )
    bff_max_queue: int = _setting(
        64, "BFF_MAX_QUEUE", "Maximum queued BFF calls per worker."
    )
    bff_queue_timeout_seconds: float = _setting(
        2.0, "BFF_QUEUE_TIMEOUT_SECONDS", "Maximum BFF admission wait."
    )
    bff_request_ingress_timeout_seconds: float = _setting(
        10.0,
        "BFF_REQUEST_INGRESS_TIMEOUT_SECONDS",
        "Maximum time to upload one BFF request body before execution admission.",
    )
    bff_request_max_bytes: int = _setting(
        1024 * 1024, "BFF_REQUEST_MAX_BYTES", "Maximum canonical BFF JSON body size."
    )
    bff_request_max_depth: int = _setting(
        32, "BFF_REQUEST_MAX_DEPTH", "Maximum canonical BFF JSON nesting depth."
    )
    bff_request_max_items: int = _setting(
        10000, "BFF_REQUEST_MAX_ITEMS", "Maximum aggregate BFF JSON container items."
    )
    bff_result_max_bytes: int = _setting(
        10 * 1024 * 1024,
        "BFF_RESULT_MAX_BYTES",
        "Maximum serialized bytes in one ordinary BFF result.",
    )
    bff_result_max_depth: int = _setting(
        32, "BFF_RESULT_MAX_DEPTH", "Maximum ordinary BFF result nesting depth."
    )
    bff_result_max_items: int = _setting(
        10000, "BFF_RESULT_MAX_ITEMS", "Maximum aggregate ordinary BFF result items."
    )
    bff_execution_mode: str = _setting(
        "trusted-thread",
        "BFF_EXECUTION_MODE",
        "BFF execution mode: trusted-thread or isolated-process.",
    )
    bff_async_execution_mode: str = _setting(
        "event-loop",
        "BFF_ASYNC_EXECUTION_MODE",
        "Trusted async BFF stage mode: event-loop or worker-thread.",
    )
    bff_isolated_max_concurrency: int = _setting(
        4,
        "BFF_ISOLATED_MAX_CONCURRENCY",
        "Concurrent optional isolated BFF child processes per worker.",
    )
    bff_isolated_max_per_user: int = _setting(
        2,
        "BFF_ISOLATED_MAX_PER_USER",
        "Concurrent optional isolated BFF child processes per user.",
    )
    bff_isolated_cpu_seconds: float = _setting(
        30.0,
        "BFF_ISOLATED_CPU_SECONDS",
        "CPU-time limit for one optional isolated BFF child.",
    )
    bff_isolated_memory_bytes: int = _setting(
        1024 * 1024 * 1024,
        "BFF_ISOLATED_MEMORY_BYTES",
        "Address-space limit for one optional isolated BFF child.",
    )
    bff_stream_max_seconds: float = _setting(
        300.0, "BFF_STREAM_MAX_SECONDS", "Maximum BFF stream duration."
    )
    bff_stream_max_bytes: int = _setting(
        10 * 1024 * 1024, "BFF_STREAM_MAX_BYTES", "Maximum BFF stream bytes."
    )
    bff_stream_max_items: int = _setting(
        10000, "BFF_STREAM_MAX_ITEMS", "Maximum BFF stream items."
    )
    bff_stream_idle_timeout_seconds: float = _setting(
        30.0, "BFF_STREAM_IDLE_TIMEOUT_SECONDS", "Maximum wait between stream items."
    )
    bff_stream_write_timeout_seconds: float = _setting(
        30.0,
        "BFF_STREAM_WRITE_TIMEOUT_SECONDS",
        "Maximum blocked write time for each BFF stream frame.",
    )
    appcode_max_files: int = _setting(
        512, "APPCODE_MAX_FILES", "Maximum files in one browser archive."
    )
    appcode_max_file_bytes: int = _setting(
        4 * 1024 * 1024, "APPCODE_MAX_FILE_BYTES", "Maximum source file size in an archive."
    )
    appcode_max_total_bytes: int = _setting(
        32 * 1024 * 1024, "APPCODE_MAX_TOTAL_BYTES", "Maximum aggregate source bytes per archive."
    )
    appcode_cache_entries: int = _setting(
        16, "APPCODE_CACHE_ENTRIES", "Per-worker bounded browser archive cache entries."
    )
    appcode_cache_max_bytes: int = _setting(
        128 * 1024 * 1024,
        "APPCODE_CACHE_MAX_BYTES",
        "Aggregate byte limit for the per-worker browser archive cache.",
    )
    appcode_build_max_concurrency: int = _setting(
        2, "APPCODE_BUILD_MAX_CONCURRENCY", "Concurrent archive builds per worker."
    )
    appcode_build_queue_timeout_seconds: float = _setting(
        1.0, "APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS", "Maximum archive build admission wait."
    )
    dev_wheel_version: str = _setting(
        "99.99.99",
        "PYTINCTURE_DEV_WHEEL_VERSION",
        "Explicit development widget-wheel fallback version.",
    )
    public_widget_wheel_max_bytes: int = _setting(
        64 * 1024 * 1024,
        "PYTINCTURE_WIDGET_WHEEL_MAX_BYTES",
        "Maximum bytes in one backend-served widget wheel.",
    )
    public_widget_wheel_digest_cache_entries: int = _setting(
        32,
        "PYTINCTURE_WIDGET_WHEEL_DIGEST_CACHE_ENTRIES",
        "Per-worker verified widget-wheel digest cache entries.",
    )
    public_widget_wheel_max_concurrency: int = _setting(
        4,
        "PYTINCTURE_WIDGET_WHEEL_MAX_CONCURRENCY",
        "Concurrent backend widget-wheel responses per worker.",
    )
    public_widget_wheel_max_queue: int = _setting(
        8,
        "PYTINCTURE_WIDGET_WHEEL_MAX_QUEUE",
        "Maximum queued backend widget-wheel responses per worker.",
    )
    public_widget_wheel_queue_timeout_seconds: float = _setting(
        1.0,
        "PYTINCTURE_WIDGET_WHEEL_QUEUE_TIMEOUT_SECONDS",
        "Maximum widget-wheel response admission wait.",
    )
    public_widget_wheel_rate_limit_attempts: int = _setting(
        120,
        "PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_ATTEMPTS",
        "Widget-wheel requests allowed per peer/application window and worker.",
    )
    public_widget_wheel_rate_limit_window_seconds: int = _setting(
        60,
        "PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_WINDOW_SECONDS",
        "Widget-wheel request rate-limit window.",
    )
    widget_trust_policy: Optional[str] = _setting(
        None,
        "PYTINCTURE_WIDGET_TRUST_POLICY",
        "Optional deployment-owned widget distribution/version/asset-hash policy JSON or path.",
        repr=False,
    )
    widget_public_index_allowlist: tuple[str, ...] = _setting(
        (),
        "PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST",
        "Exact widget name==version specs allowed to use PyPI after backend wheels.",
    )
    remote_store_timeout_seconds: float = _setting(
        2.0, "REMOTE_STORE_TIMEOUT_SECONDS", "Optional remote-store HTTP deadline."
    )
    remote_store_failure_threshold: int = _setting(
        3, "REMOTE_STORE_FAILURE_THRESHOLD", "Failures before opening the store circuit."
    )
    remote_store_cooldown_seconds: float = _setting(
        15.0, "REMOTE_STORE_COOLDOWN_SECONDS", "Open-circuit cooldown."
    )
    remote_store_max_concurrency: int = _setting(
        8,
        "REMOTE_STORE_MAX_CONCURRENCY",
        "Concurrent optional shared-store operations per worker.",
    )
    remote_store_max_queue: int = _setting(
        16,
        "REMOTE_STORE_MAX_QUEUE",
        "Maximum queued optional shared-store operations per worker.",
    )
    remote_store_queue_timeout_seconds: float = _setting(
        1.0,
        "REMOTE_STORE_QUEUE_TIMEOUT_SECONDS",
        "Maximum optional shared-store admission wait.",
    )
    readiness_cache_ttl_seconds: float = _setting(
        1.0,
        "READINESS_CACHE_TTL_SECONDS",
        "Short per-worker readiness result cache lifetime.",
    )
    enable_bff_replay_tokens: bool = _setting(
        False, "ENABLE_BFF_REPLAY_TOKENS", "Enable one-time BFF request proofs."
    )
    bff_replay_token_batch_size: int = _setting(
        12, "BFF_REPLAY_TOKEN_BATCH_SIZE", "Proofs issued per refill."
    )
    bff_replay_token_low_watermark: int = _setting(
        3, "BFF_REPLAY_TOKEN_LOW_WATERMARK", "Proof-pool refill threshold."
    )
    bff_replay_token_ttl_seconds: int = _setting(
        300, "BFF_REPLAY_TOKEN_TTL_SECONDS", "Unused proof lifetime."
    )
    bff_replay_issue_session_limit: int = _setting(
        30,
        "BFF_REPLAY_ISSUE_SESSION_LIMIT",
        "Replay-proof refill requests allowed per session and window.",
    )
    bff_replay_issue_peer_limit: int = _setting(
        120,
        "BFF_REPLAY_ISSUE_PEER_LIMIT",
        "Replay-proof refill requests allowed per network peer and window.",
    )
    bff_replay_issue_worker_limit: int = _setting(
        1000,
        "BFF_REPLAY_ISSUE_WORKER_LIMIT",
        "Replay-proof refill requests allowed per worker and window.",
    )
    bff_replay_issue_window_seconds: int = _setting(
        60,
        "BFF_REPLAY_ISSUE_WINDOW_SECONDS",
        "Replay-proof refill quota window in seconds.",
    )
    bff_replay_local_max_tokens: int = _setting(
        10000,
        "BFF_REPLAY_LOCAL_MAX_TOKENS",
        "Maximum outstanding replay proofs retained by one worker.",
    )
    bff_replay_local_max_tokens_per_session: int = _setting(
        512,
        "BFF_REPLAY_LOCAL_MAX_TOKENS_PER_SESSION",
        "Maximum outstanding replay proofs retained for one session.",
    )
    bff_replay_require_shared_store: bool = _setting(
        False,
        "BFF_REPLAY_REQUIRE_SHARED_STORE",
        "Require an atomic store shared by every worker for strict single use.",
    )
    use_redis_instance: bool = _setting(False, "USE_REDIS_INSTANCE", "Use Upstash shared state.")
    redis_url: str = _setting(
        "",
        "REDIS_UPSTASH_INSTANCE_URL",
        "Optional Upstash Redis URL; HTTPS is required except for literal "
        "loopback development IPs.",
    )
    redis_token: str = _setting(
        "", "REDIS_UPSTASH_INSTANCE_TOKEN", "Upstash Redis token.", repr=False
    )
    enable_mcp: bool = _setting(False, "ENABLE_MCP", "Enable the MCP mount.")
    mcp_tools: str = _setting("[]", "MCP_TOOLS", "Explicit MCP-to-BFF tool mappings.")
    mcp_allowed_hosts: tuple[str, ...] = _setting(
        (), "MCP_ALLOWED_HOSTS", "Exact Host values accepted by the MCP transport."
    )
    mcp_allowed_origins: tuple[str, ...] = _setting(
        (), "MCP_ALLOWED_ORIGINS", "Exact Origin values accepted by the MCP transport."
    )
    mcp_jwt_jwks_uri: str = _setting("", "MCP_JWT_JWKS_URI", "HTTPS JWT JWKS endpoint.")
    mcp_jwt_public_key: str = field(
        default="", repr=False, metadata={"env": "MCP_JWT_PUBLIC_KEY", "description": "JWT public key."}
    )
    mcp_jwt_issuer: str = _setting("", "MCP_JWT_ISSUER", "Required JWT issuer.")
    mcp_jwt_audience: str = _setting("", "MCP_JWT_AUDIENCE", "Required JWT audience.")
    mcp_jwt_algorithm: str = _setting("", "MCP_JWT_ALGORITHM", "Optional JWT algorithm.")
    trusted_proxy_headers: bool = _setting(
        False, "PYTINCTURE_TRUST_PROXY_HEADERS", "Trust forwarded host/protocol headers."
    )
    allow_development_auth_origin: bool = _setting(
        False,
        "PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN",
        "Allow request-derived authentication origins in loopback-only development.",
    )
    log_level: str = _setting(
        "INFO", "PYTINCTURE_LOG_LEVEL", "Structured application log level."
    )
    environment: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(
            self,
            "environment",
            MappingProxyType(
                {str(key): str(value) for key, value in self.environment.items()}
            ),
        )
        for name in (
            "cors_allowed_origins",
            "browser_connect_origins",
            "allowed_hosts",
            "previous_session_secrets",
            "widget_public_index_allowlist",
            "mcp_allowed_hosts",
            "mcp_allowed_origins",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

        modules_path = str(Path(self.modules_path).expanduser().resolve())
        object.__setattr__(self, "modules_path", modules_path)
        if not Path(modules_path).is_dir():
            raise ValueError(f"modules_path is not a directory: {modules_path}")
        if (
            self.require_readonly_modules_path
            and modules_path_appears_writable(modules_path)
        ):
            raise ValueError(
                "modules_path is writable by the effective service account; "
                "disable require_readonly_modules_path for development or mount "
                "the production application root read-only"
            )

        object.__setattr__(
            self,
            "application_admission",
            canonical_application_admission(self.application_admission),
        )
        object.__setattr__(
            self,
            "browser_connect_origins",
            canonical_browser_connect_origins(self.browser_connect_origins),
        )
        object.__setattr__(
            self,
            "widget_public_index_allowlist",
            canonical_widget_public_index_specs(
                self.widget_public_index_allowlist
            ),
        )

        if self.default_application is not None:
            candidate = str(self.default_application).strip()
            validate_application_name(candidate)
            object.__setattr__(self, "default_application", candidate)

        if self.favicon_folder is not None:
            favicon_path = Path(self.favicon_folder).expanduser()
            if not favicon_path.is_absolute():
                favicon_path = Path(modules_path) / favicon_path
            favicon_path = favicon_path.resolve()
            if not favicon_path.exists():
                raise ValueError(f"favicon_folder does not exist: {favicon_path}")
            object.__setattr__(self, "favicon_folder", str(favicon_path))

        if self.session_max_age_seconds <= 0 or self.session_absolute_max_age_seconds <= 0:
            raise ValueError("session lifetime values must be greater than zero")
        if self.session_absolute_max_age_seconds < self.session_max_age_seconds:
            raise ValueError("absolute session lifetime cannot be shorter than idle lifetime")
        if self.session_same_site.lower() not in {"lax", "strict", "none"}:
            raise ValueError("session_same_site must be lax, strict, or none")
        object.__setattr__(self, "session_same_site", self.session_same_site.lower())
        if self.session_same_site == "none" and self.session_https_only is False:
            raise ValueError("session_same_site='none' requires session_https_only=true")
        if self.max_request_body_bytes <= 0:
            raise ValueError("max_request_body_bytes must be greater than zero")
        if self.saml_transaction_ttl_seconds <= 0:
            raise ValueError("saml_transaction_ttl_seconds must be greater than zero")
        if self.saml_response_max_bytes <= 0:
            raise ValueError("saml_response_max_bytes must be greater than zero")
        if self.enable_saml_auth and self.saml_response_max_bytes > self.max_request_body_bytes:
            raise ValueError("saml_response_max_bytes cannot exceed max_request_body_bytes")
        if min(
            self.saml_acs_rate_limit_attempts,
            self.saml_acs_rate_limit_window_seconds,
        ) <= 0:
            raise ValueError("SAML ACS rate-limit values must be greater than zero")
        positive_limits = (
            self.session_max_claim_count,
            self.session_max_identity_bytes,
            self.session_max_cookie_bytes,
            self.browser_log_max_bytes,
            self.browser_log_rate_limit_attempts,
            self.browser_log_rate_limit_window_seconds,
            self.login_rate_limit_attempts,
            self.login_rate_limit_window_seconds,
            self.login_email_max_chars,
            self.login_password_max_chars,
            self.login_csrf_ttl_seconds,
            self.password_hash_max_concurrency,
            self.password_hash_queue_timeout_seconds,
            self.password_hash_timeout_seconds,
            self.bff_call_timeout_seconds,
            self.bff_max_concurrency,
            self.bff_queue_timeout_seconds,
            self.bff_request_ingress_timeout_seconds,
            self.bff_request_max_bytes,
            self.bff_request_max_depth,
            self.bff_request_max_items,
            self.bff_result_max_bytes,
            self.bff_result_max_depth,
            self.bff_result_max_items,
            self.bff_isolated_max_concurrency,
            self.bff_isolated_max_per_user,
            self.bff_isolated_cpu_seconds,
            self.bff_isolated_memory_bytes,
            self.bff_stream_max_seconds,
            self.bff_stream_max_bytes,
            self.bff_stream_max_items,
            self.bff_stream_idle_timeout_seconds,
            self.bff_stream_write_timeout_seconds,
            self.appcode_max_files,
            self.appcode_max_file_bytes,
            self.appcode_max_total_bytes,
            self.appcode_cache_entries,
            self.appcode_cache_max_bytes,
            self.appcode_build_max_concurrency,
            self.appcode_build_queue_timeout_seconds,
            self.public_widget_wheel_max_bytes,
            self.public_widget_wheel_digest_cache_entries,
            self.public_widget_wheel_max_concurrency,
            self.public_widget_wheel_queue_timeout_seconds,
            self.public_widget_wheel_rate_limit_attempts,
            self.public_widget_wheel_rate_limit_window_seconds,
            self.saml_validation_max_concurrency,
            self.saml_validation_queue_timeout_seconds,
            self.saml_validation_timeout_seconds,
            self.remote_store_timeout_seconds,
            self.remote_store_failure_threshold,
            self.remote_store_cooldown_seconds,
            self.remote_store_max_concurrency,
            self.remote_store_queue_timeout_seconds,
            self.readiness_cache_ttl_seconds,
        )
        floating_limits = (
            self.saml_validation_queue_timeout_seconds,
            self.saml_validation_timeout_seconds,
            self.password_hash_queue_timeout_seconds,
            self.password_hash_timeout_seconds,
            self.bff_call_timeout_seconds,
            self.bff_queue_timeout_seconds,
            self.bff_request_ingress_timeout_seconds,
            self.bff_isolated_cpu_seconds,
            self.bff_stream_max_seconds,
            self.bff_stream_idle_timeout_seconds,
            self.bff_stream_write_timeout_seconds,
            self.appcode_build_queue_timeout_seconds,
            self.public_widget_wheel_queue_timeout_seconds,
            self.remote_store_timeout_seconds,
            self.remote_store_cooldown_seconds,
            self.remote_store_queue_timeout_seconds,
            self.readiness_cache_ttl_seconds,
        )
        if not all(math.isfinite(value) for value in floating_limits):
            raise ValueError("floating-point resource limits must be finite")
        if min(positive_limits) <= 0:
            raise ValueError("resource limits must be greater than zero")
        if self.bff_max_queue < 0:
            raise ValueError("bff_max_queue cannot be negative")
        if self.public_widget_wheel_max_queue < 0:
            raise ValueError("public_widget_wheel_max_queue cannot be negative")
        if self.saml_validation_max_queue < 0:
            raise ValueError("saml_validation_max_queue cannot be negative")
        if self.remote_store_max_queue < 0:
            raise ValueError("remote_store_max_queue cannot be negative")
        docs_mode = self.api_docs_mode.strip().lower()
        if docs_mode not in {"public", "authenticated", "disabled"}:
            raise ValueError(
                "api_docs_mode must be public, authenticated, or disabled"
            )
        object.__setattr__(self, "api_docs_mode", docs_mode)
        execution_mode = self.bff_execution_mode.strip().lower()
        if execution_mode not in {"trusted-thread", "isolated-process"}:
            raise ValueError(
                "bff_execution_mode must be trusted-thread or isolated-process"
            )
        object.__setattr__(self, "bff_execution_mode", execution_mode)
        async_execution_mode = self.bff_async_execution_mode.strip().lower()
        if async_execution_mode not in {"event-loop", "worker-thread"}:
            raise ValueError(
                "bff_async_execution_mode must be event-loop or worker-thread"
            )
        object.__setattr__(
            self, "bff_async_execution_mode", async_execution_mode
        )
        if self.bff_isolated_max_per_user > self.bff_isolated_max_concurrency:
            raise ValueError(
                "bff_isolated_max_per_user cannot exceed isolated concurrency"
            )
        development_widget_version = self.dev_wheel_version.strip()
        try:
            Version(development_widget_version)
        except InvalidVersion as exc:
            raise ValueError(
                "dev_wheel_version must be a valid Python package version"
            ) from exc
        object.__setattr__(self, "dev_wheel_version", development_widget_version)
        try:
            object.__setattr__(
                self,
                "widget_trust_policy",
                canonical_widget_trust_policy(self.widget_trust_policy),
            )
        except ValueError as exc:
            raise ValueError(f"invalid widget_trust_policy: {exc}") from exc
        if not 1 <= self.bff_replay_token_batch_size <= 100:
            raise ValueError("bff_replay_token_batch_size must be between 1 and 100")
        if not 0 <= self.bff_replay_token_low_watermark < self.bff_replay_token_batch_size:
            raise ValueError("bff_replay_token_low_watermark must be below the batch size")
        if not 10 <= self.bff_replay_token_ttl_seconds <= self.session_max_age_seconds:
            raise ValueError("bff_replay_token_ttl_seconds must fit within the session lifetime")
        replay_limits = (
            self.bff_replay_issue_session_limit,
            self.bff_replay_issue_peer_limit,
            self.bff_replay_issue_worker_limit,
            self.bff_replay_issue_window_seconds,
            self.bff_replay_local_max_tokens,
            self.bff_replay_local_max_tokens_per_session,
        )
        if min(replay_limits) <= 0:
            raise ValueError("BFF replay issuance and storage limits must be greater than zero")
        if self.bff_replay_token_batch_size > self.bff_replay_local_max_tokens_per_session:
            raise ValueError("BFF replay batch size cannot exceed the per-session token limit")
        if self.bff_replay_local_max_tokens_per_session > self.bff_replay_local_max_tokens:
            raise ValueError("BFF replay per-session token limit cannot exceed worker capacity")
        if self.bff_replay_require_shared_store and not self.enable_bff_replay_tokens:
            raise ValueError(
                "bff_replay_require_shared_store requires enable_bff_replay_tokens"
            )

        auth_enabled = any(
            (self.enable_user_login, self.enable_google_auth, self.enable_microsoft_auth, self.enable_saml_auth)
        )
        dev_only = self.enable_user_login and self.enable_dev_email_login and not any(
            (self.enable_google_auth, self.enable_microsoft_auth, self.enable_saml_auth)
        )
        if self.enable_dev_email_login:
            if not self.enable_user_login:
                raise ValueError("enable_dev_email_login requires enable_user_login")
            if any((self.enable_google_auth, self.enable_microsoft_auth, self.enable_saml_auth)):
                raise ValueError(
                    "enable_dev_email_login cannot be combined with production authentication providers"
                )
            if self.trusted_proxy_headers:
                raise ValueError("enable_dev_email_login cannot trust proxy headers")
            if self.allowed_hosts and not all(
                _is_literal_loopback_host(host) for host in self.allowed_hosts
            ):
                raise ValueError(
                    "enable_dev_email_login allows only literal loopback allowed_hosts"
                )
            if self.canonical_origin and not _is_literal_loopback_origin(
                self.canonical_origin
            ):
                raise ValueError(
                    "enable_dev_email_login allows only a literal loopback canonical_origin"
                )
        if auth_enabled and not dev_only and (
            len(self.session_secret) < 32 or len(set(self.session_secret)) < 8
        ):
            raise ValueError("production authentication requires a strong session_secret")
        if self.use_redis_instance and not (self.redis_url and self.redis_token):
            raise ValueError("Redis shared state requires redis_url and redis_token")
        if self.use_redis_instance:
            object.__setattr__(self, "redis_url", validate_redis_url(self.redis_url))
        if self.enable_mcp:
            if not self.mcp_allowed_hosts or not self.mcp_allowed_origins:
                raise ValueError("MCP requires exact allowed hosts and origins")
            if any("*" in value for value in (*self.mcp_allowed_hosts, *self.mcp_allowed_origins)):
                raise ValueError("MCP allowed hosts and origins cannot contain wildcards")
            if bool(self.mcp_jwt_jwks_uri) == bool(self.mcp_jwt_public_key):
                raise ValueError("MCP requires exactly one JWT verification source")
            if self.mcp_jwt_jwks_uri and urlparse(self.mcp_jwt_jwks_uri).scheme != "https":
                raise ValueError("mcp_jwt_jwks_uri must use HTTPS")
            if not self.mcp_jwt_issuer or not self.mcp_jwt_audience:
                raise ValueError("MCP requires a JWT issuer and audience")
        if self.enable_google_auth and not (self.google_client_id and self.google_client_secret):
            raise ValueError("Google authentication requires google_client_id and google_client_secret")
        if self.enable_microsoft_auth and not (
            self.microsoft_client_id
            and self.microsoft_client_secret
            and self.microsoft_tenant_id
        ):
            raise ValueError(
                "Microsoft authentication requires client id, client secret, and tenant id"
            )
        if self.microsoft_tenant_id and not _valid_microsoft_tenant_id(
            self.microsoft_tenant_id
        ):
            raise ValueError("microsoft_tenant_id must identify one explicit tenant")
        if self.enable_saml_auth:
            if self.saml_providers:
                try:
                    providers = json.loads(self.saml_providers)
                except json.JSONDecodeError as exc:
                    raise ValueError("saml_providers must contain valid JSON") from exc
                if not isinstance(providers, (dict, list)) or not providers:
                    raise ValueError("saml_providers must be a non-empty JSON object or array")
                provider_entries = providers.values() if isinstance(providers, dict) else providers
                for provider in provider_entries:
                    if not isinstance(provider, dict):
                        raise ValueError("each saml_providers entry must be an object")
                    required_values = (
                        provider.get("idp_entity_id")
                        or provider.get("idpEntityId")
                        or self.saml_idp_entity_id,
                        provider.get("idp_sso_url")
                        or provider.get("idpSsoUrl")
                        or self.saml_idp_sso_url,
                        provider.get("idp_x509_cert")
                        or provider.get("idp_cert")
                        or provider.get("idpX509Cert")
                        or self.saml_idp_x509_cert,
                    )
                    if not all(required_values):
                        raise ValueError(
                            "each SAML provider requires an IdP entity id, SSO URL, and certificate"
                        )
            elif not all(
                (self.saml_idp_entity_id, self.saml_idp_sso_url, self.saml_idp_x509_cert)
            ):
                raise ValueError(
                    "SAML authentication requires saml_providers or the default IdP entity, SSO URL, and certificate"
                )
        if any(
            len(secret) < 32 or len(set(secret)) < 8
            for secret in self.previous_session_secrets
        ):
            raise ValueError("previous_session_secrets must contain strong keys")
        for origin in self.cors_allowed_origins:
            if origin != "*" and urlparse(origin).scheme not in {"http", "https"}:
                raise ValueError(f"invalid CORS origin: {origin}")
        if "*" in self.cors_allowed_origins:
            raise ValueError("cors_allowed_origins cannot use '*' with credentialed requests")
        for host in self.allowed_hosts:
            if (
                not host
                or host == "*"
                or "/" in host
                or "://" in host
                or "*" in host[1:]
                or (host.startswith("*") and host != "*" and not host.startswith("*."))
            ):
                raise ValueError(f"invalid allowed host: {host}")
        if self.canonical_origin is not None:
            origin = self.canonical_origin.strip().rstrip("/")
            parsed_origin = urlparse(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.netloc
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path
                or parsed_origin.params
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("canonical_origin must be an HTTP(S) origin without a path")
            try:
                parsed_origin.port
            except ValueError as exc:
                raise ValueError("canonical_origin contains an invalid port") from exc
            object.__setattr__(self, "canonical_origin", origin)
            canonical_host = parsed_origin.hostname or ""
            if self.allowed_hosts and not any(
                canonical_host == pattern
                or (
                    pattern.startswith("*.")
                    and canonical_host.endswith(pattern[1:])
                )
                for pattern in self.allowed_hosts
            ):
                raise ValueError("canonical_origin host must be included in allowed_hosts")

        if self.allow_development_auth_origin:
            if not auth_enabled:
                raise ValueError(
                    "allow_development_auth_origin requires an enabled authentication provider"
                )
            if self.session_https_only is not False:
                raise ValueError(
                    "allow_development_auth_origin requires session_https_only=false"
                )
            if self.allowed_hosts or self.canonical_origin:
                raise ValueError(
                    "allow_development_auth_origin cannot be combined with production host/origin settings"
                )
            if self.trusted_proxy_headers:
                raise ValueError(
                    "allow_development_auth_origin cannot trust proxy headers"
                )

        production_auth = bool(
            auth_enabled
            and not dev_only
            and not self.allow_development_auth_origin
        )
        if production_auth:
            if not self.allowed_hosts:
                raise ValueError(
                    "production authentication requires exact allowed_hosts"
                )
            if any("*" in host for host in self.allowed_hosts):
                raise ValueError(
                    "production authentication requires exact allowed_hosts without wildcards"
                )
            if not self.canonical_origin:
                raise ValueError(
                    "production authentication requires canonical_origin"
                )
            if urlparse(self.canonical_origin).scheme != "https":
                raise ValueError(
                    "production authentication requires an HTTPS canonical_origin"
                )
            if self.session_https_only is False:
                raise ValueError(
                    "production authentication requires session_https_only=true"
                )
            if self.session_https_only is None:
                object.__setattr__(self, "session_https_only", True)

        if self.trusted_proxy_headers and (
            not self.allowed_hosts or not self.canonical_origin
        ):
            raise ValueError(
                "trusted_proxy_headers requires allowed_hosts and canonical_origin"
            )
        normalized_log_level = self.log_level.strip().upper()
        if normalized_log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        object.__setattr__(self, "log_level", normalized_log_level)

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        **overrides,
    ) -> "PytinctureConfig":
        source = dict(os.environ if environ is None else environ)
        values = {}
        boolean_fields = {
            "require_readonly_modules_path",
            "enable_user_login", "enable_dev_email_login", "enable_google_auth",
            "enable_microsoft_auth", "enable_saml_auth", "enable_bff_replay_tokens",
            "bff_replay_require_shared_store",
            "use_redis_instance", "enable_mcp", "trusted_proxy_headers",
            "allow_development_auth_origin", "enable_browser_logs",
            "allow_noauth_browser_logs", "uvicorn_access_log",
        }
        integer_fields = {
            "session_max_age_seconds", "session_absolute_max_age_seconds",
            "session_max_claim_count", "session_max_identity_bytes",
            "session_max_cookie_bytes",
            "max_request_body_bytes", "browser_log_max_bytes",
            "browser_log_rate_limit_attempts", "browser_log_rate_limit_window_seconds",
            "bff_stream_max_bytes",
            "bff_replay_token_batch_size", "bff_replay_token_low_watermark",
            "bff_replay_token_ttl_seconds", "bff_replay_issue_session_limit",
            "bff_replay_issue_peer_limit", "bff_replay_issue_worker_limit",
            "bff_replay_issue_window_seconds", "bff_replay_local_max_tokens",
            "bff_replay_local_max_tokens_per_session", "saml_response_max_bytes",
            "saml_acs_rate_limit_attempts", "saml_acs_rate_limit_window_seconds",
            "saml_transaction_ttl_seconds", "saml_validation_max_concurrency",
            "saml_validation_max_queue",
            "login_rate_limit_attempts", "login_rate_limit_window_seconds",
            "login_email_max_chars", "login_password_max_chars",
            "login_csrf_ttl_seconds",
            "password_hash_max_concurrency", "bff_max_concurrency", "bff_max_queue",
            "bff_request_max_bytes", "bff_request_max_depth", "bff_request_max_items",
            "bff_result_max_bytes", "bff_result_max_depth", "bff_result_max_items",
            "bff_isolated_max_concurrency", "bff_isolated_max_per_user",
            "bff_isolated_memory_bytes",
            "bff_stream_max_items", "appcode_max_files", "appcode_max_file_bytes",
            "appcode_max_total_bytes", "appcode_cache_entries",
            "appcode_cache_max_bytes",
            "appcode_build_max_concurrency",
            "public_widget_wheel_max_bytes",
            "public_widget_wheel_digest_cache_entries",
            "public_widget_wheel_max_concurrency",
            "public_widget_wheel_max_queue",
            "public_widget_wheel_rate_limit_attempts",
            "public_widget_wheel_rate_limit_window_seconds",
            "remote_store_failure_threshold", "remote_store_max_concurrency",
            "remote_store_max_queue",
        }
        float_fields = {
            "password_hash_queue_timeout_seconds", "bff_call_timeout_seconds",
            "password_hash_timeout_seconds",
            "bff_queue_timeout_seconds", "bff_request_ingress_timeout_seconds",
            "bff_stream_max_seconds",
            "bff_isolated_cpu_seconds",
            "bff_stream_idle_timeout_seconds", "bff_stream_write_timeout_seconds",
            "remote_store_timeout_seconds",
            "remote_store_cooldown_seconds", "remote_store_queue_timeout_seconds",
            "readiness_cache_ttl_seconds", "saml_validation_queue_timeout_seconds",
            "saml_validation_timeout_seconds", "appcode_build_queue_timeout_seconds",
            "public_widget_wheel_queue_timeout_seconds",
        }
        tuple_fields = {
            "cors_allowed_origins", "allowed_hosts", "previous_session_secrets",
            "browser_connect_origins",
            "widget_public_index_allowlist",
            "mcp_allowed_hosts", "mcp_allowed_origins",
        }
        for definition in fields(cls):
            env_name = definition.metadata.get("env")
            if not env_name or env_name not in source:
                continue
            raw = source[env_name]
            if definition.name in boolean_fields:
                values[definition.name] = _bool(raw)
            elif definition.name == "session_https_only":
                values[definition.name] = _bool(raw)
            elif definition.name in integer_fields:
                values[definition.name] = int(raw)
            elif definition.name in float_fields:
                values[definition.name] = float(raw)
            elif definition.name in tuple_fields:
                values[definition.name] = (
                    _json_or_csv(raw)
                    if definition.name in {
                        "previous_session_secrets",
                        "browser_connect_origins",
                        "widget_public_index_allowlist",
                        "mcp_allowed_hosts",
                        "mcp_allowed_origins",
                    }
                    else _csv(raw)
                )
            else:
                values[definition.name] = (
                    None if definition.default is None and raw == "" else raw
                )
        if (
            "saml_transaction_ttl_seconds" not in values
            and source.get("SAML_REQUEST_CACHE_TTL")
        ):
            values["saml_transaction_ttl_seconds"] = int(
                source["SAML_REQUEST_CACHE_TTL"]
            )
        if "session_secret" not in values and source.get("SECRET_KEY"):
            values["session_secret"] = source["SECRET_KEY"]
        values.update(overrides)
        typed_env_names = {
            definition.metadata["env"]
            for definition in fields(cls)
            if "env" in definition.metadata
        }
        values.setdefault(
            "environment",
            {key: value for key, value in source.items() if key not in typed_env_names},
        )
        return cls(**values)

    def to_environ(self) -> dict[str, str]:
        result = {str(key): str(value) for key, value in self.environment.items()}
        for definition in fields(self):
            env_name = definition.metadata.get("env")
            if not env_name:
                continue
            value = getattr(self, definition.name)
            if value is None:
                continue
            if isinstance(value, bool):
                result[env_name] = "true" if value else "false"
            elif isinstance(value, tuple):
                result[env_name] = (
                    ",".join(value)
                    if definition.name in {"cors_allowed_origins", "allowed_hosts"}
                    else json.dumps(list(value))
                )
            else:
                result[env_name] = str(value)
        return result

    @classmethod
    def reference(cls) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (definition.name, definition.metadata["env"], definition.metadata["description"])
            for definition in fields(cls)
            if "env" in definition.metadata
        )

    @classmethod
    def environment_names(cls) -> frozenset[str]:
        """Return the environment variables owned by the typed model."""

        return frozenset(
            definition.metadata["env"]
            for definition in fields(cls)
            if "env" in definition.metadata
        )


_ACTIVE_CONFIG: ContextVar[Optional[PytinctureConfig]] = ContextVar(
    "pytincture_active_config", default=None
)


def get_active_config() -> Optional[PytinctureConfig]:
    return _ACTIVE_CONFIG.get()


def get_runtime_env(name: str, default: Optional[str] = None) -> Optional[str]:
    config = get_active_config()
    if config is not None:
        return config.to_environ().get(name, default)
    return os.getenv(name, default)


@contextmanager
def configuration_context(config: PytinctureConfig) -> Iterator[None]:
    token = _ACTIVE_CONFIG.set(config)
    try:
        yield
    finally:
        _ACTIVE_CONFIG.reset(token)
