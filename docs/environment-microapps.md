# Manifest-driven environment bundles (experimental, unreleased)

This feature is on the development branch after 1.0.2. It is not in the published
1.0.2 wheel. Install a wheel built from this branch with the `microapps-s3` extra
(or `microapps` for the local object-store adapter).

An environment's S3 manifest selects installed apps from ordinary, versioned
Python wheels copied to S3. On startup Pytincture compares that selection with the
active browser bundle's inventory. An exact match reuses the bundle; a different
selection builds and verifies a replacement before ASGI startup completes. Wheels
merely present in the folder, and entries with `installed: false`, are ignored.
The service never lists the bucket to decide what is installed.

The precompiled Pyodide WASM engine remains unchanged. Pytincture builds an
aggregate ZIP of browser Python code and resources loaded alongside that engine.
An optional base UI wheel puts the platform UI and installed microapps in that
same ZIP. Without a base wheel, the existing platform archive still loads normally
and the aggregate supplies its microapps. Existing `create_app()`, FastAPI,
app-folder, and opt-in Lambda deployment paths keep their defaults.

```mermaid
sequenceDiagram
    participant P as Portal / approval API
    participant S as Private S3 environment prefix
    participant E as Pytincture ECS replicas
    participant B as Existing browser
    P->>S: Approved wheels, immutable keys
    P->>E: Install approved app/version
    E->>S: Conditional manifest update (desired selection)
    E-->>P: 202 accepted after persistence
    E->>S: Acquire renewable, fenced build lease
    E->>E: Verify wheels, start versioned BFFs, build aggregate ZIP
    E->>S: Upload immutable ZIP; atomically replace active pointer
    Note over E,S: Other replicas reconcile their local BFFs and bundle pointer
    B->>E: Any normal BFF call
    E-->>B: Original result + bundle revision headers
    B->>E: Refresh authorized catalog
    B->>S: Fetch missing browser wheel (optional direct S3)
    B->>B: Verify and load into existing Pyodide interpreter
    Note over B: New clients receive the consolidated aggregate ZIP
```

## Wheel and manifest layout

Use a private, environment-scoped prefix, for example:

```text
s3://company-apps/environments/dev-42/
  environment.json
  wheels/tenzyn_shell-1.0.0-py3-none-any.whl
  wheels/orders_backend-2.3.0-py3-none-any.whl
  wheels/orders_ui-2.3.0-py3-none-any.whl
  wheels/reports_ui-0.8.0-py3-none-any.whl   # available, not installed
  bundles/dev-42/<selection>/<sha256>/environment.zip
```

These are standard wheels with standard filenames and distribution metadata,
not a new archive format. The backend wheel also needs Pytincture's
[packaged-app contract](packaged-microapps.md). The browser wheel contains only
browser-safe code/resources and a uniquely named Python entrypoint such as
`orders_ui:mount`. Backend and UI distribution names may differ; their versions
must match the selected app version. The filename, `METADATA` version, Python tags,
`Requires-Python`, and complete SHA-256 are checked before use.

