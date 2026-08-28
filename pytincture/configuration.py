"""Typed, validated configuration for isolated Pytincture applications."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping, Optional
from urllib.parse import urlparse


def _setting(default, env: str, description: str):
    return field(default=default, metadata={"env": env, "description": description})


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"expected a boolean value, received {value!r}")


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
    default_application: Optional[str] = _setting(
        None, "PYTINCTURE_DEFAULT_APPLICATION", "Optional application for the root redirect."
    )
    favicon_folder: Optional[str] = _setting(
        None, "PYTINCTURE_FAVICON_FOLDER", "Optional favicon file/directory."
    )
    cors_allowed_origins: tuple[str, ...] = _setting(
        (), "CORS_ALLOWED_ORIGINS", "Allowed browser origins."
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
    google_client_id: str = _setting("", "GOOGLE_CLIENT_ID", "Google OAuth client id.")
    google_client_secret: str = _setting(
        "", "GOOGLE_CLIENT_SECRET", "Google OAuth client secret."
    )
    microsoft_client_id: str = _setting(
        "", "MICROSOFT_CLIENT_ID", "Microsoft OAuth client id."
    )
    microsoft_client_secret: str = _setting(
        "", "MICROSOFT_CLIENT_SECRET", "Microsoft OAuth client secret."
    )
    saml_providers: str = _setting(
        "", "SAML_PROVIDERS", "JSON object or array of SAML identity providers."
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
    session_secret: str = _setting("", "SAML_SECRET_KEY", "Session signing secret.")
    previous_session_secrets: tuple[str, ...] = _setting(
        (), "AUTH_SESSION_PREVIOUS_SECRET_KEYS", "Previous signing keys accepted during rotation."
    )
    session_max_age_seconds: int = _setting(
        28800, "AUTH_SESSION_MAX_AGE_SECONDS", "Signed session lifetime."
    )
    session_https_only: Optional[bool] = _setting(
        None, "AUTH_SESSION_HTTPS_ONLY", "Secure-cookie requirement; derived when omitted."
    )
    session_same_site: str = _setting("lax", "AUTH_SESSION_SAME_SITE", "Cookie SameSite policy.")
    max_request_body_bytes: int = _setting(
        2 * 1024 * 1024, "MAX_REQUEST_BODY_BYTES", "Maximum request body size."
    )
    bff_call_timeout_seconds: float = _setting(
        30.0, "BFF_CALL_TIMEOUT_SECONDS", "Non-streaming BFF timeout."
    )
    bff_stream_max_seconds: float = _setting(
        300.0, "BFF_STREAM_MAX_SECONDS", "Maximum BFF stream duration."
    )
    bff_stream_max_bytes: int = _setting(
        10 * 1024 * 1024, "BFF_STREAM_MAX_BYTES", "Maximum BFF stream bytes."
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
    use_redis_instance: bool = _setting(False, "USE_REDIS_INSTANCE", "Use Upstash shared state.")
    redis_url: str = _setting("", "REDIS_UPSTASH_INSTANCE_URL", "Upstash Redis URL.")
    redis_token: str = _setting("", "REDIS_UPSTASH_INSTANCE_TOKEN", "Upstash Redis token.")
    enable_mcp: bool = _setting(False, "ENABLE_MCP", "Enable the MCP mount.")
    mcp_exposed_operations: tuple[str, ...] = _setting(
        (), "MCP_EXPOSED_OPERATIONS", "FastAPI operation ids exposed through MCP."
    )
    trusted_proxy_headers: bool = _setting(
        False, "PYTINCTURE_TRUST_PROXY_HEADERS", "Trust forwarded host/protocol headers."
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
            "previous_session_secrets",
            "mcp_exposed_operations",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

        modules_path = str(Path(self.modules_path).expanduser().resolve())
        object.__setattr__(self, "modules_path", modules_path)
        if not Path(modules_path).is_dir():
            raise ValueError(f"modules_path is not a directory: {modules_path}")

        if self.default_application is not None:
            candidate = str(self.default_application).strip().strip("/")
            if not candidate or candidate in {".", ".."} or not all(
                char.isalnum() or char in "._-" for char in candidate
            ):
                raise ValueError("default_application must be a single application name")
            object.__setattr__(self, "default_application", candidate)

        if self.favicon_folder is not None:
            favicon_path = Path(self.favicon_folder).expanduser()
            if not favicon_path.is_absolute():
                favicon_path = Path(modules_path) / favicon_path
            favicon_path = favicon_path.resolve()
            if not favicon_path.exists():
                raise ValueError(f"favicon_folder does not exist: {favicon_path}")
            object.__setattr__(self, "favicon_folder", str(favicon_path))

        if self.session_max_age_seconds <= 0:
            raise ValueError("session_max_age_seconds must be greater than zero")
        if self.session_same_site.lower() not in {"lax", "strict", "none"}:
            raise ValueError("session_same_site must be lax, strict, or none")
        object.__setattr__(self, "session_same_site", self.session_same_site.lower())
        if self.session_same_site == "none" and self.session_https_only is False:
            raise ValueError("session_same_site='none' requires session_https_only=true")
        if self.max_request_body_bytes <= 0:
            raise ValueError("max_request_body_bytes must be greater than zero")
        if min(self.bff_call_timeout_seconds, self.bff_stream_max_seconds, self.bff_stream_max_bytes) <= 0:
            raise ValueError("BFF timeout and stream limits must be greater than zero")
        if not 1 <= self.bff_replay_token_batch_size <= 100:
            raise ValueError("bff_replay_token_batch_size must be between 1 and 100")
        if not 0 <= self.bff_replay_token_low_watermark < self.bff_replay_token_batch_size:
            raise ValueError("bff_replay_token_low_watermark must be below the batch size")
        if not 10 <= self.bff_replay_token_ttl_seconds <= self.session_max_age_seconds:
            raise ValueError("bff_replay_token_ttl_seconds must fit within the session lifetime")

        auth_enabled = any(
            (self.enable_user_login, self.enable_google_auth, self.enable_microsoft_auth, self.enable_saml_auth)
        )
        dev_only = self.enable_user_login and self.enable_dev_email_login and not any(
            (self.enable_google_auth, self.enable_microsoft_auth, self.enable_saml_auth)
        )
        if auth_enabled and not dev_only and (
            len(self.session_secret) < 32 or len(set(self.session_secret)) < 8
        ):
            raise ValueError("production authentication requires a strong session_secret")
        if self.use_redis_instance and not (self.redis_url and self.redis_token):
            raise ValueError("Redis shared state requires redis_url and redis_token")
        if self.use_redis_instance and urlparse(self.redis_url).scheme not in {"http", "https"}:
            raise ValueError("redis_url must be an absolute HTTP(S) URL")
        if self.enable_google_auth and not (self.google_client_id and self.google_client_secret):
            raise ValueError("Google authentication requires google_client_id and google_client_secret")
        if self.enable_microsoft_auth and not (
            self.microsoft_client_id and self.microsoft_client_secret
        ):
            raise ValueError(
                "Microsoft authentication requires microsoft_client_id and microsoft_client_secret"
            )
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
        if any(len(secret) < 32 for secret in self.previous_session_secrets):
            raise ValueError("previous_session_secrets must contain keys of at least 32 characters")
        for origin in self.cors_allowed_origins:
            if origin != "*" and urlparse(origin).scheme not in {"http", "https"}:
                raise ValueError(f"invalid CORS origin: {origin}")
        if "*" in self.cors_allowed_origins:
            raise ValueError("cors_allowed_origins cannot use '*' with credentialed requests")

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        **overrides,
    ) -> "PytinctureConfig":
        source = dict(os.environ if environ is None else environ)
        values = {}
        boolean_fields = {
            "enable_user_login", "enable_dev_email_login", "enable_google_auth",
            "enable_microsoft_auth", "enable_saml_auth", "enable_bff_replay_tokens",
            "use_redis_instance", "enable_mcp", "trusted_proxy_headers",
        }
        integer_fields = {
            "session_max_age_seconds", "max_request_body_bytes", "bff_stream_max_bytes",
            "bff_replay_token_batch_size", "bff_replay_token_low_watermark",
            "bff_replay_token_ttl_seconds",
        }
        float_fields = {"bff_call_timeout_seconds", "bff_stream_max_seconds"}
        tuple_fields = {"cors_allowed_origins", "previous_session_secrets", "mcp_exposed_operations"}
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
                    if definition.name in {"previous_session_secrets", "mcp_exposed_operations"}
                    else _csv(raw)
                )
            else:
                values[definition.name] = (
                    None if definition.default is None and raw == "" else raw
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
                    if definition.name == "cors_allowed_origins"
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
