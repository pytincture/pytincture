# @pytincture/runtime

Standalone build of `pytincture.js`, the browser bootstrapper used by the
Pytincture framework. Legacy Pyodide delivery is the default; portable Pyodide
and MicroPython are opt-in through [runtime configuration](../../docs/browser-runtimes.md).
Production sites should export the verified, self-hosted
runtime and Pyodide set from the Python wheel; exact CDN URLs remain an
explicit convenience mode and require SRI.

## Validation

Run `npm test` for lifecycle unit tests, `npm run test:browser` for the
Chromium lifecycle harness, and `npm run build` to regenerate the distributable
IIFE, minified, and ESM bundles. Playwright retains traces, screenshots, and
videos for browser failures; CI uploads those artifacts.

See [`../../docs/browser-lifecycle.md`](../../docs/browser-lifecycle.md) for
the public startup stages, callback events, compatibility report, and typed
error fields.

## Usage

```html
<!DOCTYPE html>
<html>
  <head>
    <script src="./frontend/dist/pytincture.min.js"></script>
  </head>
  <body>
    <div id="maindiv" style="width:100%;height:100vh;"></div>

    <script type="text/json" id="micropip-libs">
      ["faker==37.0.0"]
    </script>

    <script type="text/python">
from dhxpyt.layout import MainWindow

class Demo(MainWindow):
    def load_ui(self):
        self.set_theme("dark")
        print("Demo loaded!")
    </script>
  </body>
</html>
```

What happens with default legacy Pyodide delivery:

- The runtime loads Pyodide (default `./frontend/pyodide/0.29.3/full/`).
- Installs `micropip` and any extra wheels listed in `#micropip-libs`.
- Installs the default widget library (`dhxpyt`) or another package you configure.
- For service apps, the runtime prefers the backend wheel for the requested
  version, then its permitted development fallback, before the built-in
  complete-wheel lock or an explicitly allowed public-index pin. An explicit
  hash-locked `widgetSource` overrides this order and disables fallback.
- Frontend assets share the service instance's `uuid` query parameter, which
  rotates on restart. The service worker caches only its approved immutable
  manifest assets, using that UUID in the cache namespace and canonical keys.
  PyPI/micropip resolution stays canonical; only backend-hosted
  widgetset wheel candidates receive the instance UUID.
- Auto-detects `<script type="text/python">` blocks, mounts them under `/appcode`, finds a `MainWindow` subclass (or explicit entrypoint), and runs it.
- Startup rejects with a stage-specific `PytinctureLifecycleError`; inline
  auto-start failures are also rendered inside `#maindiv` when present.

## Configuration

Before the script tag loads, you may set the following globals:

```html
<script>
  window.pytinctureAutoStartConfig = {
    widgetlib: "dhxpyt==0.9.18",
    libsSelector: "#micropip-libs",
    pyodideBaseUrl: "./frontend/pyodide/0.29.3/full/",
    enableServiceWorker: true,
    enableBackendLogging: false
  };
  // Disable auto-start if you want to call runTinctureApp manually:
  // window.pytinctureAutoStartDisabled = true;
</script>
<script src="./frontend/dist/pytincture.min.js"></script>
```

Manual start (if auto-start is disabled):

```js
runTinctureApp({
  mode: "inline",
  widgetlib: "dhxpyt==0.9.18",
  enableBackendLogging: false
});
```

## Caching

To avoid re-downloading Pyodide assets on refresh, you can enable the bundled service worker:

```html
<script>
  window.pytinctureAutoStartConfig = {
    enableServiceWorker: true,
    serviceWorkerUrl: "sw.js",
    serviceWorkerScope: "./"
  };
</script>
```

Notes:
- Service workers require HTTPS (or localhost) and a same-origin `sw.js`.
- The bundled worker intercepts only its explicit Pytincture/Pyodide asset
  manifest. It does not cache appcode, BFF/auth responses, arbitrary files, or
  private/cookie-setting responses.
- Service applications receive an application-scoped worker and an
  application/release/instance cache namespace. Upgrades delete only older
  caches owned by that application; disabling caching unregisters that worker
  and leaves other applications and libraries alone.
- Pytincture never patches global `fetch`. Cross-origin APIs, package indexes,
  and presigned URLs therefore remain byte-for-byte unchanged.

## Development

This package lives inside the main pytincture repository:

```bash
cd pytincture/frontend
npm ci --ignore-scripts
npm run build        # emits dist/pytincture.{js,min.js,esm.js}
npm run build:watch  # rebuild on changes
```

`npm run build` automatically syncs the `package.json` version with the Python framework (`pytincture/__init__.__version__`), so npm releases always match the backend version.

## Publishing

Registry publication has exactly one supported path: the protected
`.github/workflows/npm-publish.yml` workflow described in
[`docs/releasing.md`](../../docs/releasing.md). It verifies the published tag,
protected-branch ancestry, retained release artifact, GitHub attestation, and
package identity before publishing with npm trusted-publisher OIDC. There is no
local registry-publish command.

Maintainers can build and inspect the package locally without registry
credentials:

```bash
cd pytincture/frontend
npm ci --ignore-scripts
npm run build
npm pack --dry-run
```

For controlled demos, an exact version can be loaded from a CDN only after its
npm publication completes, with matching SRI copied from the trusted release
integrity manifest. A GitHub or PyPI release alone does not make this URL
available; use the Python-wheel asset export while npm publication is pending:

```html
<script
  src="https://cdn.jsdelivr.net/npm/@pytincture/runtime@1.0.0-rc.13/dist/pytincture.min.js"
  integrity="sha384-<trusted-manifest-value>"
  crossorigin="anonymous"></script>
```

See [`docs/standalone-mode.md`](../../docs/standalone-mode.md) for the local
asset export and external Pyodide/icon integrity configuration.

Always use the exact framework version and its matching trusted SRI; mutable
tags are unsupported for external production assets.
