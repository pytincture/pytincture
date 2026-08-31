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

The machine-readable dispositions and their regression-test mappings are in
[`security/review-dispositions.json`](security/review-dispositions.json).
The exact version, source integrity, license, and file hashes for the vendored
BFF documentation UI are recorded in
[`security/swagger-ui-assets.json`](security/swagger-ui-assets.json). Those
assets are served only from the explicit framework manifest with the service
instance UUID; the documentation page does not relax CSP for a third-party CDN.

Security fixes may narrow unsafe behavior without the normal deprecation
period. Release notes will identify impact and migration steps.