Current scope is pure Python on Pyodide 0.29.3. Browser wheels must be
self-contained: vendor dependencies into unique namespaces during their build.
Backend dependency wheels are explicitly listed and hashed in `dependencies`.
There is no package-index lookup or automatic dependency resolver at startup.
Arbitrary native PyPI wheels are not supported. See the
[live wheel contract](live-microapps.md#artifact-contract) for worker boundaries
and mount/unmount hooks.

Provision this complete initial manifest once. Replace the example hashes with
SHA-256 digests of the actual wheel bytes:

```json
{
  "schema": "pytincture.environment/v1",
  "environment": "dev-42",
  "revision": 0,
  "runtime": {"engine": "pyodide", "version": "0.29.3"},
  "base": {
    "artifact": {
      "key": "wheels/tenzyn_shell-1.0.0-py3-none-any.whl",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    "application": "tenzyn_shell",
    "entrypoint": "main"
  },
  "apps": {
    "orders": {
      "installed": true,
      "version": "2.3.0",
      "approval_id": "review-1234",
      "backend": {
        "key": "wheels/orders_backend-2.3.0-py3-none-any.whl",
        "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      },
      "browser": {
        "artifact": {
          "key": "wheels/orders_ui-2.3.0-py3-none-any.whl",
          "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        },
        "entrypoint": "orders_ui:mount"
      },
      "dependencies": []
    },
    "reports": {"installed": false}
  },
  "active": null,
  "history": [],
  "build": null,
  "build_epoch": 0,
  "error": null
}
```

Use `empty_manifest("dev-42")` to generate the control fields, then fill `base`
and `apps`. Set `base: null` to preserve the current platform archive. The base
wheel's `application` is its importable top-level module, with a callable named
by `entrypoint`; that must match the normal launch application's name.

`revision` counts desired-state changes. `active` records the verified bundle's
selection hash, key, digest, size, revision, exact wheel lock and release IDs.
`history` retains up to eight previous active selections so newly started replicas
can restore BFF versions still used by older browsers. `build` and `build_epoch`
are the renewable lease and fencing information. These fields are managed by
Pytincture. Do not reset or overwrite a live manifest with an unconditional upload.
Use the install API, or a read/compare/write operation preserving the control fields.

The selection digest includes installed versions, artifact keys/hashes,
entrypoints, dependencies, runtime and base package. Uninstalled entries,
approval labels, revision counters and lease heartbeats do not force a rebuild.
Changing code requires a new wheel version and immutable key; never overwrite a
released wheel. A digest mismatch fails validation.

## Wire it into an ECS service

```python
from pytincture import PytinctureConfig, create_app
from pytincture.microapps import EnvironmentMicroappManager, S3ObjectStore, create_microapp_host
from my_platform.auth import authorize_environment_app, approve_install

config = PytinctureConfig.from_env()
objects = S3ObjectStore("company-apps", "environments/dev-42")
manager = EnvironmentMicroappManager(
    "/var/lib/tenzyn/microapps", config,
    objects=objects, environment="dev-42",
    manifest_key="environment.json",
    poll_seconds=5, lease_seconds=60, startup_timeout=300,
)
app = create_microapp_host(
    create_app(config), manager,
    authorize=authorize_environment_app,
    authorize_install=approve_install,
)
```

Run `uvicorn service:app --workers 1` per task. Each replica needs its own private
runtime directory; do not share one directory between processes. A warm local
cache avoids downloading the same verified bytes again. With an empty local disk,
a matching S3 bundle is downloaded and BFF workers start, without rebuilding the
aggregate. The S3 manifest coordinates replicas, so no sticky routing to a single
controller is required for this manager. The older `LiveMicroappManager` retains
its single-controller topology.

The wrapper automatically adds `environmentCatalogUrl` and
`environmentAssetOrigins` to pages rendered by the standard Pytincture backend.
The Pyodide startup path loads the aggregate before importing the base app and
reads its embedded app inventory. It does not download individually bundled
microapps again. Custom launch pages can pass those options to `runTinctureApp`,
or import `bootstrapEnvironment(runtime, options)` from `/frontend/microapps.js`.
Normal pages have these options disabled.

For local development, replace S3 with `DirectoryObjectStore("./objects")` and
provision via `await manager.initialize(document)` before starting. Initialization
is create-only. Never automatically create an empty production manifest when the
expected S3 object is missing: a missing manifest fails readiness.

## Approve and install while the environment runs

Your approval API remains responsible for human review, environment ownership,
permissions, and looking up immutable artifact records. `authorize(scope, app_id)`
must validate the platform session itself: the wrapped base app's middleware has
not run on these routes. `app_id=None` means environment/catalog access.

The optional `authorize_install(scope, command)` callback returns an approved app
record in the manifest shape, or denies the request by returning `None`. It must
check the caller's right to change this environment and the selected version's
review status. Never accept arbitrary client-supplied S3 keys, paths or hashes.
The HTTP installation endpoint is absent unless this callback is supplied.

```javascript
await fetch('/_microapps/installations', {
  method: 'POST', credentials: 'same-origin',
  headers: {'Content-Type': 'application/json', 'X-Pytincture-Install': '1'},
  body: JSON.stringify({
    app_id: 'orders', version: '2.3.0', installed: true, expected_revision: 0
  })
});
```

The endpoint checks same-origin browser requests and the explicit header, then
persists desired state before returning **202 accepted**. A stale
`expected_revision` returns 409. Read `GET /_microapps/environment` to compare
`desired` with `active` and inspect `building`/`error`. Accepted means persisted;
activation happens only after verification and worker readiness. A process crash
after acceptance does not lose the request: another replica or a restart resumes
from S3. Polling also detects approved manifest changes from another service.

A trusted server can call:

```python
await manager.request_install("orders", approved_record,
    expected_revision=current_revision,
    artifacts={approved_backend_key: local_backend_wheel,
               approved_browser_key: local_browser_wheel})
```

`artifacts` is optional. It uploads approved server-local wheel files if absent,
checks filenames/metadata/hashes, and refuses an existing key containing different
bytes before persisting the desired selection. Do not expose local paths to the
browser. To uninstall, approve and request `installed: false`; the next active
catalog excludes the app, its code leaves future bundles, and its BFF routes stop
being served. Already downloaded Python cannot be revoked from a running browser.

## Existing browsers and upgrades

Every BFF response through the wrapper includes `X-Pytincture-Bundle` and
`X-Pytincture-Revision`. The opt-in browser integration observes fetch/XHR BFF
responses and refreshes the authorized catalog when the selection changes. It
loads missing wheels in the background, with full hash/size checks, into the
existing interpreter. The original BFF operation is never replayed. Catalog
polling and focus refresh provide a fallback for clients making no BFF calls.

Use `window.pytinctureMicroapps.mount(id, element)` to open an app and
`.unmount(id)` to close it. Listen for `pytincture:microapps` to update navigation.
The library loads code; your platform still controls its menus and permissions.
If a previously loaded app has a newer release, the old version stays in place
and `pytincture:microapp-update` asks the platform to offer a safe reload. Existing
Python module objects cannot safely be swapped underneath running code. Retained
BFF versions support those old clients across replica restarts, within the eight
selection history window and configured local worker-release cap.

An aggregate contains all installed browser apps. Only a caller authorized for
all of them receives its descriptor/download. A partially entitled user gets a
filtered catalog and individual authorized wheels. Configure the normal platform
archive for that fallback if supplying the base UI exclusively in the aggregate.
Browser code and backend data authorization remain separate responsibilities.

## Optional browser delivery directly from S3

The default `browser_delivery="host"` serves verified bytes through ECS. To have
browsers fetch only browser artifacts directly from a private S3 bucket:

```python
s3_origin = "https://company-apps.s3.us-east-1.amazonaws.com"
config = PytinctureConfig.from_env(browser_connect_origins=(s3_origin,))
# Alternatively set PYTINCTURE_BROWSER_CONNECT_ORIGINS in the service configuration.
manager = EnvironmentMicroappManager(
    "/var/lib/tenzyn/microapps", config, objects=objects, environment="dev-42",
    browser_delivery="s3", asset_origins=(s3_origin,))
```

Allow the same exact HTTPS origin in the browser's CSP `connect-src`. If your
configuration already allows other origins, retain those too. Verify your SDK's
regional endpoint uses that hostname. The manager rejects URLs from other origins.
It generates short-lived signed GET URLs when serving the authorized catalog,
keeps their query signatures, and browser fetches omit platform cookies. Catalog
and BFF calls remain on the platform origin. Backend wheels are never signed or
advertised in browser responses. Treat signed URLs as temporary bearer access.

Example bucket CORS (use the actual platform origin):

```json
[{"AllowedOrigins":["https://dev-42.example.com"],
  "AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],
  "ExposeHeaders":["Content-Length","ETag"],"MaxAgeSeconds":300}]
```

Use an ECS task role and the SDK credential chain, with `s3:GetObject` and
`s3:PutObject` limited to this environment prefix; no list-bucket permission is
needed. Apply any required KMS permissions for your bucket encryption. Keep
public access blocked, require TLS, and limit writers to the approval service and
trusted runtime. Use separate prefixes/roles for separate environments. S3
conditional writes (`If-Match` / `If-None-Match`) protect manifest changes and
immutable artifacts; see [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
S3 versioning is useful for recovery; it is not the build coordination mechanism.
CloudFront is optional and is not required by either delivery mode. Hosted immutable
bundle URLs remain usable across a concurrent swap within retained history, while
all their apps remain installed and the caller remains authorized.

## Failure handling and verification

A failed live build preserves the last active bundle. Desired state remains
pending and the watcher retries; the status endpoint reports a sanitized error
type. Startup with a desired/active mismatch waits for successful reconciliation
or fails readiness at `startup_timeout`. A builder whose lease expires or is
replaced cannot activate stale output. Superseded immutable uploads may be left
unreferenced; garbage collection is an operator concern. Preserve objects referred
to by active/history and keep rollback wheels available. Request the previous
approved version through the same API to roll back.

CloudWatch can collect ECS worker logs, readiness failures and the status
endpoint's desired/active/error values. It does not cache or rebuild application
code. S3 stores the reusable aggregate; each task caches verified wheel/bundle
bytes locally. Keep liveness independent of readiness and allow enough startup
time for the first build. Backend workers are trusted code running with the task's
permissions, not a tenant sandbox. The current worker lifecycle/cap constraints
from [live microapps](live-microapps.md) still apply.

Run the deterministic checks and real-browser acceptance:

```sh
uv run --frozen --extra dev pytest -q tests/test_environment_microapps.py tests/test_microapp_s3_store.py
cd pytincture/frontend
npx playwright test --config playwright.environment.config.mjs
```

The browser fixture verifies consolidated startup, automatic bundled-app detection,
BFF-triggered loading of one missing wheel, unchanged interpreter state, and a
new client receiving the rebuilt aggregate. S3 SDK tests stub conditional AWS
requests; acceptance uses the local object store. A deployment against a real AWS
bucket and your own authorization/approval service still needs environment validation.
