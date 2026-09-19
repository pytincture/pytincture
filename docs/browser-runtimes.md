# Experimental browser runtime choice

Branch: `feat/browser-runtime-choice`. This is an opt-in client format under
development, with a reusable build command for compatible Python applications.
Applications without a runtime manifest keep their existing Pyodide behavior.

| Engine | Execution | Current support |
|---|---|---|
| `pyodide` | CPython in WebAssembly | Existing packaged applications; also portable clients |
| `micropython` | MicroPython in WebAssembly | Portable Python clients with a compatible API subset |
| `transcrypt` | Python compiled to JavaScript before deployment | Portable clients compiled successfully by Transcrypt |

The full Book Library and Wawesome Chat apps run their Python UIs and widget
wrappers under MicroPython and Pyodide. Transcrypt remains limited to the earlier
reduced client prototype. Changing engines reloads the page; unsaved client
state is not retained. MicroPython does not request Pyodide assets.

## Build an application

Application code keeps using `import js` and the normal DOM or widget APIs. No
application-specific JavaScript host, replacement UI, or handwritten BFF bridge
is needed. MicroPython compatibility and packaging live in the framework.

Install this branch in the app's build environment, alongside its existing
widget dependencies. Add this to the app's `pyproject.toml`:

```toml
[tool.pytincture.browser]
application = "orders"
modules-path = "apps"
```

Then build and select the runtime:

```sh
pytincture-build-browser --config pyproject.toml
# Equivalent from a source checkout:
python -m pytincture.browser_build --config pyproject.toml

export PYTINCTURE_BROWSER_RUNTIME=micropython
export PYTINCTURE_PUBLIC_ASSET_PATHS='{"orders":["browser/orders/*"]}'
# Optional development runtime picker through ?runtime=:
export PYTINCTURE_ALLOW_RUNTIME_SELECTION=true
```

Launch your normal server and open `/orders`. Explicit `PytinctureConfig` users
must set the corresponding fields and environment mapping, as shown below.
The builder uses the same entrypoint convention as the server, follows local
imports, and emits `apps/browser/orders/manifest.json`. MicroPython automatically
uses that conventional bundle when `APP_RUNTIME_MANIFEST` is absent. Pyodide
retains its original package delivery in that case. Declare
`APP_RUNTIME_MANIFEST = "browser/orders/manifest.json"` only if you also want
Pyodide to use the same generated bundle or need a nonstandard bundle location.

The pinned MicroPython runtime (`1.29.0-6`) is included in the framework wheel;
apps do not need npm or a separate MicroPython installation. The browser still
downloads and initializes the smaller WASM runtime. For dhxpyt apps, the builder
reads the installed dhxpyt distribution without importing it. A `widget-wheel`
path can select an explicit wheel instead. Tested widget version: `0.9.19` from the widget repository (not yet on PyPI).
For another widgetset, set `widget-package = "wapyt"` and install its wheel in the
build environment, or provide `widget-wheel`. Other widgetsets must include a
`pytincture-assets.json` manifest with ordered JavaScript/CSS paths and SHA-256
hashes. The builder validates those hashes and bundles the package sources and
assets; stale manifests fail before replacing an existing bundle.

The browser build never imports or executes application/server modules. It
follows local packages, relative imports and re-exports, replacing decorated
BFF modules with generated stubs and stopping traversal at that boundary.
Backend imports, bodies and package initializers are not included through a BFF
edge. Type-checking-only dependencies are omitted. Keep secrets and server-only
work behind BFF modules, just as with normal browser packaging.

Rebuild after changing browser sources, BFF signatures, assets or dependencies.
Server startup does not compile applications. `--check` validates without
writing output; `build.json` records source, dependency and runtime hashes.
Known incompatible imports and syntax report build errors before changing the
existing bundle. Build output should be ignored in source control or handled as
a deployment artifact according to the application's normal process.

### Optional build settings

