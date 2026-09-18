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
  bff_docs_title="", default_application=None,
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
  class and produces the browser-side proxy. Swagger includes session-based
  methods by default in explicit development mode and hides them otherwise.
  `@backend_for_frontend(include_session_methods_in_docs=True)` or `False`
  overrides that default for the class.
- `@bff_external` (or `@bff_external()`) marks a method for the external API.
  It appears in its module's Swagger page and accepts external-scoped tokens.
  It never grants anonymous access; normal authenticated app sessions still work.
- `@bff_http_methods(*methods)` opts a method into one or more of `GET`,
  `POST`, `PUT`, `PATCH`, and `DELETE`. Undeclared methods use `POST`; GET is a
  parameterless, read-only, repeatable operation contract.
- `@bff_policy(**metadata)` attaches literal policy metadata. A server-side
  hook is required at startup; standard audience/provider/issuer/tenant/role/
  operation predicates are enforced before the hook.
- `@bff_stream(raw=False, media_type="text/event-stream")` marks a method as
  streaming. Non-raw values use newline-delimited JSON framing.

The static authorization registry proves these declarations came directly
from `pytincture.dataclass`. Direct aliases and qualified module aliases are
supported; local functions, unrelated imports, re-exported wrappers, and names
rebound after import are intentionally ignored even when their names match.

Decorated classes may accept `_user` in their constructor. If they do not,
Pytincture attaches `_user` to the wrapped instance after construction. Names
beginning with `_` are never exported as BFF operations.

Every generated BFF module also exposes `PytinctureBFFError`. Sync, async, and
streaming proxies raise it for non-2xx responses other than the established 401
login redirect. Its stable public fields are `status_code`, `status`,
`operation`, and `correlation_id`; response bodies are never copied into the
error.

Generated asynchronous calls attach browser cancellation to their bounded
wait. A timeout or Python task cancellation aborts the underlying fetch, and a
stream reader is cancelled when iteration completes or its iterator is closed.
This cleanup does not change chunk framing, `yield`, or the synchronous API.

### BFF documentation

Open `/{application}/{module}/bff-docs` to see one module's documented BFF
methods, Python signatures, named request examples and response schemas.
Use an extensionless module path, including its folders: for example,
`/library/services/catalog/bff-docs` documents `services/catalog.py` within the
`library` application's dependencies. The schema is at the same URL followed
by `/openapi.json`. Undocumented, unknown and unrelated modules return 404.

Swagger's server/base URL is `/{application}/classcall/{module}` and operations
are displayed as `/ClassName/method`. The application and module appear once
in the base URL; no `.py` suffix is used in documented paths or tags. Both
extensionless and historical `.py` class-call URLs continue to work, including
nested modules. `/{application}/bff-docs`, global `/bff-docs`, `/docs`, `/redoc`
and all aggregate OpenAPI URLs return 404 in every docs mode.

 Send the actual function arguments as a JSON object, for example
`{"page": 1, "page_size": 2}`. Swagger documents this format directly.
The server normalizes named fields before running the existing signature,
authorization, and type checks. A variadic positional parameter is supplied as
an array under its declared name; extra fields bind to `**kwargs` when declared.

Generated clients can continue using `{"args": [], "kwargs": {...}}`.
An object containing exactly `args` (an array) and `kwargs` (an object) is
recognized as the legacy envelope. If a method itself declares both of those
names, use the envelope to disambiguate that pair of values.

Add parameter and return type annotations to describe JSON types. Built-in
types, lists, dictionaries, tuples, unions, `Optional`, `Literal`, and
module-local `TypedDict` classes (including nested fields and inheritance)
produce structured schemas. Undeclared or unresolved shapes remain unknown.
Type annotations also participate in the existing BFF argument validation;
choose annotations that describe the inputs your method accepts.

Documentation is generated statically, without importing modules or invoking
methods. Server default values stay redacted. Response examples are clearly
labelled as illustrative; **Try it out → Execute** performs a real call and
shows its actual response. The **API access** panel signs in directly using the
existing email/password login controls (including login CSRF, throttling and
password verification). Its heading uses the application documentation title, and
it displays the same configured `LOGIN_HELP_TEXT` as the regular login page. For Google, Microsoft or SAML, use **Other sign-in
options**, then **Refresh sign-in status**. Browser sessions work automatically;
Swagger supplies the CSRF header for same-origin BFF writes. A raw session ID
is not a credential and is never needed in Swagger.

The page and schema title use `APP_TITLE` / `APP_LOADING_TITLE` or
`APP_CONFIG["title"]` / `APP_CONFIG["loading_title"]` from the application
entrypoint, followed by `API`. Without a title, the application name is used
(with underscores replaced by spaces). `BFF_DOCS_TITLE` explicitly overrides
the complete title. No framework branding is added.

