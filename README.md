# pyTincture

## Overview
`pyTincture` is a Python framework designed to leverage the capabilities of Pyodide, enabling developers to create sophisticated and user-friendly GUI libraries. This project aims to bridge the gap between Python's powerful backend and intuitive, interactive frontend interfaces.

## Features
- Pyodide Integration: Seamlessly bring Python to the web via Pyodide.
- GUI Library Support: Simplify the creation and management of GUI components in Python.
- Dynamic Code Packaging: Generate in-memory ZIP packages for frontend consumption.
- Widgetset Stub Generation: Automatically generate frontend stub classes using the @backend_for_frontend decorator.
- Streaming BFF Calls: Enable true streaming responses with the @bff_stream decorator.
- Authentication & Sessions: Supports Google OAuth2, SAML 2.0 SSO, and email/password authentication with session management.
- Redis Integration: Optionally use a Redis-backed session store via Upstash.
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
- USE_REDIS_INSTANCE: Set to "true" to enable Redis-backed session storage.
- ALLOWED_EMAILS: A comma-separated list of authorized email addresses.
   example: "some@email.com,joe@email.com"
- ENABLE_GOOGLE_AUTH: Enable the respective authentication mechanisms.
   example: "true"
- ENABLE_SAML_AUTH: Enable SAML 2.0 authentication.
   example: "true"
- SAML_EMAIL_ATTRIBUTE: Attribute name used to extract the user email from the SAML assertion.
   example: "email"
- SAML_NAME_ATTRIBUTE: Optional attribute for the display name.
   example: "givenName"
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
- SAML_DEBUG: Enable verbose SAML logging.
- ALLOWED_NOAUTH_CLASSCALLS
   example: [{"file": "somefile.py", "class": "SomeClass", "function": "somefunction"}]
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- SECRET_KEY: Secret key for google auth
- USE_REDIS_INSTANCE: Enable the redis upstash for sessions
   example: "true"
- REDIS_UPSTASH_INSTANCE_URL: Url for upstash redis instance
   example: "http://127.0.0.1:16379"
- REDIS_UPSTASH_INSTANCE_TOKEN: Redis Upstash token
- DATABASE_URL: Database connection string
   example: "sqlite:////absolute/path/to/database.db"

## Running the Service with your application
-------------------
Development Mode:

  Use the service from your application:
~~~
if __name__=="__main__":
    from pytincture import launch_service
    launch_service(
        modules_folder=".",  # point to your modules directly
        env_vars={
            "ALLOWED_EMAILS": []
        }
    )
~~~

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
Every `@backend_for_frontend` class exposes its methods via `/classcall`, but you can now gate each method without modifying pytincture core:

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

Because the authorization decision lives on the server, even an authenticated user who opens the browser console can’t call methods they don’t have rights to. The hook is optional—if you don’t register one, `bff_policy` metadata is ignored.

### CI/CD release flow
Publishing a GitHub release (or manually triggering the `Publish to PyPI` workflow) now runs the following automatically:

1. Build and upload the Python package to PyPI via `twine`.
2. Build the frontend runtime bundles and publish them to npm (using the same version as `pytincture/__init__.__version__`).

Required GitHub secrets:
- `PYPI_PASSWORD`: a PyPI API token (formatted `pypi-***`) with publish rights to `pytincture`.
- `NPM_TOKEN`: an npm access token with publish rights to `@pytincture/runtime`.

Both secrets must be configured at the repo (or org) level for the workflow to succeed.

## License
`pyTincture` is licensed under the MIT License.
