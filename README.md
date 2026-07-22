# pyTincture

## Overview
`pyTincture` is a Python framework designed to leverage the capabilities of Pyodide, enabling developers to create sophisticated and user-friendly GUI libraries. This project aims to bridge the gap between Python's powerful backend and intuitive, interactive frontend interfaces.

## Features
- Pyodide Integration: Seamlessly bring Python to the web via Pyodide.
- GUI Library Support: Simplify the creation and management of GUI components in Python.
- Browser-safe Code Packaging: Package the application entrypoint, reachable local imports, and explicitly configured browser files.
- Widgetset Stub Generation: Automatically generate frontend stub classes using the @backend_for_frontend decorator.
- Streaming BFF Calls: Enable true streaming responses with the @bff_stream decorator.
- Authentication & Sessions: Supports Google and Microsoft OAuth2, SAML 2.0 SSO, verified email/password login, key rotation, CSRF protection, and revocable signed sessions.
- Redis Integration: Optionally expose the legacy shared `USER_SESSION_DICT` through Upstash; authentication does not require Redis.
- Cross-Platform Compatibility: Works on any platform where Pyodide is supported.
- Easy to Use: Provides a user-friendly API to streamline GUI development.
- Production Launcher: Includes a uvicorn-based launcher for deploying the service.
- PyPI Distribution: Easily installable via pip from PyPI.

## Installation

From PyPI:
~~~
pip install pytincture
~~~

From Source:
  1. Clone the repository:
~~~
git clone https://github.com/yourusername/pyTincture.git
cd pyTincture
~~~

  2. Install dependencies:
~~~
pip install .
~~~
   (Alternatively, follow the instructions in pyproject.toml.)

## Environment Variables
- MODULES_PATH: Directory containing module files used for dynamic packaging. This is set automatically from `modules_folder` when `launch_service` starts; overriding it via env vars is usually unnecessary.
- USE_REDIS_INSTANCE: Set to "true" to back the legacy `USER_SESSION_DICT` with Upstash. Authentication does not read or write this dictionary.
- ALLOWED_EMAILS: An optional comma-separated authorization allowlist. It is not a password verifier.
   example: "some@email.com,joe@email.com"
- ENABLE_USER_LOGIN: Enable verified local email/password login. This route is rejected when the flag is false.
- AUTH_PASSWORD_HASHES: JSON object mapping normalized email addresses to Argon2id or bcrypt hashes.
   example: `{"user@example.com":"$argon2id$..."}`
- AUTH_USER_AUTHENTICATOR: Optional dotted path to a sync or async callable accepting `email`, `password`, and `request`. It must return trusted user claims, `True`, or `False`.
- ENABLE_DEV_EMAIL_LOGIN: Allow a non-empty `ALLOWED_EMAILS` list without password verification only on loopback hosts. This is intentionally unsafe and must only be set to `true` for local development.
- ENABLE_GOOGLE_AUTH: Enable the respective authentication mechanisms.
   example: "true"
- ENABLE_MICROSOFT_AUTH: Enable Microsoft OAuth2 authentication using the shared `common` tenant endpoint.
   example: "true"
- GOOGLE_CLIENT_ID: OAuth client ID for Google.
- GOOGLE_CLIENT_SECRET: OAuth client secret for Google.
- MICROSOFT_CLIENT_ID: OAuth client ID for Microsoft Azure AD / Microsoft identity platform.
- MICROSOFT_CLIENT_SECRET: OAuth client secret for Microsoft Azure AD / Microsoft identity platform.
- ENABLE_SAML_AUTH: Enable SAML 2.0 authentication.
   example: "true"
- SAML_EMAIL_ATTRIBUTE: Attribute name used to extract the user email from the SAML assertion.
   example: "email"
- SAML_NAME_ATTRIBUTE: Optional attribute for the display name.
   example: "givenName"
