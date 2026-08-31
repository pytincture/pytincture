# Service mode

Service mode is the supported choice when an application needs server-side
Python, BFF methods, authentication, private configuration, or backend-hosted
widget wheels.

## Application layout

```text
my_service/
├── service.py          # ASGI process code; never sent to the browser
├── dashboard.py        # browser entrypoint: class dashboard(...)
├── widget.py           # literal __widgetset__/__version__ metadata
├── dashboard_data.py   # decorated BFF class; replaced by a browser stub
├── helpers.py          # statically imported browser code
└── dashboard.css       # add through PYTINCTURE_BROWSER_FILES
```

The URL application name selects `<application>.py`. Pytincture discovers its
browser entrypoint from source without importing or executing the module on the
server. The supported forms are:

- a top-level class or callable with the same name as the application;
- a top-level class directly inheriting from `dhxpyt.layout.MainWindow`,
  including normal import aliases; or
- explicit literal metadata such as `APP_ENTRYPOINT = "Dashboard"` or
  `APP_CONFIG = {"entrypoint": "Dashboard"}`.

Explicit metadata must name a top-level class or function. Dynamic entrypoint
expressions and inheritance patterns that cannot be resolved statically return
a clear validation error; add literal metadata for those applications. Keep
secrets and database clients in BFF/server modules; never put them in browser
files.

## ASGI factory

```python
from pytincture import PytinctureConfig, create_app

config = PytinctureConfig(
    modules_path="./apps",
    default_application="dashboard",
    cors_allowed_origins=("https://dashboard.example.com",),
)
app = create_app(config)
```

Run `uvicorn service:app --host 127.0.0.1 --port 8070`. `create_app()` owns its
configuration, BFF registry, and state, so tests and multi-app processes do not
need to mutate global environment settings. `launch_service()` remains the
supported compatibility launcher for existing code.

## Browser delivery

`GET /{application}` returns the loader page. It fetches
`/{application}/appcode/appcode.pyt`, a ZIP archive containing only the
entrypoint, reachable local imports, configured browser files, and generated
BFF stubs. Frontend and backend-hosted files receive the service-instance UUID
as a query parameter for cache invalidation; navigation URLs remain clean.

The widgetset resolution order is:

1. install the application-declared real version from the package index;
2. request that same real version from the Pytincture backend;
3. request `PYTINCTURE_DEV_WHEEL_VERSION` from the backend only as an explicit
   development fallback (`99.99.99` by default).

The backend authorizes only the declared widgetset version and that configured
development version. Stale or arbitrary same-name wheel versions are not
public.

Micropip/package-index URLs are not modified with the backend cache UUID.
The optional service worker uses a per-application scope and an exact immutable
framework-asset manifest. It does not intercept appcode, BFF/auth calls,
cross-origin requests, or unrelated same-origin resources.

## Production

Use a stable signing key for authentication and a trusted TLS reverse proxy.
Signed browser state works across workers without Redis or sticky sessions;
remote revocation storage remains optional. See
[configuration](configuration.md), [authentication](authentication.md), and
[production deployment](production-deployment.md).

If applications in one service do not share the same admitted identities, use
the stateless `application_admission` mapping to authorize identities before a
session is issued for each application. Deploy materially different trust
domains on separate origins/processes; browser code and widgets intentionally
share their application's origin authority.
