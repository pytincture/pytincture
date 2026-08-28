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

Run that application with any ASGI server, for example
`uvicorn my_service:app`. The compatibility `launch_service()` API remains
available for existing deployments.

## Typed reference

The contract test checks every row in this table against the dataclass model.

| Field | Environment variable | Meaning |
| --- | --- | --- |
| `modules_path` | `MODULES_PATH` | Application module root. |
| `default_application` | `PYTINCTURE_DEFAULT_APPLICATION` | Optional application for the root redirect. |
| `favicon_folder` | `PYTINCTURE_FAVICON_FOLDER` | Optional favicon file/directory. |
| `cors_allowed_origins` | `CORS_ALLOWED_ORIGINS` | Allowed browser origins. |
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
| `session_secret` | `SAML_SECRET_KEY` | Session signing secret. |
| `previous_session_secrets` | `AUTH_SESSION_PREVIOUS_SECRET_KEYS` | Previous signing keys accepted during rotation. |
| `session_max_age_seconds` | `AUTH_SESSION_MAX_AGE_SECONDS` | Signed session lifetime. |
| `session_https_only` | `AUTH_SESSION_HTTPS_ONLY` | Secure-cookie requirement; derived when omitted. |
| `session_same_site` | `AUTH_SESSION_SAME_SITE` | Cookie SameSite policy. |
| `max_request_body_bytes` | `MAX_REQUEST_BODY_BYTES` | Maximum request body size. |
| `bff_call_timeout_seconds` | `BFF_CALL_TIMEOUT_SECONDS` | Non-streaming BFF timeout. |
| `bff_stream_max_seconds` | `BFF_STREAM_MAX_SECONDS` | Maximum BFF stream duration. |
| `bff_stream_max_bytes` | `BFF_STREAM_MAX_BYTES` | Maximum BFF stream bytes. |
| `enable_bff_replay_tokens` | `ENABLE_BFF_REPLAY_TOKENS` | Enable one-time BFF request proofs. |
| `bff_replay_token_batch_size` | `BFF_REPLAY_TOKEN_BATCH_SIZE` | Proofs issued per refill. |
| `bff_replay_token_low_watermark` | `BFF_REPLAY_TOKEN_LOW_WATERMARK` | Proof-pool refill threshold. |
| `bff_replay_token_ttl_seconds` | `BFF_REPLAY_TOKEN_TTL_SECONDS` | Unused proof lifetime. |
| `use_redis_instance` | `USE_REDIS_INSTANCE` | Use Upstash shared state. |
| `redis_url` | `REDIS_UPSTASH_INSTANCE_URL` | Upstash Redis URL. |
| `redis_token` | `REDIS_UPSTASH_INSTANCE_TOKEN` | Upstash Redis token. |
| `enable_mcp` | `ENABLE_MCP` | Enable the MCP mount. |
| `mcp_exposed_operations` | `MCP_EXPOSED_OPERATIONS` | FastAPI operation ids exposed through MCP. |
| `trusted_proxy_headers` | `PYTINCTURE_TRUST_PROXY_HEADERS` | Trust forwarded host/protocol headers. |
| `log_level` | `PYTINCTURE_LOG_LEVEL` | Structured application log level. |
