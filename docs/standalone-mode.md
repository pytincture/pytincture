# Standalone browser mode

Standalone mode serves ordinary static HTML. `@pytincture/runtime` loads
Pyodide, installs the configured widgetset and micropip libraries, executes
inline Python, and starts the selected entrypoint. There is no Pytincture
service, BFF, server authentication, backend wheel fallback, or private Python.

## Minimal page

Set configuration before loading the runtime:

```html
<script>
  window.pytinctureAutoStartConfig = {
    mode: "inline",
    entrypoint: "MyApp",
    widgetlib: "dhxpyt==0.9.16",
    pyodideBaseUrl: "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/",
    onLifecycleEvent: event => console.debug(event.stage, event.type)
  };
</script>
<script src="https://cdn.jsdelivr.net/npm/@pytincture/runtime@1.0.0-rc.1/dist/pytincture.min.js"></script>
<div id="maindiv"></div>
<script type="text/python">
import js
from dhxpyt.layout import MainWindow

class MyApp(MainWindow):
    def load_ui(self):
        js.document.getElementById("maindiv").textContent = "Ready"
</script>
```

Serve the page over HTTP(S). Pin the runtime and widget versions in production.
To add pure-Python browser dependencies:

```html
<script type="text/json" id="micropip-libs">["faker==37.0.0"]</script>
```

Packages must provide a Pyodide-compatible wheel or be pure Python. A normal
CPython native wheel cannot run in WebAssembly.

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