- SAML_LOGIN_LABEL: Optional label for the single-provider SAML login button.
   example: "Login with Contoso"
- SAML_LOGO_URL: Optional image URL for the single-provider SAML login button.
   example: "/appcode/contoso-logo.svg"
- SAML_SECRET_KEY: Required whenever any production authentication method is enabled. Use at least 32 random characters, keep it stable across deployments, and provide the same value to every replica. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Loopback development login may use an ephemeral generated key.
- AUTH_SESSION_PREVIOUS_SECRET_KEYS: JSON list (or comma-separated list) of prior strong signing keys accepted during rotation. Cookies accepted with an old key are re-signed with the current key.
- AUTH_SESSION_MAX_AGE_SECONDS: Signed authentication cookie lifetime in seconds. Defaults to `28800` (8 hours).
- AUTH_SESSION_HTTPS_ONLY: Require HTTPS for authentication and CSRF cookies. Defaults to `true`; loopback development login defaults it to `false` unless explicitly overridden.
- AUTH_SESSION_SAME_SITE: Cookie SameSite policy: `lax`, `strict`, or `none`. Defaults to `lax`.
- SAML_RELAY_STATE_TTL_SECONDS: Maximum SAML login handshake age in seconds. Defaults to `600` (10 minutes).
- SAML_DEFAULT_REDIRECT: Optional redirect path or URL template after SAML login (defaults to `/{application}` when unset).
   example: "/{application}"
- SAML_SP_ENTITY_ID: Optional template for the SP entity ID (supports {application}, {base_url}, {host}); defaults to `/{application}/auth/saml/metadata`.
- SAML_SP_ASSERTION_CONSUMER_SERVICE_URL: Optional template for the ACS endpoint (supports placeholders like {application}).
- SAML_SP_X509_CERT: Service Provider certificate in PEM format if signing/encryption is required.
- SAML_SP_PRIVATE_KEY: Service Provider private key in PEM format matching the SP certificate.
- SAML_IDP_ENTITY_ID: Identity Provider entity ID.
- SAML_IDP_SSO_URL: Identity Provider SSO URL.
- SAML_IDP_SLO_URL: Optional Identity Provider SLO URL.
- SAML_IDP_X509_CERT: Identity Provider certificate in PEM format.
- SAML_PROVIDERS: Optional JSON configuration for multiple named SAML providers. When set, the login page renders one SAML button per provider and uses provider-specific login routes such as `/{application}/auth/saml/{provider_id}/login`. By default, providers share the standard SP entity ID and ACS URLs (`/{application}/auth/saml/metadata` and `/{application}/auth/saml/acs`) so existing IdP app registrations do not need new reply URLs. Provider entries may override these with `sp_entity_id` and `sp_assertion_consumer_service_url` when per-provider SP URLs are required.
    example:
    ```json
    [
       {
          "id": "company-a",
          "label": "Login with Company A",
          "logo_url": "/appcode/company-a.svg",
          "idp_entity_id": "https://idp-a.example.com/metadata",
          "idp_sso_url": "https://idp-a.example.com/sso",
          "idp_x509_cert": "-----BEGIN CERTIFICATE-----..."
       },
       {
          "id": "company-b",
          "label": "Login with Company B",
          "logo_url": "/appcode/company-b.svg",
          "idp_entity_id": "https://idp-b.example.com/metadata",
          "idp_sso_url": "https://idp-b.example.com/sso",
          "idp_x509_cert": "-----BEGIN CERTIFICATE-----..."
       }
    ]
    ```
      Provider entries may also override `sp_entity_id`, `sp_assertion_consumer_service_url`, `sp_x509_cert`, `sp_private_key`, `idp_slo_url`, `default_redirect`, `allowed_roles`, and `role_attribute_keys`. If `SAML_PROVIDERS` is not set, the existing single-provider `SAML_*` variables continue to work.
