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
  controls. A prominent warning plus optional fail-closed enforcement is
  tracked in [#282](https://github.com/pytincture/pytincture/issues/282); a
  writable development tree remains supported by default.
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
- Service scripts remain same-origin under CSP; PyPI is a package connection,
  never a script source. Narrowing the default connection policy to self plus
  the exact PyPI metadata/wheel origins is tracked in
  [#283](https://github.com/pytincture/pytincture/issues/283).
- `@backend_for_frontend` remains the explicit class-level network export
  decision. Public members are operations by design; names beginning with `_`
  are already excluded from the manifest and cannot be remotely dispatched.

The machine-readable record for this scan is
[`security/review-2026-09-01-followup.json`](security/review-2026-09-01-followup.json).
It includes compatibility effects so future scans do not reinterpret accepted
architecture as an unaddressed defect.

The machine-readable dispositions and their regression-test mappings are in
[`security/review-dispositions.json`](security/review-dispositions.json).
The exact version, source integrity, license, and file hashes for the vendored
BFF documentation UI are recorded in
[`security/swagger-ui-assets.json`](security/swagger-ui-assets.json). Those
assets are served only from the explicit framework manifest with the service
instance UUID; the documentation page does not relax CSP for a third-party CDN.

Security fixes may narrow unsafe behavior without the normal deprecation
period. Release notes will identify impact and migration steps.
