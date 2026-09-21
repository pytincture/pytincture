# Browser engines and application delivery

Pyodide with legacy package delivery remains the default. Upgrading the framework,
adding a manifest, or building a bundle does not change that loading path.
MicroPython is an opt-in target for the [portable Python profile](portable-python-profile.md).
It is not a transparent CPython replacement. Transcrypt is not supported.

| Engine | Delivery mode | Behavior |
|---|---|---|
| `pyodide` | `legacy-package` (default) | Existing wheel/archive installation, widget installation and application startup |
| `pyodide` | `portable-bundle` | Native CPython sources and resources from a prebuilt bundle; no browser package installation |
| `micropython` | `portable-bundle` | The same application's assets with validated, reported MicroPython source adaptations |
| `micropython` | `legacy-package` | Configuration error |

Portable Pyodide is the parity reference: compare it with legacy Pyodide before
comparing MicroPython. Portable Pyodide preserves CPython semantics; it does not
use the MicroPython dataclass, string or standard-library replacements.
Portable delivery is still experimental. There is no planned automatic migration
or silent runtime fallback.

## Configuration

For environment-configured services:

```sh
export PYTINCTURE_BROWSER_RUNTIME=pyodide
export PYTINCTURE_DELIVERY_MODE=portable-bundle
export PYTINCTURE_PUBLIC_ASSET_PATHS='{"orders":["browser/orders/*"]}'
```

Or construct an explicit configuration:

```python
app = create_app(PytinctureConfig(
    modules_path="./apps",
    browser_runtime="pyodide",
    delivery_mode="portable-bundle",
    environment={
        "PYTINCTURE_PUBLIC_ASSET_PATHS": '{"orders":["browser/orders/*"]}',
    },
))
```

Explicit `PytinctureConfig` construction uses its arguments, not implicit process
environment defaults. Use `PytinctureConfig.from_env()` for environment settings.

Per-application literals can override deployment defaults:

```python
APP_BROWSER_RUNTIME = "pyodide"
APP_DELIVERY_MODE = "portable-bundle"
APP_RUNTIME_MANIFEST = "browser/orders/manifest.json"  # optional conventional path
```

Equivalent `APP_CONFIG` keys are `browser_runtime`, `delivery_mode` and
`runtime_manifest`. **A manifest never selects delivery mode.** In legacy mode
it is ignored, including an obsolete or missing manifest. In portable mode both
engines use the conventional `browser/<application>/manifest.json` unless an
explicit location is supplied. Rebuild earlier experimental bundles for schema 2.

`PYTINCTURE_ALLOW_RUNTIME_SELECTION=true` / `allow_runtime_selection=True` is an
explicit development/testing flag, disabled by default. It enables
`/orders?runtime=micropython`; it does not change delivery mode. It is rejected
alongside production hardening settings `canonical_origin` or
`require_readonly_modules_path`. Do not enable it in production. Select the
production engine with app/server configuration and build that target.

Engine precedence is enabled query selection, application literal, then server
configuration. Unknown/unsupported engines or combinations return 422; disabled
query selection returns 400. Selection survives login redirects. Failures never
switch engines automatically.

## Build and inspect

Install the framework and the app's widgetset in the build environment. No npm
installation is required by application builders; the framework ships its pinned
MicroPython runtime. A `micropython-assets` override must contain exactly the
pinned runtime bytes; a different runtime needs a new compatibility profile. Add to the app's `pyproject.toml`:

```toml
[tool.pytincture.browser]
application = "orders"
modules-path = "apps"
runtimes = ["pyodide", "micropython"]
```

```sh
pytincture-build-browser --config pyproject.toml --check --compatibility-report compatibility.json
pytincture-build-browser --config pyproject.toml
pytincture-build-browser --inspect apps/browser/orders/manifest.json
```

`python -m pytincture.browser_build` is equivalent. The build analyzes both engines
independently, reports unsupported targets, and fails if any **requested** target
fails validation. Use `runtimes = ["pyodide"]` for CPython-only portable clients.
`--check` writes no bundle; an explicitly requested report is still written.
`--compatibility-report -` emits JSON to stdout. The build never executes app
modules. It follows local imports and stops at decorated BFF modules, replacing
them with stubs; backend bodies, imports and package initializers behind that
boundary stay on the server. Review public code and resources before publication.