- SAML_DEBUG: Enable verbose SAML logging.
- ALLOWED_NOAUTH_CLASSCALLS
   example: [{"file": "somefile.py", "class": "SomeClass", "function": "somefunction"}]
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- SECRET_KEY: Legacy fallback signing key used only when `SAML_SECRET_KEY` is unset.
- REDIS_UPSTASH_INSTANCE_URL: Url for upstash redis instance
   example: "http://127.0.0.1:16379"
- REDIS_UPSTASH_INSTANCE_TOKEN: Redis Upstash token
- DATABASE_URL: Database connection string
   example: "sqlite:////absolute/path/to/database.db"
- PYTINCTURE_BROWSER_FILES: JSON list or comma-separated globs for extra files to include in the browser package. Python entrypoints and reachable local imports are discovered automatically.
- PYTINCTURE_PUBLIC_ASSET_PATHS: Explicit globs for files that may be served from `/{application}/appcode/` in addition to standard image, font, media, CSS, and JavaScript assets. Python and configuration files are denied by default. A root-level wheel whose distribution name matches the widgetset detected for the requested application is served automatically; unrelated wheels remain private.
- MAX_REQUEST_BODY_BYTES: Maximum request body size. Defaults to 2 MiB.
- BFF_CALL_TIMEOUT_SECONDS: Maximum non-streaming BFF execution time. Defaults to 30 seconds.
- BFF_STREAM_MAX_SECONDS: Maximum BFF stream duration. Defaults to 300 seconds.
- BFF_STREAM_MAX_BYTES: Maximum BFF stream output. Defaults to 10 MiB.
- BFF_POLICY_HOOK_PATH: Dotted path to a sync or async policy hook. This is the recommended launcher configuration because the hook must be available before application modules are imported or constructed.
- ENABLE_BFF_REPLAY_TOKENS: Opt-in one-time request proofs for authenticated BFF calls. Generated browser stubs automatically obtain, consume, and refill an in-memory token pool. Defaults to `false`.
- BFF_REPLAY_TOKEN_BATCH_SIZE: Number of one-time proofs returned in each opaque refill. Defaults to `12`.
- BFF_REPLAY_TOKEN_LOW_WATERMARK: Refill the browser-side pool when this many proofs remain. Defaults to `3`.
- BFF_REPLAY_TOKEN_TTL_SECONDS: Lifetime of an unused proof. Defaults to `300` seconds.
- ENABLE_MCP: Enable the MCP mount. MCP exports no tools by default.
- MCP_EXPOSED_OPERATIONS: JSON list of explicitly allowed FastAPI operation IDs. Login, session, logging, application delivery, and appcode download operations cannot be exported.

Authenticated browser cookies contain only stable identity claims plus opaque session and CSRF identifiers. Passwords, complete SAML attributes, SAML assertions, and changing SAML session indexes are not stored in the cookie. Logout revokes the current session; Upstash-backed services share revocations between replicas.

When `ENABLE_BFF_REPLAY_TOKENS=true`, each authenticated `.pyt` download receives a random, short-lived client decoder and an opaque session-bound capsule. Token refills return an authenticated opaque payload rather than a visible JSON token list. The capsule is recoverable after backend restarts as long as `SAML_SECRET_KEY` remains stable. Without Upstash, already-issued tokens are intentionally invalidated by a backend restart and generated stubs transparently refill them. This feature makes copied completed BFF requests fail and adds a reverse-engineering barrier; it is not a security boundary against a user who controls the browser, WASM memory, or application archive.

## Running the Service with your application
-------------------
Development Mode:

  Use the service from your application:
~~~
if __name__=="__main__":
    from pytincture import launch_service
    launch_service(
        modules_folder=".",  # point to your modules directly
        default_application="py_ui",  # optional: redirect / to /py_ui
        favicon_folder="branding/favicon",  # optional; relative to modules_folder
        env_vars={
            "ENABLE_USER_LOGIN": "true",
            "ENABLE_DEV_EMAIL_LOGIN": "true",
            "ALLOWED_EMAILS": "developer@example.com",
            "AUTH_SESSION_HTTPS_ONLY": "false",
        }
    )
