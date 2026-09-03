# Production deployment and rollback

This runbook defines the supported Pytincture 1.0 deployment topology. Run the
service at the origin root behind a TLS-terminating reverse proxy. A URL path
prefix is not supported because application, BFF, frontend, and service-worker
routes intentionally share the root scope.

The security behaviors Pytincture intentionally retains, including stateless
browser-carried sessions and optional Redis, are recorded in
[`security/accepted-architecture.md`](../security/accepted-architecture.md).

## Required configuration

- Set a stable `SAML_SECRET_KEY` of at least 32 random characters on every
  worker and replica.
- Set `AUTH_SESSION_HTTPS_ONLY=true` and leave `AUTH_SESSION_SAME_SITE=lax`
  unless the identity-provider flow requires another documented value.
- Authentication-enabled production services must set
  `PYTINCTURE_ALLOWED_HOSTS` to exact comma-separated public hostnames and
  `PYTINCTURE_CANONICAL_ORIGIN` to one external HTTPS origin, such as
  `https://service.example.com`. Wildcards, paths, and request-derived origins
  fail startup.
- Set `PYTINCTURE_TRUST_PROXY_HEADERS=true` only when the service is reachable
  exclusively through a trusted proxy that replaces forwarded headers. Proxy
  trust fails startup unless both fixed host/origin controls are configured.
- Never set `PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN` in production. It is a
  loopback-only local HTTP compatibility mode.
- Set explicit `CORS_ALLOWED_ORIGINS`; wildcard credentialed CORS is rejected
  by typed configuration and the compatibility application at startup.
- Never place `ENABLE_DEV_EMAIL_LOGIN=true` behind a reverse proxy. That mode
  rejects proxy trust and public configuration, and requires the peer, direct
  Host, and any browser Origin/Referer to use literal loopback IP addresses.
- SAML responses are decoded and parsed under `SAML_RESPONSE_MAX_BYTES`, use
  XML-signature transform and SHA-256-or-stronger algorithm allowlists, and are
  throttled per peer before signature processing. Response-signed IdPs are
  accepted; assertion-only IdPs must sign the exact AuthnRequest correlation
  inside `SubjectConfirmationData`. Tune `SAML_ACS_RATE_LIMIT_ATTEMPTS` and
  `SAML_ACS_RATE_LIMIT_WINDOW_SECONDS` alongside an edge rate limit; do not
  disable or bypass the built-in response guard.
- Keep the bounded authentication-upload admission enabled. Its total and idle
  deadlines cover request upload only, not password verification, IdP work, or
  assertion validation time.
- Keep BFF upload admission bounded independently from BFF execution. The
  process-local `BFF_REQUEST_INGRESS_*` limits cap bodies buffered per worker
  and peer without reducing cooperative async execution or stream capacity.
- SAML XML/signature validation is offloaded under a per-worker concurrency
  gate. Tune `SAML_VALIDATION_MAX_CONCURRENCY`, `SAML_VALIDATION_MAX_QUEUE`,
  and the queue/runtime deadlines for the worker's CPU budget. A timed-out
  validation retains its slot until the underlying thread exits.
- SAML logins use an HttpOnly browser-binding cookie and a one-time transaction
  with exact `InResponseTo`, response-ID, and assertion-ID correlation. Keep
  `AUTH_SESSION_HTTPS_ONLY=true` so cross-site POST binding uses a Secure,
  `SameSite=None`, application-specific `__Host-` handshake cookie.
- Pin the Pytincture and widgetset versions in the deployment artifact.
- Redis is optional. Load-balanced sessions and SAML handshakes are carried in
  signed browser cookies and work across workers without server-side state.
- Choose `PYTINCTURE_API_DOCS_MODE=authenticated` or `disabled` when operation
  metadata must not be public. The default remains `public` for compatibility;
  all documentation/schema responses are private and non-cacheable.
