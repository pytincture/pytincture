# Browser end-to-end testing

Pytincture's release suite runs a real authenticated service and real Pyodide
application in Chromium, Firefox, and WebKit. The fixture verifies:

- packaged and inline application startup;
- the pinned dhxpyt backend-wheel fallback and loaded DHTMLX assets;
- static Python imports plus explicit `PYTINCTURE_BROWSER_FILES` inclusion;
- CSS application and widget JavaScript/CSS asset discovery;
- verified local login and sync, async, and streaming BFF calls;
- UUID cache namespaces without query parameters on the visible app URL;
- application-scoped service-worker registration, upgrade/unregister behavior,
  private-response rejection, foreign-cache preservation, and unmodified
  cross-origin/presigned requests; and
- visible, non-fallback entrypoint failure diagnostics.

## Pinned compatibility matrix

| Component | E2E version |
| --- | --- |
| Pytincture runtime | Synchronized from `pytincture.__version__` |
| Pyodide | 0.29.3 browser distribution |
| Python in Pyodide | 3.13 |
| dhxpyt | 0.9.16 |
| Keycloak | 26.7.2, digest pinned in CI |
| Playwright | 1.62.1 |
| Browser engines | Playwright 1.62.1 Chromium, Firefox, and WebKit builds |

The backend test environment installs the current checkout. It downloads the
exact dhxpyt wheel without installing its server-side dependencies. It exposes
that wheel under an intentionally unpublished `0.9.16+backend` candidate, then
forces the browser's initial package-index lookup to fail so the deployed
backend wheel path is exercised.

## Running locally

From the repository root:

```bash
python -m pip download --no-deps --dest tests/e2e_apps dhxpyt==0.9.16
cp tests/e2e_apps/dhxpyt-0.9.16-py3-none-any.whl \
  tests/e2e_apps/dhxpyt-0.9.16+backend-py3-none-any.whl
cd pytincture/frontend
npm ci
npx playwright install chromium firefox webkit
npm run test:e2e
```

Failed runs retain Playwright traces, screenshots, videos, console entries,
network entries, and `tests/e2e-server.log`. CI uploads those artifacts per
browser engine.

## Standalone Python-wheel acceptance

The separate Chromium standalone job depends on the validated release artifact
job and installs that Python wheel into a clean, non-editable environment. Its
static fixture serves `pytincture.min.js` and Pyodide only from the installed
wheel. It verifies a real dhxpyt layout, pinned backend-wheel fallback before
the `99.99.99` development fallback, UUID cache tokens on local frontend
resources, canonical unmodified package-index URLs, a clean visible address,
and zero unexpected console or network errors.

The job retains `standalone-acceptance.json` plus console, network, trace,
screenshot, video, and server-log diagnostics. This suite is intentionally
separate from service-mode BFF and authentication coverage.

## Federated SAML acceptance

The `Keycloak SAML acceptance` job starts the pinned Keycloak 26.7.2 image and
imports `tests/federated/keycloak-realm.json`. It installs the validated
Pytincture wheel with the `saml` extra in a clean environment, then uses
Chromium to exercise a signed redirect/POST flow against the real service.

The suite verifies the SP entity ID and disabled `RequestedAuthnContext`, the
IdP login and ACS callback, packaged application startup, authenticated BFF
access, session persistence across reload, local logout, and denial of BFF
access after logout. CI retains structured `saml-acceptance.json`, console and
network evidence, Playwright diagnostics, and both service and Keycloak logs.

Pytincture logout is local-only. The suite therefore inspects its redirect
without following it; following `/login` would immediately initiate SAML and
an existing Keycloak SSO session may authenticate again.