```toml
[tool.pytincture.browser]
application = "orders"
modules-path = "apps"
entrypoint = "orders:OrdersWindow"  # override server-style entrypoint discovery
# Inferred by default: async for async functions, callable for classes/functions.
entry-kind = "mainwindow"         # callable and async are also accepted
output = "browser/orders"
files = ["plugins/extra_view.py"]  # extra files for dynamic imports
bff = ["api/extra_data.py"]        # extra BFF modules; normal imports are inferred
wheels = ["vendor/browser_helpers-1.0-py3-none-any.whl"]
assets = ["public/theme.css", "public/settings.json", "public/helpers.js"]
styles = ["public/theme.css"]
scripts = ["public/helpers.js"]
heap-bytes = 16777216
# Optional overrides; normally use the installed widget and bundled interpreter:
widget-wheel = "vendor/dhxpyt-0.9.19-py3-none-any.whl"
micropython-assets = "vendor/micropython"
```

`modules-path`, wheel paths and `micropython-assets` are relative to the TOML
file. `files`, `bff`, `output` and `assets` are relative to `modules-path`.
Scripts/styles must also appear in `assets`. Access a copied asset from Python
with `js.pytinctureAssetUrl("assets/public/settings.json")`. Pure-Python wheels
can add browser libraries; native extensions and unresolved dependencies fail
validation. This does not make an arbitrary CPython package MicroPython-compatible.
For a strict explicit inventory, set `discover-imports = false` and list all
browser modules in `files` and BFF modules in `bff`.

Plain DOM applications can use `entrypoint = "orders:main"` with
`async def main()` and no widget dependency. Set `APP_ENTRYPOINT = "main"` in
the entry module when needed by the server's normal discovery. MainWindow and
nested Layout subclasses get their usual `load_ui()` invocation after their
constructor completes, including inherited/custom constructors. Successful
startup sets `window.pytinctureAppReady` after the entrypoint returns. Background
tasks do not block readiness; await required initialization in `async main()`.

### Multiple applications

Shared settings and independent per-app outputs can live in one file:

```toml
[tool.pytincture.browser]
modules-path = "apps"

[tool.pytincture.browser.apps.orders]

[tool.pytincture.browser.apps.dashboard]
assets = ["public/dashboard.css"]
styles = ["public/dashboard.css"]
```

Run `pytincture-build-browser --all`, or `--application dashboard`. Outputs are
`browser/orders/` and `browser/dashboard/`. Allowlist each bundle only for its
own application. Every app gets a separate source bundle and import graph.

### BFF and compatibility

Generated stubs keep the `await Data().method_async(...)` API. Named arguments,
keyword-only arguments, server-evaluated defaults, positional-only parameters,
variadic calls and parameterless GET methods are supported. Sessions and CSRF
still go through the existing server authorization. Async JSON requests have a 35-second browser timeout. Streaming calls remain
open until completion or explicit cancellation. External token methods are not exposed as session stubs.
Methods are also exposed under their original names: normal methods use
synchronous compatibility calls and async methods retain their awaitable API.
Streaming methods return portable async iterators; close them with `aclose()`
when stopping consumption early.

The compatibility layer adapts annotations, `create_proxy`, JSON-compatible
`to_js`/`to_py`, dictionary unpacking, UUID generation, exception formatting and
task scheduling. It includes the full dhxpyt wheel's Python wrappers and shipped
JavaScript/CSS, rather than a Book Library widget whitelist. Chat icon fonts are
served locally under the existing content policy. Loading every wrapper does
not mean every widget option has been exercised; the browser tests cover the
full Book Library and separate DOM/nested-layout/CardPanel/Kanban/Chat apps.