- Browser diagnostics are accepted for authenticated services by default and
  carry only the exact bounded `{level,message,timestamp}` schema. Set
  `ENABLE_BROWSER_LOGS=false` to disable them. No-auth services do not expose
  `/logs` unless `ALLOW_NOAUTH_BROWSER_LOGS=true` is explicitly selected.
- Service CSP permits browser connections only to self and the exact PyPI
  package origins by default. Configure intentional browser API/WebSocket
  access with `PYTINCTURE_BROWSER_CONNECT_ORIGINS` as a JSON list of exact
  HTTPS/WSS origins. PyPI is never added to `script-src`; external
  Pyodide/icon resources still require SRI, and executable widget assets still
  require their SHA-256 manifest locks.
- MCP bearer tokens must carry finite `iat` and `exp` claims. Pytincture also
  enforces `nbf`, a clock-skew allowance, maximum age, and maximum declared
  lifetime. The defaults allow bounded tokens up to 24 hours; reduce the
  `MCP_JWT_*` limits to match the issuer. Use
  `MCP_ALLOW_LEGACY_TIMELESS_TOKENS=true` only as a temporary migration setting
  for an existing issuer, never as the production target.

Signed session and SAML handshake cookies can be read by any worker sharing the
signing secret. Redis is only an optional enhancement for immediate
cross-worker logout revocation and configured one-time BFF replay tokens; it is
not required to run Pytincture or load balance normal authenticated traffic.
Replay proofs are disabled by default. If enabled without strict fleet-wide
semantics, each worker uses a bounded, expiring local proof store and refill
quotas; a browser may refill after landing on a different worker. If strict
single consumption across workers is explicitly required, set
`BFF_REPLAY_REQUIRE_SHARED_STORE=true` and install any provider implementing
the public `AtomicReplayStore` contract. The optional Redis adapter is one such
provider, not a framework requirement.

Production authentication cookies use the browser-enforced `__Host-` prefix,
`Secure`, `Path=/`, and no `Domain` attribute. Upgrading from an older cookie
name deliberately requires users to sign in once; local HTTP development uses
separate `pytincture-dev-*` names and remains supported.

For Microsoft admission, prefer immutable tenant plus Entra object id
(`object_ids` matches the signed-session `oid`) or issuer plus subject. Email
and domain policies remain supported, but startup logs
`security.microsoft_mutable_email_admission` when they are the only Microsoft
identity constraint. The warning is advisory and does not break existing apps.

Resource admission counters, the appcode cache, and the application BFF graph
cache are bounded, disposable, and local to each worker. The archive LRU is
independently capped by `APPCODE_CACHE_ENTRIES` and
`APPCODE_CACHE_MAX_BYTES`; graph snapshots are capped by
`BFF_APPLICATION_GRAPH_CACHE_ENTRIES` and revalidate secure source/directory
metadata before every reuse. Cold graph discovery is bounded by the appcode
file/byte settings plus `BFF_APPLICATION_GRAPH_MAX_DIRECTORIES` and
`BFF_APPLICATION_GRAPH_MAX_SCANNED_FILES`. They do not contain
durable login/session state and do not require sticky routing. Size concurrency
settings per worker, then apply a
gateway-wide request-rate and connection limit across the deployment. Local
password login has independent peer/account limits per worker, so the gateway
should provide the coordinated outer limit when multiple workers are exposed.
Appcode responses additionally hold configurable per-peer and per-worker
download admission through completion or disconnect, with total and
blocked-write deadlines. For immutable production releases, build
`<application>.pyt` files with `pytincture-build-appcode`, point
`PYTINCTURE_APPCODE_PREBUILT_DIRECTORY` at that read-only artifact directory,
and optionally enable `PYTINCTURE_REQUIRE_PREBUILT_APPCODE`. Backend source
under `MODULES_PATH` remains installed and authoritative; development keeps
dynamic packaging by default. Required prebuilt archives cannot be combined
with session-specific BFF replay clients. Keep the generated `.pyt` and
`.pyt.json` together: Pytincture verifies the archive hash, exact source
manifest, browser-file declaration, and transformer version before serving it.
Optional Upstash operations use short deadlines and a per-worker circuit
breaker; Redis remains optional and is not used by the default signed-cookie
session path. Every explicitly enabled shared session-revocation read—whether
for a page, appcode, BFF, state, logout, logs, or API documentation
request—runs off the event loop under the bounded remote-store gate. Saturation
or timeout fails closed without filling the shared worker pool. Async readiness
and replay issuance use the same bounded remote-store capacity. Readiness
refreshes are coalesced and cached only for `READINESS_CACHE_TTL_SECONDS`.
Redis reads are uncached by default; an explicit read cache is positive-only,
bounded by entries and TTL, and invalidated by local writes/deletes. When that
integration is explicitly enabled, its remote URL must use HTTPS. Cleartext
HTTP is accepted only for local emulators addressed by a literal loopback IP
such as `127.0.0.1` or `::1`.

