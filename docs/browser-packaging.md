# Browser packaging and dynamic imports

Pytincture builds `appcode.pyt` from a conservative static graph. It includes:

- `<application>.py`;
- recursively reachable local `import x` and `from x import y` Python files;
- files selected by `PYTINCTURE_BROWSER_FILES`; and
- browser-safe stubs for decorated BFF modules.

The graph stops at a proven `@backend_for_frontend` module. That module is
included as a generated browser proxy, but imports reachable only through its
server implementation are not browser dependencies. A module independently
imported by browser code is still included through that independent edge.
Packages containing only BFF proxies use Python namespace-package behavior so
a server-side package initializer is not copied into the archive. Initializers
required by ordinary browser modules remain part of the browser graph.

It excludes virtual environments, caches, `node_modules`, build output, server
implementations, and unrelated files. Archive paths are relative, cannot
escape the module root, and follow the [appcode v1 contract](contracts/appcode-v1.md).

## Dynamic imports

Python cannot determine this target from syntax:

```python
import importlib
module_name = choose_module()
module = importlib.import_module(module_name)
```

Add every possible browser target explicitly:

```python
config = PytinctureConfig(
    modules_path="./apps",
    environment={
        "PYTINCTURE_BROWSER_FILES": '["plugins/*.py", "theme/*.css"]'
    },
)
```

Then dynamic imports work in Pyodide because the selected files are present in
the extracted archive. The BFF registry is separate: a dynamically selected
server module must still contain a statically discoverable
`@backend_for_frontend` class to receive a generated proxy. Dynamic importing
does not automatically make arbitrary server code callable or ship it to the
browser.

Static imports inside ordinary browser modules are conservative: imports under
`if`, `TYPE_CHECKING`, and other conditional syntax are included because the
packager does not execute application conditions. Imports inside a BFF module
remain behind the server boundary described above.

## Public assets

Images, fonts, media, CSS, and JavaScript assets may be served from
`/{application}/appcode/`, but only for an application that has a real
`<application>.py` entrypoint. An asset must also be selected by
`PYTINCTURE_BROWSER_FILES`, declared as that application's favicon, authorized
by `PYTINCTURE_PUBLIC_ASSET_PATHS`, or be the application's authorized widget
wheel. A filename extension alone never makes an arbitrary file public, and
Python source/bytecode cannot be exposed through this route even with a glob.

These direct asset URLs are deliberately unauthenticated. Do not place secrets
in their file set. `PYTINCTURE_BROWSER_FILES` and legacy list/comma-separated
`PYTINCTURE_PUBLIC_ASSET_PATHS` values are service-wide and therefore shared by
all real applications. Multi-application services can scope direct-only assets
with a JSON mapping; `*` explicitly declares shared files:

```json
{
  "reports": ["reports-assets/**"],
  "admin": ["admin-assets/**"],
  "*": ["shared/fonts/**"]
}
```

`PYTINCTURE_BROWSER_FILES` controls archive inclusion and also permits its
browser-safe asset types to be fetched directly.

Direct GET responses stream from no-follow file descriptors in bounded chunks,
with filesystem reads moved off the event loop. HEAD returns size, modification,
and ETag metadata without reading the asset body. Authorized widget-wheel GET
responses additionally compute and return `X-Pytincture-SHA256` incrementally;
the browser uses that value to create the hash-locked micropip URL. HEAD does
not hash or return the wheel digest.
`PYTINCTURE_PUBLIC_ASSET_PATHS` controls direct HTTP exposure only—use the
narrowest setting for each. SVG remains supported for declared application
assets and favicons, but its response receives a no-script sandbox CSP and
same-origin resource policy so direct navigation cannot become an application
script context.

Hidden paths and high-confidence secret material such as credential/secret
filenames, private-key containers, database files, and backups are rejected
even when a broad browser/public glob matches them. Import required browser
Python normally; do not use `PYTINCTURE_BROWSER_FILES` to move server
configuration into the browser boundary.

Browser Python and manifest-approved widget JavaScript are intentionally
trusted application code with same-origin authority. Pytincture constrains
which deployment-approved files enter that boundary; it does not sandbox that
code from its own application origin.

## Widget wheels

Declare literal metadata in a local imported module:

```python
__widgetset__ = "company_widgets"
__version__ = "2.4.1"
```

Pytincture reads local metadata through the AST without importing browser-only
code on the server. A root-level wheel is public only when its distribution
name and version match the application widgetset, or when its version equals
the explicit `PYTINCTURE_DEV_WHEEL_VERSION` fallback (`99.99.99` by default).
Other versions of the same distribution remain private. Backend wheel URLs
receive the server cache UUID; micropip package-index requests do not.

## Framework caching

When enabled, the service worker is scoped to one application and matches only
the bundled immutable Pytincture/Pyodide asset manifest. Its cache name includes
the application, runtime release, and service instance UUID. It never caches
appcode, BFF/auth responses, private or cookie-setting responses, or arbitrary
same-origin files, and it never deletes caches belonging to other applications
or libraries.

The runtime constructs UUID URLs only for same-origin framework/backend assets;
it does not replace global `fetch`. Package indexes, cross-origin APIs, and
presigned URLs remain unchanged.