~~~

For one application, place the complete favicon set in a conventional `favicon` directory under `modules_folder`:

```text
favicon/
  favicon.ico
  favicon-16x16.png
  favicon-32x32.png
  apple-touch-icon.png
  android-chrome-192x192.png
  android-chrome-512x512.png
  site.webmanifest
```

pyTincture scans the directory and emits the icon, size, Apple touch icon, mask icon, and web-manifest declarations browsers need. Browsers do not enumerate favicon directories themselves.

Set `favicon_folder` on `launch_service` to use a different directory. Relative paths are resolved from `modules_folder`, and absolute paths are supported, including paths outside the modules directory.

For multiple applications, use one directory per application, such as `favicon/py_ui/` and `favicon/admin/`. This also works under a launcher-configured directory. An application can override both the launcher setting and the convention with either `APP_FAVICON = "branding/icons"` or `APP_CONFIG = {"favicon": "branding/icons"}`; the value may point to a directory or a single icon file under `modules_folder`.

## Testing

Tests are written using pytest and cover endpoints, helper functions, and the launcher.
Run all tests with:
~~~
python -m pytest
~~~

Tests include:

  - tests/test_app.py: Endpoint tests and service logic.
  - tests/test_dataclass.py: Tests for stub generation, decorators, and helper functions.
  - tests/test_launcher.py: Tests for the uvicorn launcher and process management.


## Docker Quick Start Example built from https://github.com/pytincture/pytincture_example
  Run the docker image directly from Dockerhub
~~~
docker run -p8070:8070 -i pytincture/pytincture:latest
~~~
Load url in browser
~~~
http://localhost:8070/py_ui
~~~

## Standalone pytincture.js / CDN Build
The file under `pytincture/frontend/pytincture.js` can be bundled and published as a standalone runtime for demos that only need a `<script>` tag plus embedded Python.

### Building the bundle
1. Install the JS tooling once:
   ```
   cd pytincture/frontend
   npm install
   ```