BFF modules are deployment-trusted code. The default `trusted-thread` mode
preserves low-overhead async, object, and streaming behavior; timed-out worker
threads retain their admission slot until they actually exit. If trusted async
methods or async policy hooks might perform blocking work without yielding, set
`BFF_ASYNC_EXECUTION_MODE=worker-thread` to move those non-streaming stages onto
bounded per-call worker event loops. This keeps the ASGI request loop responsive
but cannot kill a stuck Python thread. The default `event-loop` mode and all
explicit async streaming remain unchanged. Keep the default for application
objects deliberately bound to the ASGI loop, or construct those objects inside
the worker-thread call. For a harder
boundary around non-streaming BFFs, select `BFF_EXECUTION_MODE=isolated-process`
and size its process/per-identity, CPU, memory, wall-time, and result limits.
Multiple sessions for the same signed identity share the configured process
allowance; this does not rate-limit request volume or ordinary async BFF calls.
Excess isolated-process calls fail fast with `Retry-After` rather than holding a
worker thread; the ordinary bounded BFF admission queue still applies.
Child processes are terminated at wall time. CPU enforcement requires POSIX
and the address-space limit is enforced on Linux; use container/cgroup limits
as the fleet boundary on other platforms. Isolated mode does not support
streaming BFF methods.

Keep `MODULES_PATH` writable only by the deployment principal. Pytincture
canonicalizes the root, rejects symlink components and cross-platform traversal
syntax, uses no-follow opens where supported, and verifies BFF source digests
between discovery and execution. These checks are defense in depth, not a
replacement for read-only application artifacts and least-privilege filesystem
permissions. At service startup, Pytincture emits the structured warning event
`security.modules_path_writable` when the effective service account appears able
to write the root. Set `PYTINCTURE_REQUIRE_READONLY_MODULES_PATH=true` to fail
closed instead. The check uses mount flags and effective-access information as
best-effort evidence; the deployment should still use a read-only root
filesystem or module mount and a non-root service user.

BFF registry discovery is fail-closed per source file. An unreadable, unsafe,
malformed, or invalidly encoded Python file contributes no callable operations,
while valid files in unrelated application graphs remain available. Rejections
are exposed in the per-app backend's `BFF_REGISTRY_FAILURES` snapshot and logged
only as bounded relative paths plus stable reason codes. A valid policy-bearing
export still fails startup when its policy hook is missing, and an explicit MCP
tool still fails MCP initialization when its exact BFF target is unavailable.
Use immutable release directories and atomic deployment anyway: an application
that imports a partially written file can still fail its own browser package.

## Private-network exposure

State-changing BFF routes require JSON and reject browser requests unless
Origin, plus Fetch Metadata when present, proves the exact service origin.
Pytincture does not
grant Private Network Access preflights, including for explicitly no-auth BFFs.
This is application-layer defense in depth: also bind development services to
loopback, firewall private deployments from untrusted networks, publish only
through the configured proxy/host, and do not add broad CORS or proxy rules for
BFF paths. Trusted server-to-server clients may omit both browser headers; they
must still use `application/json` and any configured authentication/policy.