The entrypoint is discovered using normal `APP_ENTRYPOINT`/MainWindow conventions.
Plain DOM apps can declare `entrypoint = "orders:main"` with `async def main()`.
JavaScript interaction still uses `import js`; a handwritten JavaScript app host
or replacement UI is unnecessary.

Optional settings:

```toml
[tool.pytincture.browser]
application = "orders"
modules-path = "apps"
entrypoint = "orders:OrdersWindow"
output = "browser/orders"
files = ["plugins/extra_view.py"]
dynamic-imports = ["plugins.extra_view"]
import-aliases = {app_registry = "browser.app_registry"}
bff = ["api/extra_data.py"]
wheels = ["vendor/helpers-1.0-py3-none-any.whl"]
resources = ["settings/defaults.json"]
assets = ["public/theme.css", "public/helpers.js", "public/fonts/ui.woff2"]
styles = ["public/theme.css"]
scripts = ["public/helpers.js"]
required-browser-apis = ["navigator.mediaDevices.getUserMedia", "MediaRecorder"]
heap-bytes = 16777216
# Optional; otherwise use the installed widget distribution:
widget-package = "wapyt"
widget-wheel = "vendor/wapyt-0.1.0-py3-none-any.whl"
```

Wheel paths are relative to the TOML file; files, resources, assets and output are
relative to `modules-path`. Scripts/styles must be included in `assets`.
`discover-imports = false` requires an explicit complete module/BFF inventory.
Shared settings with `[tool.pytincture.browser.apps.orders]` and
`[tool.pytincture.browser.apps.dashboard]` support `--application dashboard` or
`--all`; each app has a separate graph and output. Publish each only to its own
public asset allowlist. Rebuild after changing sources, dependencies, assets or
BFF signatures. Server startup does not build bundles.

`import-aliases` selects an explicitly supplied browser implementation without
editing application imports. For example, `import app_registry` uses
`browser/app_registry.py` under the original `app_registry` module identity;
the original computed/server registry is not bundled or evaluated. Package aliases
also cover their children. Relative imports inside a substitute keep the
implementation's package context. Targets must exist under `modules-path`;
cycles and escaping paths fail the build. Each applied substitution appears in
the compatibility report. This does not enable unrestricted computed imports.

For a computed module name, declare every permitted result, for example
`dynamic-imports = ["providers.demo"]`. A call such as
`importlib.import_module(module_name)` then checks its resolved module name against
that exact allowlist at runtime in both portable engines. Other results raise
`ImportError` before loading the module. Declared local modules and their imports
are included in the bundle; simply including a file does not authorize dynamic
access to it. This adaptation is recorded in the compatibility report.

Legacy Pyodide discovers imported/indirect MainWindow subclasses and aliases in
the browser when static discovery cannot decide, without executing client code
on the server. Explicit entrypoints may refer to imported or simple aliases.
Its application archive now includes discovered installed pure-Python dependency
files, package data and metadata, including `python-dotenv`, without runtime PyPI
downloads for those dependencies. BFF implementation imports stay server-side;
hidden/environment files, native-extension distributions and widget packages are
excluded from this dependency path. Existing appcode limits still apply. Archives
with installed dependencies currently bypass the in-memory appcode cache so an
updated dependency cannot leave stale source bytes in that cache. Prebuilt archives
remain immutable snapshots and should be rebuilt after dependency changes.

## Resources, integrity and CSP

A schema-2 manifest references an immutable `releases/<sha256>/` directory. Its
identifier covers the canonical manifest and every artifact's size/SHA-256 lock.
The builder writes the revision before atomically replacing `manifest.json`.
Identical inputs produce identical revisions; existing revisions cannot be
rewritten with different bytes. Deploy the pointer and revision together and keep
old revisions until cached pages no longer need them. Deployment tooling controls
retention; the builder does not delete old revisions.

The inventory includes Python source sets, JSON/package data, JavaScript, CSS,
fonts, images, host code, licenses, runtime requirements, and build/report metadata.
`--inspect` independently verifies the complete inventory. Browser startup verifies
required files before execution and uses SRI on script/style elements. Pyodide
skips MicroPython's WASM and sources, and vice versa. Both skip build/report files
at startup. The content address provides integrity, not publisher authentication;
serve artifacts from your trusted application origin without rewriting revisions.

