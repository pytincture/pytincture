# Security policy

## Supported versions

The latest published minor/patch release and active 1.0 release candidates
receive security fixes. Older 0.x versions are not supported once a fixed
replacement is available.

## Report a vulnerability

Use GitHub Security Advisories for the repository: open the **Security** tab,
choose **Advisories**, then **Report a vulnerability**. Do not open a public
issue or include exploit details, credentials, tokens, assertions, cookies, or
private application code in logs.

Include affected versions/modes, impact, minimal reproduction, required
configuration, and any proposed mitigation. The maintainers will acknowledge
the report, coordinate validation and a fix, and credit the reporter unless
anonymity is requested. No guaranteed response SLA is currently offered.

## Security boundaries

- Browser Python, JavaScript, `.pyt` archives, wheels, network calls, and memory
  are controlled/observable by the user and cannot hold secrets.
- BFF authentication and authorization must be enforced on the server.
- Pytincture does not provide gateway rate limiting or a Web Application
  Firewall; production deployments must provide appropriate controls.
- Redis, identity providers, proxies, dhxpyt/widgetsets, Pyodide packages, and
  application code have their own security responsibilities.
- Browser console forwarding is bounded and redacts common credential forms
  before transmission, but applications must still keep secrets out of browser
  logs and may disable forwarding.

## Intentional framework contracts

- `@backend_for_frontend` is intentionally a class-level export declaration:
  its public methods and public read-only attributes become operations. A
  method-level export marker is not required. Static discovery must prove that
  the decorator and its security metadata aliases come directly from
  Pytincture and must reject local, unrelated, rebound, or shadowed names.
- Application-selected browser Python and manifest-approved widget JavaScript
  intentionally execute with same-origin application authority. They are
  trusted application code, not a sandbox. Direct public assets remain limited
  to real applications and explicit ownership declarations; Python is never a
  public asset, and SVG responses receive a no-script sandbox policy.
- SAML browser transactions intentionally remain stateless and portable across
  workers through signed HttpOnly cookies. Redis, process memory, and sticky
  sessions are not required. A deployment needing strict single-consumption
  across simultaneous duplicate requests must add a shared atomic control as
  an optional deployment policy.
- BFF discovery is fail-closed per canonical source file. Invalid files export
  no operations and are tracked as rejected, but cannot deny startup or remove
  valid exports for unrelated applications. Repair does not restore access
  until the file passes a complete static rescan.
- Python and npm publication accept only attested artifacts from a successful
  release-triggered CI run for a published, protected-default-branch-reachable
  tag. Registry publication uses protected GitHub environments and OIDC trusted
  publishing; long-lived registry credentials are not an accepted fallback.

## 2026-09-01 follow-up review disposition

The follow-up review found no confirmed critical/high vulnerability and no
request-controlled path to server-side code execution. Its two conditional
medium availability findings and related trust observations are disposed as
follows:

- Ordinary BFF generators are now incrementally bounded before general JSON
  conversion can expand them, and conversion runs outside the request event
  loop. Finite generators remain JSON arrays; a generator is stopped and closed
  after the configured item limit. Ordinary async iterables must use the
  existing explicit streaming declaration. This remediation is tracked in
  [#280](https://github.com/pytincture/pytincture/issues/280); explicit BFF
  streaming and `yield` remain supported.
- Trusted-mode coroutine BFFs and async policy hooks use cooperative event-loop
  timeouts by default. `BFF_ASYNC_EXECUTION_MODE=worker-thread` now provides
  additive opt-in containment on a bounded worker event loop and retains BFF
  admission until timed-out thread work exits. The existing
  `BFF_EXECUTION_MODE=isolated-process` option remains the killable boundary for
  non-streaming calls. This remediation is tracked in
  [#281](https://github.com/pytincture/pytincture/issues/281); synchronous
  applications, the compatibility default, and explicit streaming are
  unchanged.
- `MODULES_PATH` is deployment-trusted application source. A read-only
  container/root mount and non-root service account remain deployment
  controls. Writable roots now emit the structured startup event
  `security.modules_path_writable`; operators can set
  `PYTINCTURE_REQUIRE_READONLY_MODULES_PATH=true` to fail closed. This additive
  remediation is tracked in
  [#282](https://github.com/pytincture/pytincture/issues/282); a writable
  development tree remains supported by default.
- The optional process executor is a bounded, killable resource boundary, not
  a hostile-code sandbox. Pytincture BFF modules are operator-deployed trusted
  application code. Truly untrusted code requires a separate container/UID,
  secret boundary, syscall policy, and network policy outside Pytincture.
- Browser Python and selected widget JavaScript are trusted application code.
  Cross-origin Pyodide scripts and icon styles already fail closed without
  valid SRI, and executable widget assets must match their declared SHA-256
  hashes. The server still enforces authentication, signed application
  audience, Origin/Fetch Metadata, CSRF, canonical arguments, export identity,
  and policy on every applicable BFF call.
- Service scripts remain same-origin under CSP. `connect-src` now defaults to
  self plus the exact PyPI metadata and wheel origins; typed additional
  HTTPS/WSS origins are validated as credential-free, wildcard-free origin
  strings. PyPI is not a script source. External Pyodide/icons retain SRI
  requirements and widget assets retain SHA-256 verification. This remediation
  is tracked in [#283](https://github.com/pytincture/pytincture/issues/283).
- `@backend_for_frontend` remains the explicit class-level network export
  decision. Public members are operations by design; names beginning with `_`
  are already excluded from the manifest and cannot be remotely dispatched.

The machine-readable record for this scan is
[`security/review-2026-09-01-followup.json`](security/review-2026-09-01-followup.json).
It includes compatibility effects so future scans do not reinterpret accepted
architecture as an unaddressed defect.

## 2026-09-01 `ceb2c0b` review disposition

The source-only review of commit `ceb2c0b653aa0a7cc0fb9b330b3f8f4a7273647f`
reported PT-01 through PT-16. The findings are tracked with the compatibility
decisions made during triage:

| Finding | Disposition | Compatibility boundary |
|---|---|---|
| PT-01 custom widget dependency confusion | Remediated in [#289](https://github.com/pytincture/pytincture/issues/289). Service mode tries the exact deployment-owned backend wheel first, verifies its server-computed complete-wheel SHA-256, retains the built-in `dhxpyt` full-wheel lock, and permits custom PyPI fallback only for an explicitly allowlisted exact spec. | Pluggable custom widgetsets remain supported. The widgetset remains backend-discovered as already required; deployments retain its wheel artifact or explicitly approve the exact public-index source. |
| PT-02 reusable raw SAML exchange | Accepted stateless boundary; document and test the fresh-client raw replay. A vendor-neutral atomic transaction provider may be offered as an optional strict mode. | Redis, process memory, sticky routing, and shared mutable state remain unnecessary. Global single consumption cannot be guaranteed without some shared atomic state. Signed browser binding, short expiry, rate limits, and normal-browser replay rejection remain required. |
| PT-03 slow BFF request ingress | Remediated in [#290](https://github.com/pytincture/pytincture/issues/290). The capped body is read under `BFF_REQUEST_INGRESS_TIMEOUT_SECONDS` before execution admission. | The ingress timeout covers only request upload. Long backend execution retains its separate configurable timeout. |
| PT-04 blocked streaming writes | Remediated in [#291](https://github.com/pytincture/pytincture/issues/291) with `BFF_STREAM_WRITE_TIMEOUT_SECONDS`, iterator cleanup, and idempotent admission release. | Generators, async generators, `yield`, and long-running streams remain supported. Moving chunks reset the timeout; only a blocked client write is disconnected. |
| PT-05 OAuth callback amplification | Remediated in [#292](https://github.com/pytincture/pytincture/issues/292) with per-peer/application/provider rate controls, bounded token-exchange admission, explicit HTTP phase and overall deadlines, and application validation before provider work. | Normal OAuth remains stateless, load-balancer friendly, and Redis-free. Controls are deliberately generous and configurable. Strict globally atomic callback consumption remains optional. |
| PT-06 generic SAML groups as roles | No framework mapping or filtering change. Signed IdP groups are authenticated identity claims supplied to application-owned authorization decorators and policies. | Applications intentionally define their own authorization. Pytincture must not invent privileged role names or reinterpret group values; it only transports the signed claims developers elect to use. `SAML_ALLOWED_ROLES` controls login eligibility, not a granted-role filter. |
| PT-07 MCP token time requirements | Fix in [#293](https://github.com/pytincture/pytincture/issues/293) with finite expiration, `nbf`/`iat`, skew, age, and lifetime policy. | Existing non-expiring tokens need migration or an explicit compatibility setting; secure production mode requires bounded tokens. |
| PT-08 cookie tossing | Fix in [#294](https://github.com/pytincture/pytincture/issues/294) with host-prefixed HTTPS production cookies and separate HTTP development names. | Existing production sessions may require one fresh login. Local HTTP development remains supported. |
| PT-09 repeated application graph scans | Fix in [#295](https://github.com/pytincture/pytincture/issues/295) with bounded, fingerprint-invalidated, process-local graph snapshots. | The cache is disposable and requires no shared state, sticky routing, or Redis. Same-name development edits must invalidate correctly. |
| PT-10 public asset amplification | Remediated in [#296](https://github.com/pytincture/pytincture/issues/296) with fingerprint-invalidated authorization caching plus configurable admission, size, total-duration, and blocked-write limits. | Explicit public assets remain unauthenticated. Ordinary assets avoid repeated source parsing; unusually large media can raise limits or use object storage. Same-name source and asset changes remain visible. No Redis or sticky routing is required. |
| PT-11 replay-refill quota poisoning | Fix in [#297](https://github.com/pytincture/pytincture/issues/297) with denial-safe ordered or transactional accounting. | Accepted requests are unchanged and replay refill remains disabled by default. |
| PT-12 per-session isolated-process quota | Fix in [#298](https://github.com/pytincture/pytincture/issues/298) by keying only scarce isolated-process fairness to stable authenticated identity. | Ordinary cooperative async BFF calls are not subject to this limit. Isolated execution uses configurable, generous concurrency and fair queuing rather than a usage-volume limit. |
| PT-13 mutable Microsoft email | Add stable-identity guidance and production warnings in [#299](https://github.com/pytincture/pytincture/issues/299). | Email/domain admission remains available for simple applications. Sensitive authorization should use immutable tenant/object or issuer/subject identity. |
| PT-14 password verification timing | Fix in [#300](https://github.com/pytincture/pytincture/issues/300) with equivalent failure work and supported transparent bcrypt migration. | Password values and the login API do not change. |
| PT-15 numeric `Literal` equality | Fix in [#301](https://github.com/pytincture/pytincture/issues/301) by comparing exact type and value. | Only requests whose JSON type already violates the declared annotation are rejected. |
| PT-16 public diagnostics | Add optional production controls in [#302](https://github.com/pytincture/pytincture/issues/302). | Development remains convenient and a minimal public health response remains available for load balancers. |

Additional residual risks remain accepted architecture:

- Stateless logout cannot revoke a copied session cookie across every worker
  without an optional shared revocation provider. Absolute session expiry,
  signing-key rotation, Secure/HttpOnly cookies, and application audience
  limits remain the Redis-free controls.
- External Pyodide mode does not authenticate every runtime support file. The
  hosted service uses the vendored runtime; external mode is an explicit
  application trust decision and its bootstrap scripts retain SRI checks.
- A BFF policy hook returning `None` retains the documented compatibility
  meaning of allow. Applications that implement conditional authorization must
  explicitly deny or raise according to the policy contract.
- Cooperative trusted-thread timeouts cannot kill Python threads. The bounded,
  killable isolated-process mode exists for non-streaming operations that need
  hard termination; it is resource containment for deployment-trusted code,
  not a hostile-code sandbox.
- `dhxpyt` and other widgetset implementations remain separately reviewed
  dependencies. Pytincture authenticates what it loads but does not claim to
  sandbox trusted browser packages.

The machine-readable record for this review is
[`security/review-2026-09-01-ceb2c0b.json`](security/review-2026-09-01-ceb2c0b.json).
It must be updated as each linked issue is remediated so later scans can
distinguish completed controls, compatibility choices, and accepted risk.

The machine-readable dispositions and their regression-test mappings are in
[`security/review-dispositions.json`](security/review-dispositions.json).
The exact version, source integrity, license, and file hashes for the vendored
BFF documentation UI are recorded in
[`security/swagger-ui-assets.json`](security/swagger-ui-assets.json). Those
assets are served only from the explicit framework manifest with the service
instance UUID; the documentation page does not relax CSP for a third-party CDN.

Security fixes may narrow unsafe behavior without the normal deprecation
period. Release notes will identify impact and migration steps.
