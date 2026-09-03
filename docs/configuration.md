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

When authentication is enabled outside explicit development modes,
`allowed_hosts` must contain exact public hostnames and `canonical_origin` must
be the one external HTTPS origin used for OAuth/SAML callbacks. Wildcard hosts
and request-derived production callback origins are rejected. Forwarded headers
are ignored unless `trusted_proxy_headers=True`; enabling proxy trust also
requires both fixed controls and a proxy that replaces client-supplied values.

For a local HTTP authentication test, set
`allow_development_auth_origin=True` and `session_https_only=False`. The
supported launcher then binds only to a literal loopback address and rejects a
routable bind. The application also rejects non-loopback peers if another ASGI
launch path is used. This escape hatch cannot be combined with proxy trust or
the production host/origin settings.

`enable_dev_email_login` has the same fail-closed boundary: the peer and direct
`Host`, plus any browser `Origin` or `Referer`, must use literal loopback IPs.
It cannot be combined with trusted proxy headers, public host/origin controls,
or Google, Microsoft, or SAML authentication. This prevents a reverse proxy on
loopback from laundering a remote request into passwordless development login.

Services that intentionally put applications with different identity rules in
one process can set `application_admission` (or
`AUTH_APPLICATION_ADMISSION`) to a JSON application-to-rules mapping:

```python
config = PytinctureConfig(
    modules_path="./apps",
    application_admission={
        "reports": {
            "providers": ["microsoft"],
            "tenants": ["contoso-tenant-id"],
            "object_ids": ["entra-object-id"],
            "email_domains": ["example.com"],
            "roles": ["reader", "administrator"],
        },
        "admin": {
            "providers": ["microsoft"],
            "roles": ["administrator"],
        },
    },
)
```

Supported rule fields are `providers`, `issuers`, `tenants`, `subjects`,
`object_ids`, `emails`, `email_domains`, and `roles`. A string or list of
strings is accepted for each field. Values in one field are alternatives,
while every configured field must match; a roles rule requires at least one
matching normalized role. Microsoft sessions expose the immutable Entra
object id as `oid`; `object_ids` matches that claim. For sensitive Microsoft
authorization prefer `tenants` plus `object_ids`, or `issuers` plus `subjects`.
Email remains available for display and simple admission, but Microsoft email
can change or be reassigned. A production service logs
`security.microsoft_mutable_email_admission` when its Microsoft admission
relies only on email or domain; the warning does not reject existing settings.
Once any application rule is configured, applications missing from the mapping
fail closed. An empty rule (`"reports": {}`) explicitly admits any globally
verified identity to that application.

Leaving the mapping empty preserves the service-wide identity policy for a
single-trust service. The admission decision is repeated before session
issuance and when the application audience is enforced, uses only signed
identity claims, and requires no Redis or in-process state. Applications with
materially different trust domains should still use separate origins and
processes so a browser/package compromise in one cannot inherit another's
same-origin authority.

Service-page `connect-src` permits only `'self'`, `https://pypi.org`, and
`https://files.pythonhosted.org` by default. Browser applications that call
another API directly can add exact origins with `browser_connect_origins`, for
example `("https://api.example.com", "wss://events.example.com")`. Entries are
origin-only HTTPS/WSS values: credentials, wildcards, paths, queries, fragments,
and ambiguous strings are rejected. This does not add those hosts to
`script-src`.

