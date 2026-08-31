# Standalone browser mode

Standalone mode serves ordinary static HTML. `@pytincture/runtime` loads
Pyodide, installs the configured widgetset and micropip libraries, executes
inline Python, and starts the selected entrypoint. It does not require a
Pytincture service and provides no BFF, server authentication, or private
Python.

A static companion origin may optionally expose a widget wheel using the
`/{application}/appcode/{package}-{version}-py3-none-any.whl` convention. Set
`application` and `devWidgetHost` in the inline runtime configuration to enable
that package fallback without enabling a Pytincture service, BFF, or server
authentication. The runtime checks the pinned version before `99.99.99`.

## Self-hosted production setup

Install the exact Pytincture release, then export its verified browser assets
into the static site's `frontend/` directory:

```bash
python -m pip install 'pytincture==1.0.0rc2'
python -m pytincture.assets ./public/frontend
```

The export command verifies every runtime, Pyodide, WASM, standard-library,
and icon asset against the versioned
`frontend/integrity/pytincture-1.0.0rc2.json` manifest before copying it. This
self-hosted layout is the production default. Set configuration before loading
the runtime:

```html
<script>
  window.pytinctureAutoStartConfig = {
    mode: "inline",
    entrypoint: "MyApp",
    widgetlib: "dhxpyt==0.9.16",
    onLifecycleEvent: event => console.debug(event.stage, event.type)
  };
</script>
<script src="./frontend/dist/pytincture.min.js"></script>
<div id="maindiv"></div>
<script type="text/python">
import js
from dhxpyt.layout import MainWindow

class MyApp(MainWindow):
    def load_ui(self):
        js.document.getElementById("maindiv").textContent = "Ready"
</script>
```

The default Pyodide and Material Icons URLs point into this same `frontend/`
tree. Serve the page over HTTP(S). To add pure-Python browser dependencies:

```html
<script type="text/json" id="micropip-libs">["faker==37.0.0"]</script>
```

Packages must provide a Pyodide-compatible wheel or be pure Python. A normal
CPython native wheel cannot run in WebAssembly. Every entry must be an exact
`name==version` pin or a wheel URL ending in `#sha256=<64 hex>`. Automatic
dependency resolution is disabled, so list every required package explicitly;
this keeps the browser dependency set reviewable and reproducible.

Widgetsets may remain fully pluggable. A widget wheel that executes JavaScript
or loads CSS includes a hashed `pytincture-assets.json`; see
[widgetset packaging](widgetset-packaging.md). Pytincture never scans unrelated
or transitive packages for executable assets.

## Explicit CDN convenience mode

CDN loading is intended for controlled demos and development. Use immutable,
exact release URLs and copy—not dynamically fetch—the matching `sri` values
from the release integrity manifest into trusted HTML/configuration:

```html
<script>
  window.pytinctureAutoStartConfig = {
    mode: "inline",
    entrypoint: "MyApp",
    widgetlib: "dhxpyt==0.9.16",
    pyodideBaseUrl: "https://cdn.example/pyodide/0.29.3/full/",
    pyodideScriptIntegrity: {
      "pyodide.js": "sha384-<trusted-manifest-value>",
      "pyodide.asm.js": "sha384-<trusted-manifest-value>"
    },
    materialIconsUrl: "https://cdn.example/materialdesignicons/7.4.47/materialdesignicons.css",
    materialIconsIntegrity: "sha384-<trusted-manifest-value>"
  };
</script>
<script
  src="https://cdn.example/pytincture/1.0.0rc2/pytincture.min.js"
  integrity="sha384-<trusted-manifest-value>"
  crossorigin="anonymous"></script>
```

External Pyodide and icon URLs fail preflight unless their SRI values are
supplied. The runtime applies anonymous CORS to those script/stylesheet loads.
WASM, the standard library, and Pyodide metadata are loaded internally by
Pyodide, so verify those bytes from the signed/reviewed manifest before
deployment or self-host them. A manifest fetched from the same potentially
compromised CDN is not an independent trust root.

## Explicit startup

Set `window.pytinctureAutoStartDisabled = true` before the runtime and call:

```javascript
await window.runTinctureApp({
  mode: "inline",
  entrypoint: "MyApp",
  widgetlib: "dhxpyt==0.9.16"
});
```

The promise resolves after the entrypoint starts and rejects with
`PytinctureLifecycleError`. The full stable key list is in the
[public API contract](public-api.md#runtime-configuration-keys), and startup
stages are in the [browser lifecycle guide](browser-lifecycle.md).

## Security boundary

Every inline script, package name, URL, and value in browser memory is visible
to the user. Never embed service credentials or treat browser Python as a
trusted authorization layer. Use service mode and enforce policy in BFF code
when data or operations require trust.
