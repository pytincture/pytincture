# @pytincture/runtime

Standalone build of `pytincture.js`, the Pyodide bootstrapper used by the pytincture framework. It can be loaded directly from a CDN to run embedded Python snippets (or zipped pytincture apps) with no backend.

## Usage

```html
<!DOCTYPE html>
<html>
  <head>
    <script src="https://cdn.jsdelivr.net/npm/@pytincture/runtime@0.9.20/dist/pytincture.min.js"></script>
  </head>
  <body>
    <div id="maindiv" style="width:100%;height:100vh;"></div>

    <script type="text/json" id="micropip-libs">
      ["faker"]
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

What happens:

- The runtime loads Pyodide (default `https://cdn.jsdelivr.net/pyodide/v0.28.0/full/`).
- Installs `micropip` and any extra wheels listed in `#micropip-libs`.
- Installs the default widget library (`dhxpyt`) or another package you configure.
- Auto-detects `<script type="text/python">` blocks, mounts them under `/appcode`, finds a `MainWindow` subclass (or explicit entrypoint), and runs it.
- Errors are printed to the console and rendered inside `#maindiv` when present.

## Configuration

Before the script tag loads, you may set the following globals:

```html
<script>
  window.pytinctureAutoStartConfig = {
    widgetlib: "dhxpyt",
    libsSelector: "#micropip-libs",
    pyodideBaseUrl: "https://cdn.jsdelivr.net/pyodide/v0.28.0/full/",
    enableBackendLogging: false
  };
  // Disable auto-start if you want to call runTinctureApp manually:
  // window.pytinctureAutoStartDisabled = true;
</script>
<script src="https://cdn.jsdelivr.net/npm/@pytincture/runtime/dist/pytincture.min.js"></script>
```

Manual start (if auto-start is disabled):

```js
runTinctureApp({
  mode: "inline",
  widgetlib: "dhxpyt",
  enableBackendLogging: false
});
```

## Development

This package lives inside the main pytincture repository:

```bash
cd pytincture/frontend
npm install
npm run build        # emits dist/pytincture.{js,min.js,esm.js}
npm run build:watch  # rebuild on changes
```

`npm run build` automatically syncs the `package.json` version with the Python framework (`pytincture/__init__.__version__`), so npm releases always match the backend version.

## Publishing

From the repo root you can run:

```bash
bash scripts/publish_runtime.sh
```

The helper script:
1. Reads the framework version.
2. Installs dependencies & syncs package.json.
3. Builds the bundles.
4. Publishes to npm if that version isn’t already available.

Once published, load from jsDelivr/UNPKG:

```html
<script src="https://cdn.jsdelivr.net/npm/@pytincture/runtime@0.9.20/dist/pytincture.min.js"></script>
```

Replace `0.9.20` with the framework version you need, or omit it to use `@latest`.