2. Produce distributable artifacts (this automatically syncs `package.json`'s version to the Python framework’s `pytincture/__init__.py`):
   ```
   npm run build
   ```
   The `dist/` folder will contain:
   - `pytincture.js` (IIFE build for script tags)
   - `pytincture.min.js` (minified IIFE)
   - `pytincture.esm.js` (ES module build)

You can run `npm run build:watch` while editing `pytincture/frontend/pytincture.js` to regenerate the bundles automatically.

### Publishing to a CDN
The frontend directory is wired like a normal npm package (`name: @pytincture/runtime`). After bumping the version in `pytincture/frontend/package.json`:
```
cd pytincture/frontend
npm run build
npm publish --access public
```
The publish script reuses the synchronized version, so npm releases always match the Python `__version__`.
Alternatively, you can run the helper script from the repo root and let it handle version syncing, bundling, and publishing (it skips publishing if that version already exists on npm):
```
bash scripts/publish_runtime.sh
```
Once published to npm, CDNs such as jsDelivr and UNPKG will expose the runtime automatically, e.g.:
```
<script src="https://cdn.jsdelivr.net/npm/@pytincture/runtime@0.1.0/dist/pytincture.min.js"></script>
```
You can also point jsDelivr at a Git tag (`https://cdn.jsdelivr.net/gh/<org>/<repo>@<tag>/pytincture/frontend/dist/pytincture.min.js`) if you prefer GitHub releases.

### Using pytincture.js standalone
With the CDN script on the page, pytincture auto-detects any `<script type="text/python">` blocks and runs them once Pyodide is ready. Optional helpers:

- Add `window.pytinctureAutoStartConfig = { widgetlib: "dhxpyt", libsSelector: "#micropip-libs" }` before loading the script to override defaults.
- Set `window.pytinctureAutoStartDisabled = true` if you prefer to call `runTinctureApp({...})` manually.
- Extra Python wheels can be listed in `<script type="text/json" id="micropip-libs">["faker"]</script>`.

Errors are rendered inside `#maindiv` (if present) and logged to the console, making it easy to host pure-static demos without the full framework.

### Backend-for-Frontend access policies
Only classes marked with `@backend_for_frontend` and their public methods/attributes are registered. Unknown targets are rejected from a static manifest before application code is imported or constructed. Methods default to POST.

Use `@bff_http_methods` to opt a side-effect-free method into GET or to select PUT, PATCH, or DELETE:

```python
from pytincture.dataclass import backend_for_frontend, bff_http_methods

@backend_for_frontend
class Reports:
    @bff_http_methods("GET")
    def status(self):
        return {"ready": True}
```

Policy metadata must use literal values so it can be read without importing the module. Hooks may be synchronous or asynchronous and run before module import, construction, or attribute access:

1. Tag the method with `@bff_policy(...)` to describe whatever metadata you need (roles, scopes, tenants, etc.):
   ```python
   from pytincture.dataclass import backend_for_frontend, bff_policy

   @backend_for_frontend
   class Reports:
       @bff_policy(role="manager", scopes=["reports:view"])
       def export(self):
           ...
   ```
2. Register a server-side hook that runs before every call. The hook receives the authenticated user (from OAuth/SAML/local login), the policy metadata, the class/method names, and the request. It can raise `HTTPException` to block the call:
   ```python
   from fastapi import HTTPException
   from pytincture.backend.app import set_bff_policy_hook

   def my_policy_hook(user, policy, **kwargs):
       roles = set(user.get("roles", []))
       required = policy.get("role")
       if required and required not in roles:
           raise HTTPException(status_code=403, detail="Forbidden")

   set_bff_policy_hook(my_policy_hook)
   ```

Because the authorization decision lives on the server, even an authenticated user who opens the browser console can’t call methods they don’t have rights to. The hook is optional—if you don’t register one, `bff_policy` metadata is ignored. Cookie-authenticated state-changing calls also require the CSRF token automatically sent by generated browser stubs.

### 0.10 security migration

Version 0.10 intentionally removes insecure legacy behavior:

- configure a strong `SAML_SECRET_KEY` before enabling authentication;
- configure `AUTH_PASSWORD_HASHES` or `AUTH_USER_AUTHENTICATOR` for local login, or use `ENABLE_DEV_EMAIL_LOGIN` only on loopback during development;
- decorate every remotely callable class with `@backend_for_frontend`;
- change manually issued GET method calls to POST or declare `@bff_http_methods("GET")`;
- explicitly list extra browser package files and public assets;
- explicitly opt in to MCP operations;
- update custom cookie-based clients to echo the `pytincture_csrf` cookie in `X-CSRF-Token` for POST, PUT, PATCH, and DELETE.

Pytincture does not currently provide rate limiting. Production deployments should enforce suitable login and request rates at the application gateway or reverse proxy.

### CI/CD release flow
Python and JavaScript publishing use separate GitHub Actions workflows. Publishing a GitHub release triggers both independently, and either workflow can also be run manually:

1. `Publish to PyPI` builds and uploads only the Python package via `twine`.
2. `Publish JavaScript Runtime` builds and publishes only `@pytincture/runtime` to npm, using the version from `pytincture/__init__.__version__`.

Required GitHub secrets:
- `PYPI_PASSWORD`: a PyPI API token (formatted `pypi-***`) with publish rights to `pytincture`.
- `NPM_TOKEN`: an npm access token with publish rights to `@pytincture/runtime`.

Each secret is required only by its corresponding workflow, so a missing or failed npm publication does not affect the PyPI publication, and vice versa.

## License
`pyTincture` is licensed under the MIT License.