## Reverse proxy

Example nginx location for a dedicated host:

```nginx
location / {
    proxy_pass http://127.0.0.1:8070;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Request-ID $request_id;
    proxy_buffering off;
    proxy_read_timeout 310s;
}
```

`X-Forwarded-For` deliberately uses `$remote_addr`, not
`$proxy_add_x_forwarded_for` or `$http_x_forwarded_for`: the public edge must
replace any caller-supplied chain. `proxy_buffering off` is required for incremental BFF streaming. Set the proxy
timeout slightly above `BFF_STREAM_MAX_SECONDS`. Do not forward arbitrary
client-supplied `X-Forwarded-*` values. Pytincture accepts a caller request ID
only when it contains 1–128 letters, digits, `.`, `_`, `:`, or `-`.

## Health and monitoring

- `GET /healthz` is the liveness probe. HTTP 200 means the worker event loop
  can answer; it does not check dependencies.
- `GET /readyz` is the readiness probe. HTTP 200 means the modules directory,
  frontend entrypoint/runtime, and configured Redis stores are available.
  HTTP 503 means the worker must not receive traffic. Concurrent probes share
  one off-loop refresh and reuse it only for the short configured cache TTL.
- Do not place either endpoint behind application authentication.

The public status and HTTP code remain available in every mode. Set
`PYTINCTURE_DIAGNOSTIC_DETAILS_MODE=minimal` to omit the framework version and
named readiness checks, or set it to `operator` with a strong
`PYTINCTURE_DIAGNOSTIC_OPERATOR_TOKEN` and send that value as a Bearer token
only from operator probes. The default `public` mode preserves development and
existing deployment behavior. These controls are stateless and need neither
Redis nor sticky routing. Independently set `PYTINCTURE_API_DOCS_MODE` to
`authenticated` or `disabled` in production.

Pytincture emits one-line JSON events on the `pytincture.security` logger.
Collect `request.complete`, `bff.start`, and `bff.stream.finish`; index at least
`correlation_id`, `path`, `status_code`, `duration_ms`, and streaming `reason`.
Alert on readiness failures, elevated 5xx/401/403 rates, BFF timeouts, stream
`byte-limit`/`timeout` frequency, and p95 latency budget regressions. Control
verbosity with `PYTINCTURE_LOG_LEVEL`.

Raw Uvicorn access logging is disabled by default because callback query
strings can contain OAuth or SAML material. If an operator explicitly sets
`PYTINCTURE_UVICORN_ACCESS_LOG=true`, Pytincture installs a path-only filter
that removes the complete query string before the access record is formatted.
Authenticated BFF, login, callback, session-state, appcode, browser-log, MCP,
and API-documentation responses carry `Cache-Control: private, no-store`,
`Pragma: no-cache`, and `Vary: Cookie, Authorization`.

HSTS remains the responsibility of the TLS-terminating edge, where the public
domain, subdomain, and preload policies are known. Before final promotion,
record durable `production_edge_reviews` evidence proving the live HTTPS
redirect, HSTS policy, canonical origin, and trusted proxy-header replacement.

Use `scripts/audit_production_edge.py` against the real public hostname. The
audit verifies the TLS certificate with the system trust store, requires the
HTTP health URL to redirect exactly to the canonical HTTPS health URL, enforces
the configured minimum HSTS lifetime, and probes an endpoint that emits an
absolute canonical URL both normally and with hostile `Forwarded` and
`X-Forwarded-*` inputs. It also hashes and checks the deployed nginx
configuration structurally. It selects the exact HTTPS `server_name`, port,
and location that handles the canonical probe rather than accepting matching
text from an unrelated block. That target must replace `Host`,
`X-Forwarded-Host`, `X-Forwarded-Proto`, and `X-Forwarded-For`; caller-supplied
forwarded values must not be passed through. A second request sends a forged
client address to `/_pytincture/edge-client` and requires the application to
observe a different peer. The probe returns only that request's bounded peer
value, is never cached, and is not an authentication or authorization input.

