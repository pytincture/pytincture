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

Security fixes may narrow unsafe behavior without the normal deprecation
period. Release notes will identify impact and migration steps.
