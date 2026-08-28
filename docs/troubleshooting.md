# Troubleshooting

Start with the visible lifecycle stage, the browser console, the failing
network request, and its `X-Request-ID`. `/healthz` tests the process;
`/readyz` also tests required files and configured Redis.

## Application does not start

- `runtime-load`: verify the Pyodide base URL and that all same-origin runtime
  files return 200 with the instance `?uuid=` query.
- `package-install`: inspect the package-index response; do not add a backend
  UUID to micropip/PyPI URLs.
- `widgetset-install`: Pytincture tries PyPI, then the backend for the declared
  real version, then `99.99.99`. A failed PyPI lookup followed by a successful
  backend wheel is expected fallback, not an application failure.
- `widgetset-load`: confirm the wheel includes the required JavaScript/CSS and
  that its `__widgetset__`/`__version__` metadata matches.
- `entrypoint-execution`: the packaged app failed. Pytincture intentionally
  does not hide this by falling back to inline mode.

`TypeError: chartFactory is not a constructor` means the widget JavaScript for
that chart is missing or incompatible, even if the Python wrapper installed.
Verify the dhxpyt version and DHTMLX chart bundle before changing application
Python.

## Cache and URL behavior

Frontend, appcode, public assets, service workers, and backend wheel responses
use one UUID per service process. Restarting changes it. The browser address
bar must remain `/{application}` without `?uuid=...`; if navigation redirects
to a UUID URL, report it as a runtime regression. Hard reload once after a
service-worker upgrade and inspect the worker script URL/scope.

## Login loops or identity-provider failures

Confirm the process has the intended enable flags, a stable strong signing
key, correct secure-cookie/proxy settings, and the exact public callback URL.
For SAML, validate metadata and certificate newlines and leave requested authn
context disabled unless the IdP explicitly requires it. Selecting a password
option manually does not prove the original AuthnRequest is accepted.

## Layout/widget display problems

Poor collapsed-sidebar contrast or a logo that does not shrink is application
theme/layout behavior, normally in dhxpyt configuration or app CSS—not a
Pytincture loader problem. Reproduce with the same widgetset outside the
application, inspect computed styles and collapsed dimensions, then change the
app/widgetset at the owning layer.

## Useful evidence

Record Pytincture, Pyodide, Python, widget package/version, browser engine,
lifecycle stage/code/resource, request ID, failing response status, and the
sanitized server log event. Never paste session cookies, OAuth tokens, SAML
assertions, Redis tokens, private keys, or full production environment dumps.
