# Experimental browser runtime choice

Branch: `feat/browser-runtime-choice`. This is an opt-in client format under
development, not automatic conversion of an existing `dhxpyt` application.
Applications without a runtime manifest keep their existing Pyodide behavior.

| Engine | Execution | Current support |
|---|---|---|
| `pyodide` | CPython in WebAssembly | Existing packaged applications; also portable clients |
| `micropython` | MicroPython in WebAssembly | Portable Python clients with a compatible API subset |
| `transcrypt` | Python compiled to JavaScript before deployment | Portable clients compiled successfully by Transcrypt |

The Book Library feature-branch example exercises the same Python logic under
all three engines, including authenticated BFF reads, filtering, selection,
editing, saving and reloading. MicroPython and Transcrypt do not request any
Pyodide assets. The example's runtime picker reloads the page to change engines;
unsaved client state is not retained.

## Choose the engine

Environment-based applications can set:

```sh
PYTINCTURE_BROWSER_RUNTIME=micropython
PYTINCTURE_ALLOW_RUNTIME_SELECTION=true
```

For an explicit configuration object, pass the corresponding fields:

```python
config = PytinctureConfig(
    modules_path="./apps",
    browser_runtime="micropython",
    allow_runtime_selection=True,
    environment={
        "PYTINCTURE_PUBLIC_ASSET_PATHS": '{"books":["browser/books/*"]}',
    },
)
app = create_app(config)
```

As with other settings, explicitly constructing `PytinctureConfig` does not
implicitly read the process environment. Use `PytinctureConfig.from_env()` when
environment values should supply defaults.

`browser_runtime` defaults to `pyodide`. `allow_runtime_selection` defaults to
false. When enabled, `/books?runtime=micropython` selects a supported engine;
unknown or unsupported engines return 422, and disabled selection returns 400.
The choice survives the application's login redirect. No automatic fallback
silently changes the requested engine.

The application's Python entry module declares its client bundle using literals:

```python
from books_data import Library  # BFF dependency discovery remains unchanged

APP_TITLE = "Books"
APP_RUNTIME_MANIFEST = "browser/books/manifest.json"
# Optional per-app default, overriding the deployment default:
APP_BROWSER_RUNTIME = "pyodide"
```

Equivalent `APP_CONFIG` keys are `runtime_manifest` and `browser_runtime`.
Precedence is an enabled query selection, then app default, then deployment
default. The manifest applies to all its declared engines, including Pyodide.
Use a separate entry module if you want to keep the full original UI alongside
a smaller portable UI, as the example does with `py_ui` and `runtime_books`.

## Portable client format

The manifest and its assets are intentionally published through the existing
application-scoped public-asset allowlist. They must contain only frontend code
and public data. The main app page still requires its normal login, and BFF
authorization, sessions, argument validation and CSRF checks remain enforced.
Server `.py` files remain blocked by the public asset route. A build step puts
only selected browser Python modules into the explicitly public source JSON.

`browser/books/manifest.json`:

```json
{
  "schema": 1,
  "runtimes": ["pyodide", "micropython", "transcrypt"],
  "host": "host.js",
  "scripts": ["vendor/suite.js"],
  "styles": ["vendor/suite.css"],
  "sources": "sources.json",
  "entrypoint": "client",
  "compiled": "transcrypt/client.js",
  "micropython": {
    "module": "vendor/micropython.mjs",
    "wasm": "vendor/micropython.wasm",
    "heapBytes": 8388608
  }
}
```

Asset paths are relative to the manifest. They cannot be absolute, external,
hidden paths or parent-directory traversals. Supply pinned runtime assets from
your build; the loader does not contact a CDN or install packages automatically.
The working example pins MicroPython npm build `1.29.0-6` and Transcrypt `3.9.4`.

`sources.json` contains `{"files":{"client.py":"...","bridge.py":"..."}}`.
Only these selected files are installed in the interpreter filesystem. The
source bundle is limited to 256 files and 8 MiB. `entrypoint` names the Python
module; it must export `async def main()`. The compiled module must export
`main()` and any callbacks used by its host.

`host.js` exports `async function setup(context)`. It creates the page UI and
connects widgets before the client starts. `context` contains:

- `engine`, `runtimes`, and `application`.
- `invoke(name, payload)`: serialized calls to asynchronous Python callbacks or
  compiled JS callbacks. Interpreter payloads are strings; JSON is convenient
  for carrying records across the foreign-function interface. Await the promise
  or handle errors. Calls do not return converted Python objects in this version.
- `callBff(module, className, method, namedArguments)`: same-origin JSON POST,
  using session credentials and the configured CSRF cookie. Module paths may
  contain folders. It returns the parsed JSON response and rejects non-2xx
  responses. It does not implement generated-stub login redirects or streaming.

The loader first loads styles and scripts, then calls the host's `setup`, starts
the selected engine, and awaits `main()`. It returns a handle with `engine`,
`runtime`, and serialized `call(name, payload)`. Switching engines requires a
new document; multiple simultaneous runtimes in one page are not supported.

For custom HTML, call the existing entrypoint:

```javascript
const handle = await runTinctureApp({
  application: "books",
  runtime: "micropython",
  runtimeManifestUrl: "/books/appcode/browser/books/manifest.json",
  csrfCookieName: "__Host-pytincture-csrf"
});
```

## Compatibility limits and next work

The prototype uses a small JavaScript widget host instead of the existing
`dhxpyt` Python wrappers. MicroPython lacks the tested `dataclasses`, `typing`
and `pyodide.ffi` imports; Transcrypt cannot compile the original example
unchanged. Existing CPython wheels, generated Pyodide BFF stubs, synchronous
callbacks, BFF streaming, replay-token handling and the full widget suite have
not been ported. Deployments enabling BFF replay tokens receive an explicit 422
for portable clients; the existing Pyodide package path remains available.

Portable clients skip the Pyodide package installer, widget-wheel installer,
service-worker setup and cache warmup. This is part of the measured startup
benefit, alongside the smaller interpreter/compiler output. Compare engines
using equivalent client features and cold/warm network conditions.

Next work on this branch should expand `dhxpyt` compatibility, cover more widgets
and callback shapes, add runtime-specific diagnostics, and measure real devices
before proposing production support. Runtime adapters live in
`pytincture/frontend/browser-runtimes.js`; add another engine only after its
client contract and browser acceptance tests work.