To disable documentation in production, set
`PytinctureConfig(api_docs_mode="disabled", ...)`. Environment-based startup
(`create_app()` or `PytinctureConfig.from_env()`) also accepts
`PYTINCTURE_API_DOCS_MODE=disabled`. If constructing a config explicitly,
pass the field explicitly or read the environment into it. This disables
both Swagger pages and OpenAPI schemas, including every module-specific
route, without disabling
BFF calls. The `authenticated` mode instead requires a valid login to read docs.

### External methods and API tokens

For applications that need their own credentials without a user login, see
[application API clients](api-clients.md). Clients receive grants against exact
application/module/class/method identifiers; no additional permission names or
decorator arguments are needed. User-session tokens below remain supported.

Declare external access beside the method, with no environment-variable
allowlist. For `services/catalog.py`:

```python
from pytincture.dataclass import backend_for_frontend, bff_external

@backend_for_frontend
class Catalog:
    @bff_external
    def search(self, query: str) -> list[str]:
        return [query]

    def refresh_index(self) -> bool:
        return True
```

Outside development mode, `/library/services/catalog/bff-docs` shows `search`
and hides `refresh_index`. In explicit development mode it shows both.
`include_session_methods_in_docs` defaults to `None`, which inherits the service
mode. Explicit `True` includes session-based methods and explicit `False` hides
them, regardless of the mode. Development here means either
`PytinctureConfig(allow_development_auth_origin=True, ...)` or
`PytinctureConfig(enable_dev_email_login=True, ...)`, using the existing
loopback-only development controls. Merely visiting localhost does not enable
this default. That flag only controls documentation: it does not authorize
external-scoped tokens to call `refresh_index`. Both methods remain callable
from the app through its authenticated session. `@bff_external` requires a
valid API token for cookie-free callers, or an authenticated browser session
with the normal CSRF/replay protections; it rejects anonymous calls even if a
legacy anonymous allowlist names the same method. It also rejects anonymous
calls in a service with no login providers. The method's BFF policies still run,
and the issuer's identity is available as `_user`.

Enable token issuance in the service's existing authenticated configuration:

```python
config = PytinctureConfig(
    modules_path="./app",
    enable_bff_api_tokens=True,
    api_docs_mode="public",  # or authenticated / disabled
    # Add your existing authentication configuration here.
)
```

The default `api_docs_scope="all"` honors each class's `include_session_methods_in_docs` flag.
`api_docs_scope="public"` is an optional service-wide restriction: show external
methods and legacy public methods only, even if a class requests `include_session_methods_in_docs=True`.
If nothing is visible in a module, its docs and schema return 404.

`ALLOWED_NOAUTH_CLASSCALLS` remains supported for existing anonymous APIs, but
is not required for `@bff_external`. Matching application, module, class and
method is exact, including folders. Services with no login providers still
expose ordinary BFF methods anonymously; the external decorator explicitly
requires credentials. Decorators and literal boolean/`None` documentation flags are discovered
statically without importing application code; unrelated or shadowed decorators
do not grant external access.

With `enable_bff_api_tokens=True`, sign in in **API access**, choose the token
scope and click **Generate API token**. Swagger fills its **Authorize** bearer
credential automatically, shows the token for copying and uses it for Execute.
The token is kept in memory, not local storage; reload or **Clear token** removes
it from the page (neither revokes a copy already issued).

- **External methods only** (`scope=external`): usable only on `@bff_external`
  methods or legacy public methods for that application. It cannot call
  session-only methods, even when issued by a privileged user or when
  `include_session_methods_in_docs=True`. Current source declarations are checked on each request.
- **Methods allowed by my sign-in**: carries the issuer's authenticated identity
  for that application; normal admission and BFF policies still apply. This
  scope is offered when the module docs include session-based methods.
  Hiding it in external-only docs does not remove a signed-in user's existing
  ability to delegate their own permissions.

Tokens expire after at most 15 minutes, or earlier if the originating session
expires. Issuance requires a real authenticated browser session and its CSRF
header; anonymous visitors cannot mint access credentials. Tokens are signed,
not encrypted, and contain the issuer's session identity, so treat them as
credentials. They are not OAuth/MCP JWTs and cannot authenticate other framework
endpoints. Shared session revocation, when configured, revokes delegated tokens
as well. Without a shared revocation store, issued copies remain usable until
expiry; removing a token from Swagger or logging out locally is not revocation.
Tokens use the service signing key with a separate purpose/salt; key rotation
invalidates them.

For non-browser clients:

```sh
curl -X POST 'https://your-app.example/library/classcall/services/catalog/Catalog/search' \
  -H "Authorization: Bearer $BFF_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"books"}'
```

