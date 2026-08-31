# Production deployment and rollback

This runbook defines the supported Pytincture 1.0 deployment topology. Run the
service at the origin root behind a TLS-terminating reverse proxy. A URL path
prefix is not supported because application, BFF, frontend, and service-worker
routes intentionally share the root scope.

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
- Set explicit `CORS_ALLOWED_ORIGINS`; wildcard credentialed CORS is rejected.
- SAML responses are decoded and parsed under `SAML_RESPONSE_MAX_BYTES`, use
  an XML-signature transform allowlist, and are throttled per peer before
  signature processing. Tune `SAML_ACS_RATE_LIMIT_ATTEMPTS` and
  `SAML_ACS_RATE_LIMIT_WINDOW_SECONDS` alongside an edge rate limit; do not
  disable or bypass the built-in response guard.
- SAML logins use an HttpOnly browser-binding cookie and a one-time transaction
  with exact `InResponseTo`, response-ID, and assertion-ID correlation. Keep
  `AUTH_SESSION_HTTPS_ONLY=true` so cross-site POST binding uses a Secure,
  `SameSite=None` handshake cookie.
- Pin the Pytincture and widgetset versions in the deployment artifact.
- Redis is optional. Load-balanced sessions and SAML handshakes are carried in
  signed browser cookies and work across workers without server-side state.

Signed session and SAML handshake cookies can be read by any worker sharing the
signing secret. Redis is only an optional enhancement for immediate
cross-worker logout revocation and configured one-time BFF replay tokens; it is
not required to run Pytincture or load balance normal authenticated traffic.

Resource admission counters and the appcode cache are bounded, disposable, and
local to each worker. They do not contain durable login/session state and do
not require sticky routing. Size concurrency settings per worker, then apply a
gateway-wide request-rate and connection limit across the deployment. Local
password login has independent peer/account limits per worker, so the gateway
should provide the coordinated outer limit when multiple workers are exposed.
Optional Upstash operations use short deadlines and a per-worker circuit
breaker; Redis remains optional and is not used by the default signed-cookie
session path. When that integration is explicitly enabled, its remote URL must
use HTTPS. Cleartext HTTP is accepted only for local emulators addressed by a
literal loopback IP such as `127.0.0.1` or `::1`.

Keep `MODULES_PATH` writable only by the deployment principal. Pytincture
canonicalizes the root, rejects symlink components and cross-platform traversal
syntax, uses no-follow opens where supported, and verifies BFF source digests
between discovery and execution. These checks are defense in depth, not a
replacement for read-only application artifacts and least-privilege filesystem
permissions.

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
    proxy_set_header X-Request-ID $request_id;
    proxy_buffering off;
    proxy_read_timeout 310s;
}
```

`proxy_buffering off` is required for incremental BFF streaming. Set the proxy
timeout slightly above `BFF_STREAM_MAX_SECONDS`. Do not forward arbitrary
client-supplied `X-Forwarded-*` values. Pytincture accepts a caller request ID
only when it contains 1–128 letters, digits, `.`, `_`, `:`, or `-`.

## Health and monitoring

- `GET /healthz` is the liveness probe. HTTP 200 means the worker event loop
  can answer; it does not check dependencies.
- `GET /readyz` is the readiness probe. HTTP 200 means the modules directory,
  frontend entrypoint/runtime, and configured Redis stores are available.
  HTTP 503 means the worker must not receive traffic.
- Do not place either endpoint behind application authentication.

Pytincture emits one-line JSON events on the `pytincture.security` logger.
Collect `request.complete`, `bff.start`, and `bff.stream.finish`; index at least
`correlation_id`, `path`, `status_code`, `duration_ms`, and streaming `reason`.
Alert on readiness failures, elevated 5xx/401/403 rates, BFF timeouts, stream
`byte-limit`/`timeout` frequency, and p95 latency budget regressions. Control
verbosity with `PYTINCTURE_LOG_LEVEL`.

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