Browser dataclasses support required/default fields, factories, inheritance,
`__post_init__`, repr/equality, `init`, `kw_only`, `asdict`, `fields`, `replace`
and `is_dataclass`. Frozen/slots/order/hash options and `InitVar` are unsupported.
This is a documented subset, not the full CPython standard library. Custom
metaclasses, native packages such as NumPy/Pandas, structural pattern matching,
and BFF replay tokens still require the original Pyodide package runtime or
application changes. Synchronous BFF calls use blocking XMLHttpRequest for
compatibility; prefer async methods. MicroPython's own language
and standard-library differences also apply; see the
[upstream differences](https://docs.micropython.org/en/latest/genrst/core_language.html).

## Verification

```sh
python -m pytest tests/test_browser_build.py tests/test_browser_runtimes.py
# Test environment: playwright==1.63.0, Chromium, dhxpyt==0.9.19
PYTHONPATH=. python tests/browser_build_smoke.py
```

The browser check builds two independent applications without manually listing
imports/BFF modules, then runs both under MicroPython and Pyodide. It checks DOM
callbacks, inherited dataclasses, nested imports, BFF defaults/GET/variadic calls,
public assets, nested layout lifecycle and additional custom widgets. It rejects
console errors and verifies MicroPython requests no Pyodide files.

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

Optional per-app overrides use literals in the entry module:

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
The same generated full UI can run under either declared interpreter.

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

`host.js` exports `async function setup(context)`. Custom hosts can create UI;
the generated host only connects BFF and loads icons, leaving the UI to Python.
`context` contains:

- `engine`, `runtimes`, and `application`.
- `assetUrl(path)`: resolve a validated bundle-relative public asset URL.
- `invoke(name, payload)`: serialized calls to asynchronous Python callbacks or
  compiled JS callbacks. Interpreter payloads are strings; JSON is convenient
  for carrying records across the foreign-function interface. Await the promise
  or handle errors. Calls do not return converted Python objects in this version.
- `callBffSync(...)`: synchronous JSON compatibility call with the same CSRF
  checks.
- `streamBff(..., options)`: streamed BFF results (`raw: true` for raw UTF-8);
  returns an object with async `next()` and `close()`.
- `callBff(module, className, method, namedArguments)`: same-origin JSON POST,
  using session credentials and the configured CSRF cookie. Module paths may
  contain folders. It returns the parsed JSON response and rejects non-2xx
  responses. It does not implement generated-stub login redirects or replay tokens.

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

The shared builder includes the dhxpyt Python wrappers and async
session BFF stubs described above. CPython native extension wheels and replay-token handling have not been ported. Transcrypt cannot compile the full original example
unchanged and is not emitted by this builder. Deployments enabling BFF replay
tokens receive an explicit 422 for portable clients; the existing Pyodide
package path remains available.

Portable clients skip the Pyodide package installer, widget-wheel installer,
service-worker setup and cache warmup. This is part of the measured startup
benefit, alongside the smaller interpreter/compiler output. Compare engines
using equivalent client features and cold/warm network conditions.

Next work on this branch should expand `dhxpyt` compatibility, cover more widgets
and callback shapes, add runtime-specific diagnostics, and measure real devices
before proposing production support. Runtime adapters live in
`pytincture/frontend/browser-runtimes.js`; add another engine only after its
client contract and browser acceptance tests work.


### Full application validation

The branch has also been exercised with the current `pytincture_example` and
`wAwesomeChat`, using dhxpyt and wapyt respectively. The Wawesome UI uncovered
shared compiler/transport gaps beyond the example: widget asset manifests,
synchronous BFF methods, JSON/raw streaming, typing aliases, string title case,
and common `copy`/`datetime` imports. The latter are bundled from pinned
[micropython-lib sources](https://github.com/micropython/micropython-lib/tree/5139530d3327a6012921c70d44ae3151480efe2d/python-stdlib),
with their licenses and hashes. They implement MicroPython subsets, not the entire
CPython standard library.

Client code must keep server-only imports (database, environment files, provider
credentials) behind BFF methods. An async generator (`async def` containing
`yield`) is rejected at build time: use an async iterator with `__aiter__` and
`async __anext__`. Generated BFF streams already use that portable interface.
Prefer `datetime.now(timezone.utc)` over the unsupported `datetime.utcnow()`.

Wawesome validation uses a disposable authenticated database and a local
OpenAI-compatible streaming service through the real LiteLLM/BFF stack. Voice
validation records synthetic microphone audio using MediaRecorder and sends it
to real Whisper. This checks recording, transcription, VAD, auto-submit and
streaming without paid API calls or hardware permission prompts. It does not
certify real provider accounts or physical microphones. See the app's
`tests/browser_runtime_smoke.py` and `tests/browser_voice_smoke.py` for repeatable
interaction checks. The framework fixture checks run in CI for both runtimes.


Portable widgets can access `globalThis.pytinctureBrowserRuntime.engine` and
`.runtime` after interpreter initialization. `captureOutput(source)` executes a
short synchronous Python snippet in a fresh globals dictionary using that same
interpreter and returns its stdout, including Unicode and output without a final
newline. It restores output routing on exceptions. Input/output are limited to
1 MiB. This is a convenience for existing code-preview widgets, not an isolation
or security boundary; snippets have the same browser access as the application.
