# Configuration

`PytinctureConfig` validates a service before its ASGI application accepts
traffic. Configuration precedence is:

1. Explicit keyword overrides passed to `PytinctureConfig.from_env()`.
2. Values in the supplied mapping, or `os.environ` when no mapping is supplied.
3. Typed defaults declared by `PytinctureConfig`.

Constructing `PytinctureConfig(...)` directly does not read the process
environment. Unknown environment entries are retained in `environment` so
existing application-specific and provisional Pytincture settings continue to
work. Typed fields always take precedence when converted back to an environment
for the backend.

```python
from pytincture import PytinctureConfig, create_app

config = PytinctureConfig(
    modules_path="./apps",
    cors_allowed_origins=("https://app.example.com",),
    max_request_body_bytes=1_048_576,
)
app = create_app(config)
```

For an internet-facing service, also set `allowed_hosts` to its public
hostname(s) and `canonical_origin` to the external origin used for OAuth/SAML
callbacks. Forwarded headers are ignored unless `trusted_proxy_headers=True`;
enable that only behind a proxy that replaces client-supplied forwarded values.

Run that application with any ASGI server, for example
`uvicorn my_service:app`. The compatibility `launch_service()` API remains
available for existing deployments.

Application names are ASCII Python identifiers (`reports_v2`, not
`reports-v2` or `reports.v2`) and cannot collide with framework route names
such as `healthz`, `frontend`, `classcall`, or `mcp`. This rule applies to the
default application and every application-scoped route.

## Typed reference

The contract test checks every row in this table against the dataclass model.

