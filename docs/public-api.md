# Public API contract

This document defines the API surface Pytincture intends to stabilize for
1.0. The machine-readable inventory is
[`contracts/public-api-v1.json`](../contracts/public-api-v1.json). CI checks
that the inventory still matches the implementation.

The contract becomes a semantic-versioning commitment with 1.0. During the
remaining 0.x releases, incompatible corrections remain possible, but the
project will use the deprecation process whenever practical.

## Python API

### Service launcher

Import these names from `pytincture`:

- `PytinctureConfig` is the typed, validated configuration for one ASGI app.
- `create_app(config=None)` creates an isolated FastAPI application. It accepts
  a `PytinctureConfig`, a mapping of field overrides, or the process
  environment when omitted.
- `__version__` reports the installed framework/runtime release.
- `launch_service(modules_folder, port=8070, ssl_keyfile=None,
  ssl_certfile=None, env_vars=None, bff_docs_path="/bff-docs",
  bff_docs_title="pyTincture BFF API", default_application=None,
  favicon_folder=None, host=None)` starts the Pytincture service. The existing call forms
  remain supported when the planned application factory is introduced.
- `set_modules_path(path)` selects the application module root for the current
  process and synchronizes `MODULES_PATH`.
- `get_modules_path()` returns the selected module root, then `MODULES_PATH`,
  then the current directory.

`launch_service()` is the supported production convenience launcher. The
lower-level `main()` helper and process-management implementation are not
public APIs.

The launcher defaults to `0.0.0.0` for normal services. When development
email login is active, an omitted `host` safely defaults to `127.0.0.1`, and
an explicitly routable host is rejected before the server process starts.

### BFF declarations

Import these decorators from `pytincture.dataclass`:

- `@backend_for_frontend` exports public methods and public attributes from a
  class and produces the browser-side proxy.
- `@bff_http_methods(*methods)` opts a method into one or more of `GET`,
  `POST`, `PUT`, `PATCH`, and `DELETE`. Undeclared methods use `POST`.
- `@bff_policy(**metadata)` attaches literal application-defined metadata for
  the server-side policy hook.
- `@bff_stream(raw=False, media_type="text/event-stream")` marks a method as
  streaming. Non-raw values use newline-delimited JSON framing.

Decorated classes may accept `_user` in their constructor. If they do not,
Pytincture attaches `_user` to the wrapped instance after construction. Names
beginning with `_` are never exported as BFF operations.

### Runtime hooks

The following hooks currently live in `pytincture.backend.app` and remain
supported while the typed configuration/application-factory API is developed:

- `set_bff_policy_hook(hook)` installs or clears the sync/async authorization
  hook invoked before a BFF operation.
- `set_user_authenticator(authenticator)` installs or clears the sync/async
  local-user authenticator.
- `revoke_session(session_id)` revokes a session in the configured revocation
  store.

The hooks will gain a configuration-object home in a later roadmap phase. A
compatibility import or migration period will precede removal of these paths.

## JavaScript API

Pytincture exposes one function on `window`:

- `runTinctureApp(config)` starts one packaged or inline browser application
  and returns a promise that resolves after the entrypoint starts or rejects
  with a `PytinctureLifecycleError`.

`window.PytinctureLifecycleError` exposes the stable `stage`, `code`,
`resource`, `requestId`, `correlationId`, and sanitized `rootCause` fields.

The legacy positional form `runTinctureApp(application, widgetlib,
entrypoint)` remains supported through 1.x but object configuration is the
recommended form.

Two pre-load globals are public:

- `window.pytinctureAutoStartConfig` supplies object-form configuration for an
  inline application discovered at DOM ready.
- `window.pytinctureAutoStartDisabled = true` disables automatic inline start.

### Runtime configuration keys

| Key | Default | Meaning |
| --- | --- | --- |
| `application` | `null` | Service application route/name. |
| `entrypoint` | application | Python class or callable to start. |
| `widgetlib` | `"dhxpyt"` | PyPI package/specifier for the widgetset. |
| `widgetSource` | `null` | Explicit micropip source; disables backend fallback. |
| `requestUuid` | generated | Cache namespace; service mode supplies one per server process. |
| `mode` | `"auto"` | `"package"`, `"inline"`, or automatic selection. |
| `onLifecycleEvent` | `null` | Callback for stage, compatibility, fallback, error, and ready events. |
| `pyodideBaseUrl` | bundled path | Trailing-slash base for Pyodide assets. |
| `loadMaterialIcons` | `true` | Load the Material Design icon stylesheet. |
| `materialIconsUrl` | CDN URL | Icon stylesheet source. |
| `enableBackendLogging` | service-dependent | Forward sanitized console messages to the backend. |
| `logEndpoint` | `"/logs"` | Backend browser-log endpoint. |
| `inlineSelector` | Python script selector | Locates inline Python blocks. |
| `libsSelector` | `"#micropip-libs"` | Locates the JSON list of extra micropip packages. |
| `devWidgetHost` | page origin | Backend host used for widget-wheel fallback. |
| `devWheelVersion` | `"99.99.99"` | Final development-wheel fallback version. |
| `enableServiceWorker` | `false` | Register the runtime service worker. |
| `serviceWorkerUrl` | `"sw.js"` | Service-worker script URL. |
| `serviceWorkerScope` | `"./"` | Service-worker scope. |
| `warmPyodideCache` | `true` | Preload Pyodide assets when caching is enabled. |
| `showLoadingOverlay` | `true` | Render startup progress. |
| `loadingOverlayId` | `"pytincture-loading"` | Loading overlay DOM id. |
| `loadingTitle` | `"Starting PyTincture"` | Loading overlay title. |

New optional keys may be added in a minor release. Existing keys will not be
removed or change meaning during 1.x without following the deprecation policy.

## Internal and provisional surfaces

Anything not listed in the contract fixture is internal unless another
contract document explicitly says otherwise. In particular, the following are
not compatibility promises:

- underscore-prefixed functions, variables, generated-stub helpers, and
  middleware classes;
- the module-global `pytincture.backend.app.app` object and route function
  names (the documented HTTP contracts remain supported);
- registry dictionaries and registry reload helpers;
- generated bundle internals and helper functions other than
  `runTinctureApp()`; and
- exact log text, HTML markup, CSS class names, or archive compression details.
