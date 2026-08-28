# Backend-for-frontend guide

A BFF class keeps trusted Python on the server while generated browser Python
uses the same class-shaped API over HTTP.

```python
from pytincture.dataclass import (
    backend_for_frontend,
    bff_http_methods,
    bff_policy,
    bff_stream,
)

@backend_for_frontend
class Reports:
    def __init__(self, _user):
        self._user = _user

    @bff_http_methods("GET")
    @bff_policy(role="reader")
    def status(self):
        return {"ready": True, "email": self._user["email"]}

    @bff_stream()
    async def rows(self):
        yield {"id": 1}
        yield {"id": 2}
```

Import `Reports` normally from browser application code. During packaging,
Pytincture removes the implementation and emits a proxy with matching public
methods. Private names and undecorated classes are never registered.

Methods default to POST. Declare GET only for side-effect-free operations.
Cookie-authenticated state-changing calls include the CSRF token automatically.
The server checks the static operation manifest before importing a requested
module or constructing its class.

## Authorization

`@bff_policy` records literal metadata; it does not make an authorization
decision by itself. Configure a server-side hook before application modules
are loaded:

```python
from fastapi import HTTPException

def enforce(user, policy, **context):
    if policy.get("role") not in set(user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Forbidden")

config = PytinctureConfig(
    modules_path="./apps",
    environment={"BFF_POLICY_HOOK_PATH": "security.enforce"},
)
```

The dotted callable is recommended for reproducible service startup. The
compatibility `set_bff_policy_hook()` API is public through 1.x.

## Limits and streaming

Non-streaming calls are bounded by `BFF_CALL_TIMEOUT_SECONDS`. Streams are
bounded by `BFF_STREAM_MAX_SECONDS` and `BFF_STREAM_MAX_BYTES`; Pytincture
closes sync/async iterators on completion, timeout, byte limit, or disconnect.
The default stream framing is newline-delimited JSON. Use `@bff_stream(raw=True,
media_type=...)` only when the method yields correctly encoded bytes/text.

The normative transport, error, authentication, and compatibility rules are
in the [BFF v1 contract](contracts/bff-v1.md).