High-trust deployments can set `widget_trust_policy` to a JSON document or a
path to one. The policy is loaded and canonicalized at startup, then acts as an
exact allowlist for widget distribution, version, executable/style paths, and
SHA-256 values. An unlisted application widget fails closed. The selected
administrator-owned manifest is serialized into the service page and overrides
the wheel's own asset manifest. See
[widgetset packaging](widgetset-packaging.md#deployment-owned-trust-policy) for
the schema. Leaving this setting empty preserves normal pluggable widgetsets.
This is a static deployment control and adds no Redis, process memory, sticky
routing, or server-side session requirement.

Hosted applications install their detected widgetset from a backend wheel
before considering a public package index. Keep the exact wheel artifact beside
the application modules; Pytincture verifies its distribution/version against
the backend-discovered widget declaration, hashes the complete wheel, and gives
the browser only the resulting hash-locked backend URL. The built-in `dhxpyt`
compatibility release retains its Pytincture-owned complete-wheel lock.

Custom service widgetsets fail closed when neither the declared nor configured
development backend wheel exists. A deployment that intentionally uses PyPI
for a custom widget may add its exact normalized spec to
`widget_public_index_allowlist`, for example `("mywidgets==1.2.3",)`. This is an
explicit source-trust decision; a broad or unpinned requirement is rejected.
Standalone HTML owners remain responsible for choosing an exact or
hash-locked source because no Pytincture backend exists in that mode.

Writable module roots remain supported for local development and are the
default. Service startup emits `security.modules_path_writable` when the
effective account appears able to modify `MODULES_PATH`. Production deployments
can set `require_readonly_modules_path=True` to turn that best-effort signal
into a startup failure after mounting application source read-only.

Run production applications with any ASGI server, for example
`uvicorn my_service:app`. The compatibility `launch_service()` API remains
available for existing deployments. Both typed and compatibility paths reject
credentialed wildcard CORS; configure exact origins instead of `*`.

Application names are ASCII Python identifiers (`reports_v2`, not
`reports-v2` or `reports.v2`) and cannot collide with framework route names
such as `healthz`, `frontend`, `classcall`, or `mcp`. This rule applies to the
default application and every application-scoped route.

## Typed reference

The contract test checks every row in this table against the dataclass model.

| Field | Environment variable | Meaning |
| --- | --- | --- |
| `modules_path` | `MODULES_PATH` | Application module root. |
| `require_readonly_modules_path` | `PYTINCTURE_REQUIRE_READONLY_MODULES_PATH` | Fail startup when the effective service account can write the module root. |
| `default_application` | `PYTINCTURE_DEFAULT_APPLICATION` | Optional application for the root redirect. |
| `favicon_folder` | `PYTINCTURE_FAVICON_FOLDER` | Optional favicon file/directory. |
| `cors_allowed_origins` | `CORS_ALLOWED_ORIGINS` | Allowed browser origins. |
| `browser_connect_origins` | `PYTINCTURE_BROWSER_CONNECT_ORIGINS` | Exact additional HTTPS/WSS origins permitted by browser connect-src. |
| `allowed_hosts` | `PYTINCTURE_ALLOWED_HOSTS` | Allowed HTTP Host header names. |
| `canonical_origin` | `PYTINCTURE_CANONICAL_ORIGIN` | Canonical external HTTP(S) origin for authentication callbacks. |
| `enable_user_login` | `ENABLE_USER_LOGIN` | Enable local user login. |
| `enable_dev_email_login` | `ENABLE_DEV_EMAIL_LOGIN` | Enable loopback-only development email login. |
| `enable_google_auth` | `ENABLE_GOOGLE_AUTH` | Enable Google OAuth. |
| `enable_microsoft_auth` | `ENABLE_MICROSOFT_AUTH` | Enable Microsoft OAuth. |
| `enable_saml_auth` | `ENABLE_SAML_AUTH` | Enable SAML authentication. |
| `application_admission` | `AUTH_APPLICATION_ADMISSION` | JSON per-application identity admission rules. |
| `allow_development_auth_origin` | `PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN` | Allow request-derived authentication origins in loopback-only development. |
| `google_client_id` | `GOOGLE_CLIENT_ID` | Google OAuth client id. |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `microsoft_client_id` | `MICROSOFT_CLIENT_ID` | Microsoft OAuth client id. |
| `microsoft_client_secret` | `MICROSOFT_CLIENT_SECRET` | Microsoft OAuth client secret. |
| `microsoft_tenant_id` | `MICROSOFT_TENANT_ID` | Required Microsoft Entra tenant id. |
| `oauth_initiation_rate_limit_attempts` | `OAUTH_INITIATION_RATE_LIMIT_ATTEMPTS` | OAuth login initiations allowed per peer/application/provider window. |
| `oauth_callback_rate_limit_attempts` | `OAUTH_CALLBACK_RATE_LIMIT_ATTEMPTS` | OAuth callbacks allowed per peer/application/provider window. |
| `oauth_rate_limit_window_seconds` | `OAUTH_RATE_LIMIT_WINDOW_SECONDS` | OAuth initiation and callback rate-limit window. |
| `oauth_exchange_max_concurrency` | `OAUTH_EXCHANGE_MAX_CONCURRENCY` | Concurrent OAuth provider exchanges per worker. |
| `oauth_exchange_max_queue` | `OAUTH_EXCHANGE_MAX_QUEUE` | Maximum queued OAuth provider exchanges per worker. |
| `oauth_exchange_queue_timeout_seconds` | `OAUTH_EXCHANGE_QUEUE_TIMEOUT_SECONDS` | Maximum OAuth provider-exchange admission wait. |
| `oauth_exchange_timeout_seconds` | `OAUTH_EXCHANGE_TIMEOUT_SECONDS` | Overall OAuth provider-exchange deadline. |
| `oauth_connect_timeout_seconds` | `OAUTH_CONNECT_TIMEOUT_SECONDS` | OAuth provider connection deadline. |
| `oauth_read_timeout_seconds` | `OAUTH_READ_TIMEOUT_SECONDS` | OAuth provider read-idle deadline. |
| `oauth_write_timeout_seconds` | `OAUTH_WRITE_TIMEOUT_SECONDS` | OAuth provider write-idle deadline. |
| `oauth_pool_timeout_seconds` | `OAUTH_POOL_TIMEOUT_SECONDS` | OAuth provider connection-pool deadline. |
| `saml_providers` | `SAML_PROVIDERS` | JSON object or array of SAML identity providers. |
| `saml_idp_entity_id` | `SAML_IDP_ENTITY_ID` | Default SAML identity-provider entity id. |
| `saml_idp_sso_url` | `SAML_IDP_SSO_URL` | Default SAML identity-provider sign-in URL. |
| `saml_idp_x509_cert` | `SAML_IDP_X509_CERT` | Default SAML identity-provider certificate. |
| `saml_transaction_ttl_seconds` | `SAML_RELAY_STATE_TTL_SECONDS` | Maximum lifetime of a browser-bound one-time SAML transaction. |
| `saml_response_max_bytes` | `SAML_RESPONSE_MAX_BYTES` | Maximum decoded SAML response size before signature processing. |
| `saml_acs_rate_limit_attempts` | `SAML_ACS_RATE_LIMIT_ATTEMPTS` | Maximum SAML ACS attempts per peer in one window. |
| `saml_acs_rate_limit_window_seconds` | `SAML_ACS_RATE_LIMIT_WINDOW_SECONDS` | SAML ACS rate-limit window in seconds. |
| `saml_validation_max_concurrency` | `SAML_VALIDATION_MAX_CONCURRENCY` | Concurrent SAML XML/signature validations per worker. |
| `saml_validation_max_queue` | `SAML_VALIDATION_MAX_QUEUE` | Maximum queued SAML validations per worker. |
| `saml_validation_queue_timeout_seconds` | `SAML_VALIDATION_QUEUE_TIMEOUT_SECONDS` | Maximum SAML validation admission wait. |
| `saml_validation_timeout_seconds` | `SAML_VALIDATION_TIMEOUT_SECONDS` | Maximum wait for one SAML validation stage. |
| `saml_public_rate_limit_attempts` | `SAML_PUBLIC_RATE_LIMIT_ATTEMPTS` | Maximum SAML login and metadata requests per peer in one window. |
| `saml_public_rate_limit_window_seconds` | `SAML_PUBLIC_RATE_LIMIT_WINDOW_SECONDS` | SAML login and metadata rate-limit window in seconds. |
| `saml_public_max_concurrency` | `SAML_PUBLIC_MAX_CONCURRENCY` | Concurrent SAML login and metadata toolkit operations per worker. |
| `saml_public_max_queue` | `SAML_PUBLIC_MAX_QUEUE` | Maximum queued SAML login and metadata toolkit operations per worker. |
| `saml_public_queue_timeout_seconds` | `SAML_PUBLIC_QUEUE_TIMEOUT_SECONDS` | Maximum SAML login and metadata toolkit admission wait. |
| `saml_public_timeout_seconds` | `SAML_PUBLIC_TIMEOUT_SECONDS` | Maximum wait for one SAML login or metadata toolkit operation. |
| `saml_metadata_cache_entries` | `SAML_METADATA_CACHE_ENTRIES` | Bounded per-worker SAML metadata fingerprints retained. |
| `session_secret` | `SAML_SECRET_KEY` | Session signing secret. |
| `previous_session_secrets` | `AUTH_SESSION_PREVIOUS_SECRET_KEYS` | Previous signing keys accepted during rotation. |
| `session_max_age_seconds` | `AUTH_SESSION_MAX_AGE_SECONDS` | Session idle lifetime. |
| `session_absolute_max_age_seconds` | `AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS` | Absolute authenticated session lifetime. |
| `session_https_only` | `AUTH_SESSION_HTTPS_ONLY` | Secure-cookie requirement; derived when omitted. |
| `session_same_site` | `AUTH_SESSION_SAME_SITE` | Cookie SameSite policy. |
| `session_max_claim_count` | `AUTH_SESSION_MAX_CLAIM_COUNT` | Maximum keys retained in an authenticated session identity. |
| `session_max_identity_bytes` | `AUTH_SESSION_MAX_IDENTITY_BYTES` | Maximum canonical JSON bytes retained for an authenticated identity. |
| `session_max_cookie_bytes` | `AUTH_SESSION_MAX_COOKIE_BYTES` | Maximum signed browser-session cookie value bytes. |
| `max_request_body_bytes` | `MAX_REQUEST_BODY_BYTES` | Maximum request body size. |
| `auth_request_ingress_max_concurrency` | `AUTH_REQUEST_INGRESS_MAX_CONCURRENCY` | Concurrent authentication request-body uploads per worker. |
| `auth_request_ingress_max_concurrency_per_peer` | `AUTH_REQUEST_INGRESS_MAX_CONCURRENCY_PER_PEER` | Concurrent authentication request-body uploads per peer. |
| `auth_request_ingress_max_queue` | `AUTH_REQUEST_INGRESS_MAX_QUEUE` | Maximum queued authentication request-body uploads per worker. |
| `auth_request_ingress_queue_timeout_seconds` | `AUTH_REQUEST_INGRESS_QUEUE_TIMEOUT_SECONDS` | Maximum authentication request-body admission wait. |
| `auth_request_ingress_total_timeout_seconds` | `AUTH_REQUEST_INGRESS_TOTAL_TIMEOUT_SECONDS` | Maximum total time to upload an authentication request body. |
| `auth_request_ingress_idle_timeout_seconds` | `AUTH_REQUEST_INGRESS_IDLE_TIMEOUT_SECONDS` | Maximum pause between authentication request-body chunks. |
| `enable_browser_logs` | `ENABLE_BROWSER_LOGS` | Accept bounded browser diagnostics for authenticated services. |
| `allow_noauth_browser_logs` | `ALLOW_NOAUTH_BROWSER_LOGS` | Explicitly expose bounded browser diagnostics in no-auth services. |
| `browser_log_max_bytes` | `BROWSER_LOG_MAX_BYTES` | Maximum browser diagnostic request bytes. |
| `browser_log_rate_limit_attempts` | `BROWSER_LOG_RATE_LIMIT_ATTEMPTS` | Browser diagnostic requests allowed per peer and window. |
| `browser_log_rate_limit_window_seconds` | `BROWSER_LOG_RATE_LIMIT_WINDOW_SECONDS` | Browser diagnostic rate-limit window in seconds. |
| `api_docs_mode` | `PYTINCTURE_API_DOCS_MODE` | API documentation mode: public, authenticated, or disabled. |
| `diagnostic_details_mode` | `PYTINCTURE_DIAGNOSTIC_DETAILS_MODE` | Health/readiness detail mode: public, minimal, or operator. |
| `diagnostic_operator_token` | `PYTINCTURE_DIAGNOSTIC_OPERATOR_TOKEN` | Bearer token for operator-only health/readiness details. |
| `uvicorn_access_log` | `PYTINCTURE_UVICORN_ACCESS_LOG` | Enable sanitized path-only Uvicorn access logs. |
| `login_rate_limit_attempts` | `AUTH_LOGIN_RATE_LIMIT_ATTEMPTS` | Password attempts per peer and window. |
| `login_rate_limit_window_seconds` | `AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | Password rate-limit window. |
| `login_email_max_chars` | `AUTH_LOGIN_EMAIL_MAX_CHARS` | Maximum submitted email length. |
| `login_password_max_chars` | `AUTH_LOGIN_PASSWORD_MAX_CHARS` | Maximum submitted password length. |
| `login_csrf_ttl_seconds` | `AUTH_LOGIN_CSRF_TTL_SECONDS` | Lifetime of a one-time password-login CSRF transaction. |
| `password_hash_max_concurrency` | `AUTH_PASSWORD_HASH_MAX_CONCURRENCY` | Concurrent password hash checks per worker. |
| `password_hash_queue_timeout_seconds` | `AUTH_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS` | Password hash admission wait. |
| `password_hash_timeout_seconds` | `AUTH_PASSWORD_HASH_TIMEOUT_SECONDS` | Maximum credential-verifier runtime. |
| `bff_call_timeout_seconds` | `BFF_CALL_TIMEOUT_SECONDS` | Non-streaming BFF timeout. |
| `bff_max_concurrency` | `BFF_MAX_CONCURRENCY` | Concurrent admitted BFF calls per worker. |
| `bff_max_queue` | `BFF_MAX_QUEUE` | Maximum queued BFF calls per worker. |
| `bff_queue_timeout_seconds` | `BFF_QUEUE_TIMEOUT_SECONDS` | Maximum BFF admission wait. |
| `bff_request_ingress_timeout_seconds` | `BFF_REQUEST_INGRESS_TIMEOUT_SECONDS` | Maximum time to upload one BFF request body before execution admission. |
| `bff_request_ingress_max_concurrency` | `BFF_REQUEST_INGRESS_MAX_CONCURRENCY` | Concurrent BFF request-body uploads per worker. |
| `bff_request_ingress_max_concurrency_per_peer` | `BFF_REQUEST_INGRESS_MAX_CONCURRENCY_PER_PEER` | Concurrent BFF request-body uploads per peer. |
| `bff_request_ingress_max_queue` | `BFF_REQUEST_INGRESS_MAX_QUEUE` | Maximum queued BFF request-body uploads per worker. |
| `bff_request_ingress_queue_timeout_seconds` | `BFF_REQUEST_INGRESS_QUEUE_TIMEOUT_SECONDS` | Maximum BFF request-body upload admission wait. |
| `bff_request_max_bytes` | `BFF_REQUEST_MAX_BYTES` | Maximum canonical BFF JSON body size. |
| `bff_request_max_depth` | `BFF_REQUEST_MAX_DEPTH` | Maximum canonical BFF JSON nesting depth. |
| `bff_request_max_items` | `BFF_REQUEST_MAX_ITEMS` | Maximum aggregate BFF JSON container items. |
| `bff_result_max_bytes` | `BFF_RESULT_MAX_BYTES` | Maximum serialized bytes in one ordinary BFF result. |
| `bff_result_max_depth` | `BFF_RESULT_MAX_DEPTH` | Maximum ordinary BFF result nesting depth. |
| `bff_result_max_items` | `BFF_RESULT_MAX_ITEMS` | Maximum aggregate ordinary BFF result items. |
| `bff_execution_mode` | `BFF_EXECUTION_MODE` | BFF execution mode: trusted-thread or isolated-process. |
| `bff_async_execution_mode` | `BFF_ASYNC_EXECUTION_MODE` | Trusted async BFF stage mode: event-loop or worker-thread. |
| `bff_isolated_max_concurrency` | `BFF_ISOLATED_MAX_CONCURRENCY` | Concurrent optional isolated BFF child processes per worker. |
| `bff_isolated_max_per_user` | `BFF_ISOLATED_MAX_PER_USER` | Concurrent optional isolated BFF child processes per stable authenticated identity. Multiple sessions share this allowance. |
| `bff_isolated_cpu_seconds` | `BFF_ISOLATED_CPU_SECONDS` | CPU-time limit for one optional isolated BFF child. |
| `bff_isolated_memory_bytes` | `BFF_ISOLATED_MEMORY_BYTES` | Address-space limit for one optional isolated BFF child. |
| `bff_stream_max_seconds` | `BFF_STREAM_MAX_SECONDS` | Maximum BFF stream duration. |
| `bff_stream_max_bytes` | `BFF_STREAM_MAX_BYTES` | Maximum BFF stream bytes. |
| `bff_stream_max_items` | `BFF_STREAM_MAX_ITEMS` | Maximum BFF stream items. |
| `bff_stream_idle_timeout_seconds` | `BFF_STREAM_IDLE_TIMEOUT_SECONDS` | Maximum wait between stream items. |
| `bff_stream_write_timeout_seconds` | `BFF_STREAM_WRITE_TIMEOUT_SECONDS` | Maximum blocked write time for each BFF stream frame. |
| `appcode_max_files` | `APPCODE_MAX_FILES` | Maximum files in one browser archive. |
| `appcode_max_file_bytes` | `APPCODE_MAX_FILE_BYTES` | Maximum source file size in an archive. |
| `appcode_max_total_bytes` | `APPCODE_MAX_TOTAL_BYTES` | Maximum aggregate source bytes per archive. |
| `appcode_cache_entries` | `APPCODE_CACHE_ENTRIES` | Per-worker bounded browser archive cache entries. |
| `appcode_cache_max_bytes` | `APPCODE_CACHE_MAX_BYTES` | Aggregate byte limit for the per-worker browser archive cache. |
| `bff_application_graph_cache_entries` | `BFF_APPLICATION_GRAPH_CACHE_ENTRIES` | Per-worker cached application BFF membership graphs. |
| `bff_application_graph_max_directories` | `BFF_APPLICATION_GRAPH_MAX_DIRECTORIES` | Maximum directories examined for one application BFF graph. |
| `bff_application_graph_max_scanned_files` | `BFF_APPLICATION_GRAPH_MAX_SCANNED_FILES` | Maximum files examined by browser-file globs for one BFF graph. |
| `appcode_build_max_concurrency` | `APPCODE_BUILD_MAX_CONCURRENCY` | Concurrent archive builds per worker. |
| `appcode_build_queue_timeout_seconds` | `APPCODE_BUILD_QUEUE_TIMEOUT_SECONDS` | Maximum archive build admission wait. |
| `appcode_download_max_concurrency` | `APPCODE_DOWNLOAD_MAX_CONCURRENCY` | Concurrent appcode responses per worker. |
| `appcode_download_max_concurrency_per_peer` | `APPCODE_DOWNLOAD_MAX_CONCURRENCY_PER_PEER` | Concurrent appcode responses per peer/application and worker. |
| `appcode_download_max_queue` | `APPCODE_DOWNLOAD_MAX_QUEUE` | Maximum queued appcode responses per worker. |
| `appcode_download_queue_timeout_seconds` | `APPCODE_DOWNLOAD_QUEUE_TIMEOUT_SECONDS` | Maximum appcode response admission wait. |
| `appcode_download_max_seconds` | `APPCODE_DOWNLOAD_MAX_SECONDS` | Maximum total duration of one appcode response. |
| `appcode_download_write_timeout_seconds` | `APPCODE_DOWNLOAD_WRITE_TIMEOUT_SECONDS` | Maximum blocked write time for each appcode response frame. |
| `appcode_prebuilt_directory` | `PYTINCTURE_APPCODE_PREBUILT_DIRECTORY` | Optional directory containing deployment-built <application>.pyt archives. |
| `require_prebuilt_appcode` | `PYTINCTURE_REQUIRE_PREBUILT_APPCODE` | Require a deployment-built archive instead of dynamic browser packaging. |
| `public_asset_authorization_cache_entries` | `PYTINCTURE_PUBLIC_ASSET_AUTHORIZATION_CACHE_ENTRIES` | Per-worker public-asset authorization cache entries. |
| `public_asset_max_bytes` | `PYTINCTURE_PUBLIC_ASSET_MAX_BYTES` | Maximum bytes in one directly served public asset. |
| `public_asset_max_concurrency` | `PYTINCTURE_PUBLIC_ASSET_MAX_CONCURRENCY` | Concurrent public-asset responses per worker. |
| `public_asset_max_concurrency_per_peer` | `PYTINCTURE_PUBLIC_ASSET_MAX_CONCURRENCY_PER_PEER` | Concurrent public-asset responses per peer/application and worker. |
| `public_asset_max_queue` | `PYTINCTURE_PUBLIC_ASSET_MAX_QUEUE` | Maximum queued public-asset responses per worker. |
| `public_asset_queue_timeout_seconds` | `PYTINCTURE_PUBLIC_ASSET_QUEUE_TIMEOUT_SECONDS` | Maximum public-asset admission wait. |
| `public_asset_rate_limit_attempts` | `PYTINCTURE_PUBLIC_ASSET_RATE_LIMIT_ATTEMPTS` | Public-asset requests allowed per peer/application window and worker. |
| `public_asset_rate_limit_window_seconds` | `PYTINCTURE_PUBLIC_ASSET_RATE_LIMIT_WINDOW_SECONDS` | Public-asset request rate-limit window. |
| `public_asset_max_seconds` | `PYTINCTURE_PUBLIC_ASSET_MAX_SECONDS` | Maximum total duration of one public-asset response. |
| `public_asset_write_timeout_seconds` | `PYTINCTURE_PUBLIC_ASSET_WRITE_TIMEOUT_SECONDS` | Maximum blocked write time for each public-asset frame. |
| `dev_wheel_version` | `PYTINCTURE_DEV_WHEEL_VERSION` | Explicit development widget-wheel fallback version. |
| `public_widget_wheel_max_bytes` | `PYTINCTURE_WIDGET_WHEEL_MAX_BYTES` | Maximum bytes in one backend-served widget wheel. |
| `public_widget_wheel_digest_cache_entries` | `PYTINCTURE_WIDGET_WHEEL_DIGEST_CACHE_ENTRIES` | Per-worker verified widget-wheel digest cache entries. |
| `public_widget_wheel_max_concurrency` | `PYTINCTURE_WIDGET_WHEEL_MAX_CONCURRENCY` | Concurrent backend widget-wheel responses per worker. |
| `public_widget_wheel_max_queue` | `PYTINCTURE_WIDGET_WHEEL_MAX_QUEUE` | Maximum queued backend widget-wheel responses per worker. |
| `public_widget_wheel_queue_timeout_seconds` | `PYTINCTURE_WIDGET_WHEEL_QUEUE_TIMEOUT_SECONDS` | Maximum widget-wheel response admission wait. |
| `public_widget_wheel_rate_limit_attempts` | `PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_ATTEMPTS` | Widget-wheel requests allowed per peer/application window and worker. |
| `public_widget_wheel_rate_limit_window_seconds` | `PYTINCTURE_WIDGET_WHEEL_RATE_LIMIT_WINDOW_SECONDS` | Widget-wheel request rate-limit window. |
| `widget_trust_policy` | `PYTINCTURE_WIDGET_TRUST_POLICY` | Optional deployment-owned widget distribution/version/asset-hash policy JSON or path. |
| `widget_public_index_allowlist` | `PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST` | Exact widget name==version specs allowed to use PyPI after backend wheels. |
| `remote_store_timeout_seconds` | `REMOTE_STORE_TIMEOUT_SECONDS` | Optional remote-store HTTP deadline. |
| `remote_store_failure_threshold` | `REMOTE_STORE_FAILURE_THRESHOLD` | Failures before opening the store circuit. |
| `remote_store_cooldown_seconds` | `REMOTE_STORE_COOLDOWN_SECONDS` | Open-circuit cooldown. |
| `remote_store_max_concurrency` | `REMOTE_STORE_MAX_CONCURRENCY` | Concurrent optional shared-store operations per worker. |
| `remote_store_max_queue` | `REMOTE_STORE_MAX_QUEUE` | Maximum queued optional shared-store operations per worker. |
| `remote_store_queue_timeout_seconds` | `REMOTE_STORE_QUEUE_TIMEOUT_SECONDS` | Maximum optional shared-store admission wait. |
| `readiness_cache_ttl_seconds` | `READINESS_CACHE_TTL_SECONDS` | Short per-worker readiness result cache lifetime. |
| `enable_bff_replay_tokens` | `ENABLE_BFF_REPLAY_TOKENS` | Enable one-time BFF request proofs. |
| `bff_replay_token_batch_size` | `BFF_REPLAY_TOKEN_BATCH_SIZE` | Proofs issued per refill. |
| `bff_replay_token_low_watermark` | `BFF_REPLAY_TOKEN_LOW_WATERMARK` | Proof-pool refill threshold. |
| `bff_replay_token_ttl_seconds` | `BFF_REPLAY_TOKEN_TTL_SECONDS` | Unused proof lifetime. |
| `bff_replay_issue_session_limit` | `BFF_REPLAY_ISSUE_SESSION_LIMIT` | Replay-proof refill requests allowed per session and window. |
| `bff_replay_issue_peer_limit` | `BFF_REPLAY_ISSUE_PEER_LIMIT` | Replay-proof refill requests allowed per network peer and window. |
| `bff_replay_issue_worker_limit` | `BFF_REPLAY_ISSUE_WORKER_LIMIT` | Replay-proof refill requests allowed per worker and window. |
| `bff_replay_issue_window_seconds` | `BFF_REPLAY_ISSUE_WINDOW_SECONDS` | Replay-proof refill quota window in seconds. |
| `bff_replay_local_max_tokens` | `BFF_REPLAY_LOCAL_MAX_TOKENS` | Maximum outstanding replay proofs retained by one worker. |
| `bff_replay_local_max_tokens_per_session` | `BFF_REPLAY_LOCAL_MAX_TOKENS_PER_SESSION` | Maximum outstanding replay proofs retained for one session. |
| `bff_replay_require_shared_store` | `BFF_REPLAY_REQUIRE_SHARED_STORE` | Require an atomic store shared by every worker for strict single use. |
| `use_redis_instance` | `USE_REDIS_INSTANCE` | Use Upstash shared state. |
| `redis_url` | `REDIS_UPSTASH_INSTANCE_URL` | Optional Upstash Redis URL; HTTPS is required except for literal loopback development IPs. |
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
| `mcp_allow_legacy_timeless_tokens` | `MCP_ALLOW_LEGACY_TIMELESS_TOKENS` | Allow legacy MCP JWTs without exp/iat claims. |
| `mcp_jwt_clock_skew_seconds` | `MCP_JWT_CLOCK_SKEW_SECONDS` | MCP JWT clock-skew allowance. |
| `mcp_jwt_max_token_age_seconds` | `MCP_JWT_MAX_TOKEN_AGE_SECONDS` | Maximum MCP JWT age since iat. |
| `mcp_jwt_max_token_lifetime_seconds` | `MCP_JWT_MAX_TOKEN_LIFETIME_SECONDS` | Maximum MCP JWT exp-to-iat lifetime. |
| `trusted_proxy_headers` | `PYTINCTURE_TRUST_PROXY_HEADERS` | Trust forwarded host/protocol headers. |
| `log_level` | `PYTINCTURE_LOG_LEVEL` | Structured application log level. |

The remote-store gate covers every shared session-revocation read when that
optional feature is enabled. Queue saturation and deadlines fail closed.
Ordinary signed-cookie validation remains local and does not use this gate, a
thread pool, Redis, or another server-side session store.

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
| `BFF_DOCS_PATH` | Route path for generated BFF API documentation. The interactive UI uses exact, hash-locked Swagger assets packaged in the Python wheel; it makes no CDN request. |
| `BFF_DOCS_TITLE` | Title for generated BFF API documentation. |
| `BFF_POLICY_HOOK_PATH` | Dotted sync/async BFF authorization policy callable. |
| `LOGIN_HELP_TEXT` | Escaped plain-text login guidance. |
| `PYTINCTURE_BROWSER_FILES` | JSON list or comma-separated globs added to `appcode.pyt`. |
| `PYTINCTURE_PUBLIC_ASSET_PATHS` | Unauthenticated direct-asset globs. Lists/CSV are service-wide; a JSON application-to-globs mapping provides per-app scope and `*` declares shared files. |
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
| `SAML_SP_PRIVATE_KEY` | PEM service-provider key; encrypted assertions are currently rejected before toolkit processing. |
| `SAML_SP_X509_CERT` | PEM service-provider certificate. |
| `SECRET_KEY` | Legacy fallback for `SAML_SECRET_KEY`; migrate to the typed setting. |

Boolean strings accept `true/false`, `1/0`, `yes/no`, and `on/off` for typed
configuration. Direct legacy backend settings generally use lowercase
`"true"`; use the documented spelling to avoid ambiguity.
