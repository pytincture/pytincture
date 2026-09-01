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

Methods default to POST. Declaring GET is an explicit contract that the method
is parameterless, read-only, safe to repeat, and bodyless.
Cookie-authenticated state-changing calls include the CSRF token automatically.
All state-changing calls use one canonical `{ "args": [], "kwargs": {} }`
JSON object. Generated clients encode it once. Pytincture rejects aliases,
duplicate keys, non-finite values, excessive nesting/items, and static
signature/type mismatches before importing or constructing application code.
Pytincture also validates exact Origin and
Fetch Metadata on browser requests, including read-only GETs and BFFs intentionally exposed
without authentication. API clients such as server-to-server jobs may omit
both browser headers; if either is supplied it must describe a same-origin
browser request.
The server checks the static operation manifest before importing a requested
module or constructing its class.

BFF work is admitted through a bounded per-worker queue. The configured call
deadline starts before module loading, construction, and policy enforcement;
timed-out synchronous work retains its slot until its worker thread actually
finishes. Streaming responses—including a `StreamingResponse` returned by app
code—are wrapped with item, byte, idle, and wall-time limits. Tune the
`BFF_*` settings for the worker count and expected page-sized payloads.

Discovery records the canonical regular source file and its SHA-256 digest.
Execution reopens that file without following symlinks and requires the digest
to match, so a traversal, symlink, or discovery-to-import replacement is
rejected before browser or backend code executes.

## Authorization

`@bff_policy` records literal metadata. Policy-bearing exports fail startup
unless a server-side hook is configured before application modules are loaded:

```python
def enforce(user, policy, **context):
    if policy.get("role") not in set(user.get("roles", [])):
        return False
    return True

config = PytinctureConfig(
    modules_path="./apps",
    environment={"BFF_POLICY_HOOK_PATH": "security.enforce"},
)
```

The dotted callable is recommended for reproducible service startup. The
compatibility `set_bff_policy_hook()` API is public through 1.x.
Pytincture enforces standard application, provider, issuer, tenant, role, and
operation predicates before the hook runs; the hook owns application-specific
metadata such as scopes or account entitlements.

The hook contract is fail-closed: return `True` to allow, `False` to deny with
403, or `None` for compatibility with hooks that allow by completing normally.
Raising `HTTPException` preserves its explicit denial status. Other exceptions
are sanitized as server failures, and any other return type is rejected rather
than treated as permission.

Generated clients raise a `PytinctureBFFError` before decoding any non-2xx
response. The error exposes only `status_code`, `operation`, and
`correlation_id`; the backend response body is intentionally not included.

For every synchronous exported method whose class does not already use that
name, the browser proxy also exposes an awaitable `<method>_async(...)`
companion. Existing synchronous calls remain
available through 1.x, but emit a `DeprecationWarning` and retain the browser
platform's unavoidable synchronous-XHR limitations. New browser code should
use the async companion; backend classes and the class-level decorator do not
need to change. Async fetches, response reads, and stream reads have a bounded
35-second browser wait in addition to the server-side limits.

Optional stateless replay tokens remain browser-held and work across workers
without Redis or sticky routing. Concurrent token demand shares one refill.
Low-watermark prefetch is best-effort: if it fails after a BFF mutation has
already succeeded, the successful result is returned and a generic browser
diagnostic is emitted; the next call retries the refill before sending another
mutation.

Sessions and generated BFF routes are scoped to the application used during
login. Exact module-relative paths are checked against the files packaged for
that application, so a same-named module elsewhere cannot inherit a grant.

## Limits and streaming

Non-streaming calls are bounded by `BFF_CALL_TIMEOUT_SECONDS`. Streams are
bounded by `BFF_STREAM_MAX_SECONDS` and `BFF_STREAM_MAX_BYTES`; Pytincture
closes sync/async iterators on completion, timeout, byte limit, or disconnect.
The default stream framing is newline-delimited JSON. Use `@bff_stream(raw=True,
media_type=...)` only when the method yields correctly encoded bytes/text.

The normative transport, error, authentication, and compatibility rules are
in the [BFF v1 contract](contracts/bff-v1.md).