| Field | Environment variable | Meaning |
| --- | --- | --- |
| `modules_path` | `MODULES_PATH` | Application module root. |
| `default_application` | `PYTINCTURE_DEFAULT_APPLICATION` | Optional application for the root redirect. |
| `favicon_folder` | `PYTINCTURE_FAVICON_FOLDER` | Optional favicon file/directory. |
| `cors_allowed_origins` | `CORS_ALLOWED_ORIGINS` | Allowed browser origins. |
| `allowed_hosts` | `PYTINCTURE_ALLOWED_HOSTS` | Allowed HTTP Host header names. |
| `canonical_origin` | `PYTINCTURE_CANONICAL_ORIGIN` | Canonical external HTTP(S) origin for authentication callbacks. |
| `enable_user_login` | `ENABLE_USER_LOGIN` | Enable local user login. |
| `enable_dev_email_login` | `ENABLE_DEV_EMAIL_LOGIN` | Enable loopback-only development email login. |
| `enable_google_auth` | `ENABLE_GOOGLE_AUTH` | Enable Google OAuth. |
| `enable_microsoft_auth` | `ENABLE_MICROSOFT_AUTH` | Enable Microsoft OAuth. |
| `enable_saml_auth` | `ENABLE_SAML_AUTH` | Enable SAML authentication. |
| `google_client_id` | `GOOGLE_CLIENT_ID` | Google OAuth client id. |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `microsoft_client_id` | `MICROSOFT_CLIENT_ID` | Microsoft OAuth client id. |
| `microsoft_client_secret` | `MICROSOFT_CLIENT_SECRET` | Microsoft OAuth client secret. |
| `saml_providers` | `SAML_PROVIDERS` | JSON object or array of SAML identity providers. |
| `saml_idp_entity_id` | `SAML_IDP_ENTITY_ID` | Default SAML identity-provider entity id. |
| `saml_idp_sso_url` | `SAML_IDP_SSO_URL` | Default SAML identity-provider sign-in URL. |
| `saml_idp_x509_cert` | `SAML_IDP_X509_CERT` | Default SAML identity-provider certificate. |
| `saml_transaction_ttl_seconds` | `SAML_RELAY_STATE_TTL_SECONDS` | Maximum lifetime of a browser-bound one-time SAML transaction. |
| `saml_response_max_bytes` | `SAML_RESPONSE_MAX_BYTES` | Maximum decoded SAML response size before signature processing. |
| `saml_acs_rate_limit_attempts` | `SAML_ACS_RATE_LIMIT_ATTEMPTS` | Maximum SAML ACS attempts per peer in one window. |
| `saml_acs_rate_limit_window_seconds` | `SAML_ACS_RATE_LIMIT_WINDOW_SECONDS` | SAML ACS rate-limit window in seconds. |
| `session_secret` | `SAML_SECRET_KEY` | Session signing secret. |
| `previous_session_secrets` | `AUTH_SESSION_PREVIOUS_SECRET_KEYS` | Previous signing keys accepted during rotation. |
| `session_max_age_seconds` | `AUTH_SESSION_MAX_AGE_SECONDS` | Signed session lifetime. |
| `session_https_only` | `AUTH_SESSION_HTTPS_ONLY` | Secure-cookie requirement; derived when omitted. |
| `session_same_site` | `AUTH_SESSION_SAME_SITE` | Cookie SameSite policy. |
| `max_request_body_bytes` | `MAX_REQUEST_BODY_BYTES` | Maximum request body size. |
| `login_rate_limit_attempts` | `AUTH_LOGIN_RATE_LIMIT_ATTEMPTS` | Password attempts per peer and window. |
| `login_rate_limit_window_seconds` | `AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | Password rate-limit window. |
| `login_email_max_chars` | `AUTH_LOGIN_EMAIL_MAX_CHARS` | Maximum submitted email length. |
| `login_password_max_chars` | `AUTH_LOGIN_PASSWORD_MAX_CHARS` | Maximum submitted password length. |
| `password_hash_max_concurrency` | `AUTH_PASSWORD_HASH_MAX_CONCURRENCY` | Concurrent password hash checks per worker. |
| `password_hash_queue_timeout_seconds` | `AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS` | Password hash admission wait. |
| `password_hash_timeout_seconds` | `AUTH_PASSWORD_HASH_TIMEOUT_SECONDS` | Maximum credential-verifier runtime. |
| `bff_call_timeout_seconds` | `BFF_CALL_TIMEOUT_SECONDS` | Non-streaming BFF timeout. |
| `bff_max_concurrency` | `BFF_MAX_CONCURRENCY` | Concurrent admitted BFF calls per worker. |
| `bff_max_queue` | `BFF_MAX_QUEUE` | Maximum queued BFF calls per worker. |
| `bff_queue_timeout_seconds` | `BFF_QUEUE_TIMEOUT_SECONDS` | Maximum BFF admission wait. |
| `bff_stream_max_seconds` | `BFF_STREAM_MAX_SECONDS` | Maximum BFF stream duration. |
| `bff_stream_max_bytes` | `BFF_STREAM_MAX_BYTES` | Maximum BFF stream bytes. |
| `bff_stream_max_items` | `BFF_STREAM_MAX_ITEMS` | Maximum BFF stream items. |
| `bff_stream_idle_timeout_seconds` | `BFF_STREAM_IDLE_TIMEOUT_SECONDS` | Maximum wait between stream items. |
| `appcode_max_files` | `APPCODE_MAX_FILES` | Maximum files in one browser archive. |
| `appcode_max_file_bytes` | `APPCODE_MAX_FILE_BYTES` | Maximum source file size in an archive. |
| `appcode_max_total_bytes` | `APPCODE_MAX_TOTAL_BYTES` | Maximum aggregate source bytes per archive. |
| `appcode_cache_entries` | `APPCODE_CACHE_ENTRIES` | Per-worker bounded browser archive cache entries. |
| `appcode_build_max_concurrency` | `APPCODE_BUILD_MAX_CONCURRENCY` | Concurrent archive builds per worker. |
| `appcode_build_queue_timeout_seconds` | `APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS` | Maximum archive build admission wait. |
| `remote_store_timeout_seconds` | `REMOTE_STORE_TIMEOUT_SECONDS` | Optional remote-store HTTP deadline. |
| `remote_store_failure_threshold` | `REMOTE_STORE_FAILURE_THRESHOLD` | Failures before opening the store circuit. |
| `remote_store_cooldown_seconds` | `REMOTE_STORE_COOLDOWN_SECONDS` | Open-circuit cooldown. |
| `enable_bff_replay_tokens` | `ENABLE_BFF_REPLAY_TOKENS` | Enable one-time BFF request proofs. |
| `bff_replay_token_batch_size` | `BFF_REPLAY_TOKEN_BATCH_SIZE` | Proofs issued per refill. |
| `bff_replay_token_low_watermark` | `BFF_REPLAY_TOKEN_LOW_WATERMARK` | Proof-pool refill threshold. |
| `bff_replay_token_ttl_seconds` | `BFF_REPLAY_TOKEN_TTL_SECONDS` | Unused proof lifetime. |
| `use_redis_instance` | `USE_REDIS_INSTANCE` | Use Upstash shared state. |
| `redis_url` | `REDIS_UPSTASH_INSTANCE_URL` | Upstash Redis URL. |
| `redis_token` | `REDIS_UPSTASH_INSTANCE_TOKEN` | Upstash Redis token. |
| `enable_mcp` | `ENABLE_MCP` | Enable the MCP mount. |
| `mcp_tools` | `MCP_TOOLS` | Explicit MCP-to-BFF tool mappings. |
| `mcp_allowed_hosts` | `MCP_ALLOWED_HOSTS` | Exact Host values accepted by the MCP transport. |
| `mcp_allowed_origins` | `MCP_ALLOWED_ORIGINS` | Exact Origin values accepted by the MCP transport. |
| `mcp_jwt_jwks_uri` | `MCP_JWT_JWKS_URI` | HTTPS JWT JWKS endpoint. |
| `mcp_jwt_public_key` | `MCP_JWT_PUBLIC_KEY` | JWT public key. |
| `mcp_jwt_issuer` | `MCP_JWT_ISSUER` | Required JWT issuer. |
| `mcp_jwt_audience` | `MCP_JWT_AUDIENCE` | Required JWT audience. |
| `mcp_jwt_algorithm` | `MCP_JWT_ALGORITHM` | Optional JWT algorithm. |
| `trusted_proxy_headers` | `PYTINCTURE_TRUST_PROXY_HEADERS` | Trust forwarded host/protocol headers. |
| `log_level` | `PYTINCTURE_LOG_LEVEL` | Structured application log level. |

## Pass-through and compatibility settings

These settings remain supported by the backend and can be supplied through
`PytinctureConfig.environment`. They are not yet typed fields. Secrets should
come from the deployment secret manager rather than committed files.

| Environment variable | Meaning |
| --- | --- |
| `ALLOWED_EMAILS` | Optional comma-separated authorization allowlist applied after identity verification. |
| `ALLOWED_NOAUTH_CLASSCALLS` | Legacy JSON allowlist for unauthenticated BFF calls. Each entry must identify an exact `application`, module-relative `file`, `class`, and `function`; avoid in new deployments. |
| `AUTH_PASSWORD_HASHES` | JSON email-to-Argon2id/bcrypt map for local login. |
| `AUTH_SESSION_CLAIM_KEYS` | Additional small trusted user claims retained in signed sessions. |
| `AUTH_USER_AUTHENTICATOR` | Dotted sync/async local credential verifier. |
| `AUTH_USER_CLAIMS` | Verified local-user profile claims. |
| `DEFAULT_APP_USERS` | Compatibility fallback profile source after password verification; prefer `AUTH_USER_CLAIMS`. |
| `BFF_DOCS_PATH` | Route path for generated BFF API documentation. |
| `BFF_DOCS_TITLE` | Title for generated BFF API documentation. |
| `BFF_POLICY_HOOK_PATH` | Dotted sync/async BFF authorization policy callable. |
| `LOGIN_HELP_TEXT` | Escaped plain-text login guidance. |
| `PYTINCTURE_BROWSER_FILES` | JSON list or comma-separated globs added to `appcode.pyt`. |
| `PYTINCTURE_PUBLIC_ASSET_PATHS` | Globs extending directly served public application assets. |
| `SAML_ALLOWED_ROLES` | Optional comma-separated SAML role allowlist. |
| `SAML_DEBUG` | Enable OneLogin SAML diagnostic mode; do not expose assertion data in production logs. |
| `SAML_DEFAULT_REDIRECT` | Safe post-login path/template. |
| `SAML_EMAIL_ATTRIBUTE` | Preferred assertion attribute for email. |
| `SAML_IDP_SLO_URL` | Optional identity-provider logout URL. |
| `SAML_LOGIN_LABEL` | Single-provider login button label. |
| `SAML_LOGO_URL` | Single-provider login button image URL. |
| `SAML_NAME_ATTRIBUTE` | Optional assertion attribute for display name. |
| `SAML_REQUESTED_AUTHN_CONTEXT` | Add RequestedAuthnContext; default false. |
| `SAML_REQUEST_CACHE_TTL` | Legacy fallback for the SAML transaction lifetime. |
| `SAML_ROLE_ATTRIBUTE_KEYS` | Candidate assertion attributes containing roles. |
| `SAML_SP_ASSERTION_CONSUMER_SERVICE_URL` | Service-provider ACS URL/template. |
| `SAML_SP_ENTITY_ID` | Service-provider entity ID/template. |
| `SAML_SP_PRIVATE_KEY` | PEM service-provider signing/decryption key. |
| `SAML_SP_X509_CERT` | PEM service-provider certificate. |
| `SECRET_KEY` | Legacy fallback for `SAML_SECRET_KEY`; migrate to the typed setting. |

Boolean strings accept `true/false`, `1/0`, `yes/no`, and `on/off` for typed
configuration. Direct legacy backend settings generally use lowercase
`"true"`; use the documented spelling to avoid ambiguity.
