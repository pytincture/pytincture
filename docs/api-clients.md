# Application API clients

API clients let another application's server call selected `@bff_external`
methods without signing in as a person. Each client gets its own generated ID
and secret, an assigned application, and grants for existing modules, classes,
and methods. There are no manually named permission scopes.

## Declare the external methods

```python
# services/catalog.py
from pytincture.dataclass import backend_for_frontend, bff_external

@backend_for_frontend
class Catalog:
    def __init__(self, _user):
        self.user = _user

    @bff_external
    def dataset_page(self, page: int = 1, page_size: int = 100):
        ...

    def internal_report(self):
        ...
```

The application must import this module so it belongs to the application's BFF
dependency graph. A grant never exports an otherwise unexported module or method.
`internal_report` remains inaccessible to application tokens, even with a class
grant. Existing signed-in browser calls retain their session/CSRF authorization.

## Register a client on the server

Use a private directory **outside `modules_path` and all web-served directories**.
The CLI creates the SQLite file with owner-only permissions and stores only a
hash of the randomly generated 256-bit secret. It prints the secret once;
deliver it securely to the consuming application's operator and keep it in
that application's server-side secret store. Never embed it in browser or
mobile app code, source control, URLs, or logs.

```sh
mkdir -p /var/lib/myapp/private
chmod 700 /var/lib/myapp/private
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 \
  create --application myapp --client-id reporting-service \
  --allow services/catalog:Catalog:dataset_page
```

Repeat `--allow` for more methods or classes. `--allow services/catalog:Catalog`
grants all present **and future** externally decorated methods on that class.
Use explicit method grants when future additions should need approval.
Module paths are exact and may include folders; `.py` is optional and normalized.
Wildcards, traversal, private methods, and empty grant lists are rejected.
A client belongs to one application; create separate clients for other apps.

Registration and lifecycle operations are operator-only CLI/Python operations;
there is no publicly accessible client-registration endpoint. Treat permission
to modify the registry as permission to administer API access.

## Configure the service

```python
import os
from pytincture import PytinctureConfig, create_app

app = create_app(PytinctureConfig(
    modules_path="/srv/myapp/appcode",
    enable_bff_api_tokens=True,
    bff_api_client_registry="/var/lib/myapp/private/clients.sqlite3",
    session_secret=os.environ["APP_SIGNING_SECRET"],
    allowed_hosts=("api.example.com",),
    canonical_origin="https://api.example.com",
    session_https_only=True,
    api_docs_mode="disabled",  # Optional: token exchange and BFF calls still work.
))
```

Use a strong persistent signing secret, at least 32 random characters. Existing
login providers can remain configured, but application credentials do not require
one. `BFF_API_CLIENT_REGISTRY` is the equivalent environment setting when using
`PytinctureConfig.from_env()` or the legacy launcher. If constructing a config
directly, supply the field explicitly. An empty path disables client credentials.
The registry must exist before the service starts.

HTTPS is required for client credential exchange and client-token calls. For
local HTTP tests only, use `allow_development_auth_origin=True` and
`session_https_only=False`, with no production host/origin/proxy settings.
That mode rejects non-loopback requests. A TLS-terminating proxy must use the
framework's documented trusted-proxy configuration.

## Obtain and use a token

```python
import os
import requests

base = "https://api.example.com/myapp"
response = requests.post(f"{base}/auth/client-token", json={
    "grant_type": "client_credentials",
    "client_id": "reporting-service",
    "client_secret": os.environ["MYAPP_CLIENT_SECRET"],
}, timeout=15)
response.raise_for_status()
issued = response.json()

response = requests.post(
    f"{base}/classcall/services/catalog/Catalog/dataset_page",
    headers={"Authorization": f"Bearer {issued['access_token']}"},
    json={"page": 1, "page_size": 25}, timeout=30,
)
response.raise_for_status()
print(response.json())
```

Cache the access token in server memory and obtain a new one before its
`expires_in` lifetime (900 seconds) elapses. There is no refresh token; exchange
the client credentials again. Do not blindly retry mutating API calls.
The token is signed, not encrypted, and is not a JWT. The exchange uses JSON;
it is not a drop-in OAuth/OIDC provider. Credentials and tokens must stay private.
Issuance shares the configured login attempt/window limits, with a separate
per-peer key. Limits are local to each worker; use your ingress controls for
deployment-wide quotas.

With Swagger enabled, open `/{application}/{module}/bff-docs`, expand
**Application credentials**, and generate an application token. The secret is
cleared after each attempt, and the token is held only in the page's memory.
**Clear token** removes the local copy; it does not revoke credentials.
Swagger's ordinary **Authorize** dialog also accepts an already-issued bearer
token. External operations show their exact grant identifiers.

## Change permissions, rotate, or revoke access

```sh
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 list
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 \
  set-grants reporting-service --allow services/catalog:Catalog:dataset_page
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 \
  rotate reporting-service
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 \
  disable reporting-service
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 \
  enable reporting-service
python -m pytincture.api_clients --registry /var/lib/myapp/private/clients.sqlite3 audit
```

`rotate` returns a new secret and immediately invalidates the old secret and
tokens. Coordinate replacing the consuming app's secret. `disable` blocks token
issuance and existing tokens. `enable` permits fresh token issuance but does not
restore old tokens. `set-grants` replaces the complete grant list and invalidates
earlier tokens. Changes are transactional; no service restart is required.
Requests/streams already admitted may finish.

All workers must read the same registry and use the same signing secret. This
initial registry implementation supports workers on one host using a local
SQLite file. Do not deploy independent registry copies across hosts or put
SQLite on an unsuitable network filesystem and assume revocation propagates;
a shared database/provider is needed for distributed deployments.

The registry records administrative action/time/client ID; `audit` shows the
latest 100 actions. Request logs record successful token issuance and client
grant decisions with client ID and correlation ID, without secrets or tokens.
Your existing BFF policy hook receives the client identity and remains enforced
for tenant/resource-specific checks. `_user["client_id"]` identifies the caller;
`auth_provider`/`auth_type` are `api_client`, with empty email and roles. Client
registration grants are separate from email-based user admission rules.

For an operator's own management UI or provisioning code, the same operations
are available as `pytincture.api_clients.create_client(path, application, grants,
client_id=...)` and `update_client(path, client_id, action, grants=...)`. Grant
objects use `{"module": "services/catalog", "class": "Catalog", "methods":
["dataset_page"]}`; omit `methods` for a class grant. Keep these management
operations behind your own administrator authorization.