Package data from pure Python wheels is included automatically. Pyodide mounts it
into its real filesystem for native `importlib.resources`; MicroPython exposes the
documented resource subset over verified in-memory bytes. Explicit assets can be
addressed using `js.pytinctureAssetUrl("assets/public/helpers.js")`.

Assets are local by default. Missing CSS-relative images/fonts/styles fail the
build. Detected active external dependencies also fail unless deliberately listed
in `[tool.pytincture.browser.external-origins]`, with separate `script`, `style`,
`font`, `connect` or `image` lists of exact HTTPS origins. The report identifies
required origins and warns about external bytes outside the bundle's integrity
coverage. Bundle those bytes locally for immutable, fully locked delivery.
Computed JavaScript URLs need browser conformance testing; static analysis cannot
prove every possible dependency.

The build report does not loosen server CSP. If intentionally using a CDN, configure
its respective directive separately:

```sh
export PYTINCTURE_BROWSER_SCRIPT_ORIGINS='["https://scripts.example.com"]'
export PYTINCTURE_BROWSER_STYLE_ORIGINS='["https://styles.example.com"]'
export PYTINCTURE_BROWSER_FONT_ORIGINS='["https://fonts.example.com"]'
export PYTINCTURE_BROWSER_CONNECT_ORIGINS='["https://api.example.com"]'
```

Object equivalents are `browser_script_origins`, `browser_style_origins`,
`browser_font_origins` and `browser_connect_origins`. Script/style/font origins
must be exact HTTPS origins, without paths, credentials or wildcards. Connect also
supports exact WSS origins. Style permission never authorizes the stylesheet's
font origin. Existing default CSP remains in effect. There is no automatic image
CSP widening; prefer local images.

## Widgetset asset ownership

Portable delivery loads the widget manifest's ordered JS/CSS once, then publishes
`globalThis.pytinctureAssets`:

- `isPackageReady(packageName)` confirms the entire declared asset list loaded.
- `isLoaded(pathOrAbsoluteUrl, optionalSha256)` checks an individual asset.
- `getInfo()` returns a read-only owner, bundle identifier, packages and asset list.

A widget's `pytincture-assets.json` can declare:

```json
"asset_loader": {"module": "mywidgets._runtime", "function": "use_preloaded_assets"}
```

The hook must be a top-level synchronous function callable without arguments.
The builder validates the hook and invokes it before importing the app entrypoint,
after assets and package sources are installed. The hook should check
`js.pytinctureAssets.isPackageReady("mywidgets")`, mark its loader ready, and avoid
reloading owned assets even when a component is missing. A missing component should
raise a useful manifest error. Keep the existing self-loader for legacy/standalone
use, where the portable registry is absent. Package initializers must not construct
widgets before this hook can run. Existing widgetsets that already honor loaded
constructors can use the registry without declaring a hook.

The hook is optional for existing Widgetsets. When building a portable bundle,
Pytincture routes widget Python `import js` and named `from js import ...` bindings
through a widget-scoped browser bridge. The bridge suppresses repeated `eval` of
already-loaded script bytes and repeated DOM insertion of matching styles or
owned script/stylesheet URLs. It preserves loader completion events, and unrelated
JavaScript/CSS continues normally. Application JS imports and global browser APIs
are unchanged. The build report identifies this adaptation in both engines.

The bridge also covers named `from pyodide.code import run_js` imports (including
aliases). For older Widgetsets with top-level `_try_inject_inter_fonts()` or
`_try_inject_icon_fonts()` helpers, the builder adds an early package-readiness
check inside each helper. This skips resource reading, Base64 encoding and CSS
construction before they block the interpreter. The original helper runs when
that package is not ready. The `widget-font-ownership` transformation is reported
per function for both portable engines; it is never applied to application code.
A ready package means its declared manifest assets loaded successfully. Its
manifest must include the font CSS and dependencies these helpers would supply.
Different initializer names still need the explicit readiness check or ownership
hook; Pytincture does not guess which arbitrary functions are safe to skip.