The canonical probe can be public SAML metadata or an OAuth initiation route.
It must return a body or redirect whose decoded content contains the exact
canonical HTTPS origin. For example:

```bash
python scripts/audit_production_edge.py \
  --https-origin https://service.example.com \
  --http-origin http://service.example.com \
  --canonical-probe-path /myapp/auth/saml/metadata \
  --proxy-config /etc/nginx/conf.d/service.conf \
  --version 1.0.0rc4 \
  --commit-sha FULL_DEPLOYED_COMMIT_SHA \
  --evidence-url https://github.com/OWNER/REPOSITORY/actions/runs/RUN_ID \
  --output production-edge-evidence.json
```

The output follows
[`contracts/production-edge-evidence-v1.schema.json`](../contracts/production-edge-evidence-v1.schema.json).
It contains response and proxy-configuration hashes, bounded status/header
observations, and the required boolean checks; response bodies, cookies, and
credentials are never recorded. A custom internal CA may be supplied with
`--ca-file`, but there is intentionally no insecure TLS bypass. Attach the JSON
to the durable evidence URL before adding it to `release/qualification.json`.
The audit is read-only and does not require Redis, sticky routing, or any new
service-side state. Evidence produced by older auditor versions must be
regenerated because the proxy observations now include the selected-vhost,
selected-location, and forged-client checks.

The versioned [performance budgets](performance.md) cover health, generated
application packages, representative BFF calls, and cold/warm browser startup
on GitHub-hosted runners. These are regression gates, not capacity promises.
Re-run against each production topology and set service-specific BFF budgets:

```bash
python scripts/load_smoke.py \
  --base-url https://service.example.com \
  --requests 1000 --concurrency 20 --p95-budget-ms 500
```

## Clean deployment procedure

1. Build and verify the wheel: `uv build` and
   `uvx twine check dist/pytincture-*.whl`.
2. Create a new empty virtual environment and install only that wheel plus the
   application's pinned requirements.
3. Mount the immutable application modules and inject secrets through the
   platform secret manager; never bake `.env` or IdP private keys into images.
4. Start one candidate worker, require `/healthz` and `/readyz` to return 200,
   then run `scripts/load_smoke.py`.
5. Verify login, one packaged app, one sync BFF call, and one streaming BFF
   call through the real proxy hostname.
6. Add the candidate to the load balancer, drain the prior version, and watch
   the JSON event/error/latency dashboards for at least one session lifetime.

## Secret rotation

Generate a new signing secret, move the current value into
`AUTH_SESSION_PREVIOUS_SECRET_KEYS`, deploy the new current key to every worker,
and retain the prior key for at least `AUTH_SESSION_MAX_AGE_SECONDS`. Remove the
old key in a later deployment. Rotate OAuth, SAML, and Redis credentials with
provider overlap where available, then verify `/readyz` and a real login before
revoking the old credential.

## Backup and restore

Back up the immutable wheel/image, application modules, versioned
configuration (without secrets), IdP metadata/certificates, and deployment
manifests. Back up durable application databases according to their own
runbooks. Session revocations and unused replay tokens are transient security
state: do not restore stale copies after rollback. Redis loss requires users to
authenticate again and invalidates outstanding replay tokens.

## Rollback

1. Stop routing new traffic to the candidate and retain its logs/artifacts.
2. Redeploy the last known-good immutable wheel/image and matching application
   modules/configuration.
3. Keep the current and previous session signing keys during rollback so valid
   cookies can be re-signed; do not roll back to a removed or compromised key.
4. Require `/readyz` 200, run the load smoke, and verify login/BFF/streaming.
5. Drain the failed workers. If schema or application data changed, execute the
   application's separately tested backward migration before restoring writes.
6. Record the failed version, correlation IDs, and rollback time in the
   incident record before resuming the rollout.
