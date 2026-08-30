# Browser packaging and dynamic imports

Pytincture builds `appcode.pyt` from a conservative static graph. It includes:

- `<application>.py`;
- recursively reachable local `import x` and `from x import y` Python files;
- files selected by `PYTINCTURE_BROWSER_FILES`; and
- browser-safe stubs for decorated BFF modules.

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

## Public assets

Images, fonts, media, CSS, and JavaScript assets may be served from
`/{application}/appcode/`. Extend the allowlist with
`PYTINCTURE_PUBLIC_ASSET_PATHS`; Python and configuration files stay denied.
`PYTINCTURE_BROWSER_FILES` controls archive inclusion, while
`PYTINCTURE_PUBLIC_ASSET_PATHS` controls direct HTTP exposure—use the narrowest
setting for each.

## Widget wheels

Declare literal metadata in a local imported module:

```python
__widgetset__ = "company_widgets"
__version__ = "2.4.1"
```

Pytincture reads local metadata through the AST without importing browser-only
code on the server. A root-level wheel is public only when its distribution
name and version match the application widgetset. Backend wheel URLs receive
the server cache UUID; micropip package-index requests do not.

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