New `resources.json` files use `format = "asset-references-v1"` and reference the
already-verified `vendor/` assets. They no longer transfer a second Base64 copy of
fonts, scripts or package data. Both interpreters still receive resource files,
and MicroPython resource reads convert bytes to Base64 only on demand. This is
not a lazy filesystem or a guarantee that a particular heap will fit an app.
Rebuild and serve the matching framework browser runtime with new bundles. The
updated loader also accepts older inline-Base64 resource bundles.

This supports conventional old resource-based loaders without a Widgetset source
change. Loaders that rewrite asset contents, use star imports from `js`, or obtain
browser APIs through an unrelated bridge still need the explicit ownership hook
or their own readiness check. This compatibility bridge applies to portable
delivery; legacy widget loading retains its existing path.

## Runtime identity and startup timing

Use the supported read-only API rather than internal interpreter handles:

```javascript
const info = globalThis.pytinctureRuntime.getInfo();
// schema, engine, deliveryMode, runtimeVersion, pythonImplementation,
// pythonVersion, bundleId, compatibilityProfile, startupTimings
window.addEventListener("pytincture:lifecycle", event => console.log(event.detail));
```

The body exposes `data-browser-runtime` and `data-delivery-mode`. Versions come
from the running interpreter; unavailable values are null. Legacy mode has null
bundle/profile identifiers. Each timing has a stage, duration in milliseconds,
resource, cache status and completion/failure status. Lifecycle events also carry
engine, delivery mode, timestamp and request identifier. Resource queries are
sanitized. Cache status is `network`, `cache` or `unknown` when browser timing
cannot establish it.

Phases distinguish runtime download/initialization, legacy package and widget
installation, assets, bundle download/installation, module import, entrypoint and
`first-render`. Parent phases include child durations; do not sum both. Runtime
initialization can itself download WASM and stdlib resources. `first-render` is the
first frame opportunity after entrypoint completion (bounded in background tabs),
not proof that arbitrary background tasks have rendered. Await required startup
work in an async entrypoint and verify actual app DOM in conformance tests.

## Validation and rollout

Validate legacy Pyodide first, then portable Pyodide, then portable MicroPython.
Compare screenshots and behavior using identical app inputs. Do not use one
startup measurement as a general performance guarantee; capture cold/warm timing,
network/cache conditions, runtime identity and the first useful app DOM.

The CI conformance fixtures cover direct DOM, BFF sync/async/streaming, nested
widgets, CodeMirror, modal persistence, fonts, CSP, runtime identity and screenshots.
`tests/run_application_conformance.py` runs the complete example and Wawesome apps
in all three supported combinations, including authentication and voice. See
[validation evidence and boundaries](browser-runtime-validation.md) for commands,
versions and the exact features exercised. Legacy delivery has not been deprecated.

`PYTHONPATH=. python tests/browser_compatibility_smoke.py` exercises compatibility
regressions in real Chromium: substituted computed registries, browser-local
environment defaults, collection unpacking, callbacks, and an unchanged old-style
Widgetset without an ownership hook on both portable engines. It also checks
legacy Pyodide with installed `python-dotenv` and an imported alias of an indirect
MainWindow subclass. CI runs this alongside the existing portable fixtures.
These self-contained cases reproduce reported private-app patterns; they do not
claim validation of an unavailable client application.


## Upgrading portable applications to RC9

Legacy Pyodide remains the default and requires no runtime-selection change.
For portable applications, rebuild the bundle and serve the RC9 framework/browser
runtime together. RC9 resource manifests reference verified asset bytes instead
of duplicating them as Base64; older loaders cannot read this new representation.
The updated loader continues accepting older inline-Base64 bundles.

Remove temporary substitutes for the standard-library and FFI features now
provided by the framework before testing those implementations. Keep explicit
substitutes for application-specific server-only modules. New bundles use portable
profile 2: `uuid4()` returns a UUID object, so use `str(uuid4())` where a string is
required. Existing profile-1 bundles keep their own embedded implementation.
Nested f-strings now run through the MicroPython compatibility transformation;
no application rewrite is needed. See [the portable profile](portable-python-profile.md)
for the supported API subsets and per-runtime build report.
