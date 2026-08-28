# Browser end-to-end testing

Pytincture's release suite runs a real authenticated service and real Pyodide
application in Chromium, Firefox, and WebKit. The fixture verifies:

- packaged and inline application startup;
- the pinned dhxpyt backend-wheel fallback and loaded DHTMLX assets;
- static Python imports plus explicit `PYTINCTURE_BROWSER_FILES` inclusion;
- CSS application and widget JavaScript/CSS asset discovery;
- verified local login and sync, async, and streaming BFF calls;
- UUID cache namespaces without query parameters on the visible app URL;
- service-worker registration; and
- visible, non-fallback entrypoint failure diagnostics.

## Pinned compatibility matrix

| Component | E2E version |
| --- | --- |
| Pytincture runtime | Synchronized from `pytincture.__version__` |
| Pyodide | 0.29.3 browser distribution |
| Python in Pyodide | 3.13 |
| dhxpyt | 0.9.16 |
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