Programmatic issuance uses the existing login flow: GET then POST
`/{application}/auth/mcp` with email, password and the returned
`login_csrf_token`, preserving cookies. POST `/{application}/auth/bff-token`
with `{"scope":"external"}` or `{"scope":"session"}`, those cookies and
`X-CSRF-Token` from the configured readable CSRF cookie. The response contains
`access_token`, `token_type`, `application`, `scope` and `expires_in`.
The old `scope=public` spelling is accepted with the same restricted access.
GET `/{application}/auth/bff-token` reports sign-in status and enabled features.
Token issuance returns 404 unless explicitly enabled and remains independent
of whether Swagger is disabled in production.

For legacy allowlisted methods only, anonymous calls remain the default: an optional token
does not turn a public method into a protected method. To require credentials,
set `require_public_bff_token=True` (`REQUIRE_PUBLIC_BFF_TOKEN=true`) alongside
`enable_bff_api_tokens=True` and a configured login provider. Then public methods
require a valid API token **or** authenticated browser session; ordinary generated
app clients continue working. Browser sessions retain CSRF and optional replay
proof checks. Explicit bearer calls use the bearer credential instead of those
browser-only proofs; signature, expiry, application scope, origin, admission,
input validation and BFF policy checks still apply. Invalid bearer credentials
fail rather than falling back to cookies or anonymous access.

`@bff_external` methods always require credentials; they do not need the legacy
`require_public_bff_token` switch. For environment-based startup the equivalent settings are
`PYTINCTURE_API_DOCS_SCOPE=public`, `ENABLE_BFF_API_TOKENS=true` and, optionally,
`REQUIRE_PUBLIC_BFF_TOKEN=true`. Explicit `PytinctureConfig(...)` construction
must pass these fields explicitly or read the environment into them.

### Runtime hooks

The following hooks currently live in `pytincture.backend.app` and remain
supported while the typed configuration/application-factory API is developed:

- `set_bff_policy_hook(hook)` installs or clears the sync/async authorization
  hook invoked before a BFF operation. `True` and `None` allow, `False` denies,
  and other return values fail closed.
- `set_bff_replay_token_store(store)` installs a provider implementing
  `pytincture.backend.replay.AtomicReplayStore` for the optional BFF request-
  proof feature. Providers explicitly declare whether consumption is atomic
  across workers; strict shared mode fails startup for a local-only provider.
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

When backend console forwarding is enabled, the browser sends only a bounded
diagnostic summary. Sensitive keys and token-like string values are redacted,
object traversal is depth/entry limited, cycles are replaced, and the final
message is capped at 800 characters before the request is created. This is
defense in depth: application code must not write credentials or secrets to the
browser console, and deployments may set `enableBackendLogging: false`.
Service pages enable forwarding only when the backend `/logs` route is
available. It is available for authenticated services by default; no-auth
services require the explicit `ALLOW_NOAUTH_BROWSER_LOGS=true` opt-in. The
backend enforces an exact schema, request/message bounds, CSRF/origin controls
where applicable, and a dedicated per-peer rate limit, and records only
structured metadata rather than the message text.

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
| `widgetlib` | `"dhxpyt==0.9.18"` | Exact PyPI package/version for the widgetset. |
| `widgetSource` | `null` | Explicit `#sha256=`-locked wheel URL; disables backend fallback. |
| `widgetAssetManifest` | `null` | Optional hashed asset manifest for a controlled legacy widget wheel. |
| `backendWidgetSources` | service metadata | Existing deployment-owned backend wheel URLs. Generated service pages supply this; standalone owners normally leave it unset. |
| `allowPublicWidgetIndex` | standalone: `true`; service: backend policy | Permit an exact custom widget package pin to use PyPI only after backend-wheel resolution. Hosted pages enable it only for specs in `PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST`. |
| `requestUuid` | generated | Cache namespace; service mode supplies one per server process. |
| `csrfCookieName` | page protocol | Exact framework CSRF cookie selected by hosted runtime metadata. |
| `mode` | `"auto"` | `"package"`, `"inline"`, or automatic selection. |
| `onLifecycleEvent` | `null` | Callback for stage, compatibility, fallback, error, and ready events. |
| `pyodideBaseUrl` | bundled path | Trailing-slash base for Pyodide assets. |
| `pyodideScriptIntegrity` | `null` | Required `pyodide.js`/`pyodide.asm.js` SRI map when `pyodideBaseUrl` is cross-origin. |
| `allowUnverifiedExternalPyodide` | `false` | Explicit demo/development opt-in for cross-origin Pyodide; production uses the self-hosted verified default. |
| `loadMaterialIcons` | `true` | Load the Material Design icon stylesheet. |
| `materialIconsUrl` | bundled path | Self-hosted Material Design icon stylesheet source. |
| `materialIconsIntegrity` | `null` | Required SRI value when `materialIconsUrl` is cross-origin. |
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
