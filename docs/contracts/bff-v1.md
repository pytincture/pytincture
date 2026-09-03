# BFF transport and generated-stub contract — version 1

This contract covers calls exported with `@backend_for_frontend` and the
browser proxies generated into `appcode.pyt`. It is the BFF contract supported
by Pytincture 1.x.

## Operation discovery

- Only classes decorated with Pytincture's `@backend_for_frontend` are
  exported. Static discovery proves that decorator names and module aliases
  were imported directly from `pytincture.dataclass`; local, unrelated,
  re-exported, or rebound same-named decorators are not security declarations.
- `@backend_for_frontend` must be the single outermost export decorator. Other
  class decorators remain supported beneath it, so the final class they
  produce is explicitly passed through Pytincture's export boundary.
- The class marker intentionally exports every public operation; a separate
  method-level export marker is not required by the 1.x developer contract.
- Public methods are operations. Public assigned/annotated attributes are
  read-only `GET` operations.
- Private names beginning with `_` are not exported.
- Module identifiers are relative POSIX-style paths under `MODULES_PATH` and
  include `.py`.
- Static manifest validation occurs before application code is imported.
- Duplicate exported class definitions and any later binding of the exported
  class or one of its members reject that source file before import. Manifest
  operations carry source-definition fingerprints and dispatch verifies the
  final Pytincture wrapper, original class, and current member against them
  before construction.
- A source file that is unreadable, unsafe, malformed, or invalidly encoded
  contributes no callable operations and is recorded as rejected. Its failure
  does not remove valid operations from other canonical module paths. A
  repaired file must pass a complete static rescan before it can be called.

## HTTP request

The route is:

```text
/{application}/classcall/{file_path}/{class_name}/{function_name}
```

The application is the signed session audience, and the module must be an
exact path packaged for that application. The unscoped `/classcall/...` route
was removed before 1.0 because it could not prove application-graph ownership.
Generated proxies and manually written clients must always use the scoped
route. In no-auth mode, `@backend_for_frontend` remains the complete public
export decision; no redundant per-method allowlist is required.

Registry membership is checked before application-graph discovery, so an
unknown class or operation cannot trigger graph parsing. Valid application
graphs use a bounded per-worker LRU and are reused only while secure metadata
for every selected Python source and relevant directory remains unchanged.
Source edits, removals, additions, and newly matched browser-file declarations
invalidate automatically. Cold scans apply the appcode file, per-file byte,
aggregate byte, directory, and scanned-file limits. This cache is disposable;
it creates no Redis, shared-state, or sticky-routing requirement.

Methods default to `POST`. `@bff_http_methods` may declare `GET`, `POST`,
`PUT`, `PATCH`, or `DELETE`. A method mismatch returns `405` with `Allow`.

For body-bearing methods, the canonical JSON body is:

```json
{
  "args": [],
  "kwargs": {}
}
```

Generated stubs may send positional values in `args` and named values in
`kwargs`. This is the only accepted representation: the body itself cannot be
a JSON string, both keys are required, additional top-level keys are rejected,
and legacy structured positional entries are not auto-unwrapped. This strict
pre-1.0 boundary avoids a permanent double-decoding compatibility mode.

Before application code is imported or a BFF class is constructed, Pytincture
rejects duplicate keys, non-finite numbers, excessive bytes/nesting/items,
malformed `args`/`kwargs`, signature binding errors, missing/unexpected
arguments, and values that conflict with common static annotations such as
`str`, `bool`, `int`, `float`, `list[T]`, `dict[K, V]`, `Optional`, `Union`,
and `Literal`. Application-specific annotations remain application-owned, but
argument names/counts are still bound statically.

`Literal` validation compares both the exact JSON runtime type and value.
Python's equality aliases do not cross the request boundary: `true` does not
match `Literal[1]`, `1` does not match `Literal[True]` or `Literal[1.0]`, and
JSON `null` matches only an explicit `None` option.

State-changing dispatcher requests require `Content-Type: application/json`.
Declaring GET is an explicit developer promise that the operation is
parameterless, read-only, safe to repeat, and bodyless. Browser requests that
send an Origin must supply the exact service Origin; Fetch Metadata, when
present, must report `Sec-Fetch-Site: same-origin` for GET and mutations.
Cross-site, same-site, opaque/null-origin, and malformed browser contexts are rejected.
Trusted non-browser clients may omit both browser headers, but cannot send
conflicting browser metadata.

Authenticated state-changing requests carry `X-CSRF-Token`. When replay
protection is enabled, generated stubs additionally carry
`X-Pytincture-BFF-Token`; rejected or expired proofs return `409` with
`X-Pytincture-Replay: rejected` and the stub may refill and retry once.

Replay proofs are disabled by default. When enabled, refill admission is
bounded independently per signed session, direct network peer, and worker.
Those three checks commit atomically: a denial at any scope consumes no quota
from the other scopes.
The built-in single-worker store has fixed worker/session capacities and uses
an expiration index, so expiry cleanup does not scan a growing mapping. This
optional local mode does not provide cross-worker single consumption. A
deployment that explicitly sets `BFF_REPLAY_REQUIRE_SHARED_STORE=true` must
install an `AtomicReplayStore` through `set_bff_replay_token_store()` (or use
the optional Redis adapter); startup fails closed until the provider declares
and implements fleet-wide atomic consumption. Normal sessions, BFF calls, and
load balancing do not require this feature or any shared store.

## Response

Non-streaming return values are FastAPI JSON responses. Timeouts return `504`.
Authentication, policy, validation, missing export, and method errors retain
their HTTP status semantics; server errors are sanitized and include a
correlation id. Responses carry `X-Request-ID`.

Ordinary results are encoded through a bounded iterator before a response is
created. `BFF_RESULT_MAX_BYTES`, `BFF_RESULT_MAX_DEPTH`, and
`BFF_RESULT_MAX_ITEMS` cap serialized output and structure; an oversized result
returns `413`. Finite synchronous generators and iterators remain ordinary JSON
arrays. They are consumed incrementally, stopped and closed after the first
item beyond the configured aggregate limit, and serialized in the bounded BFF
worker pool instead of the request event loop. An async iterable returned from
an ordinary operation is rejected; declare it with `@bff_stream()` to stream it
incrementally. Stream items are likewise bounded before their serialized bytes
are retained, in addition to the aggregate stream duration/byte/item limits.
Cooperative async generators are the preferred streaming contract. Legacy
synchronous iterators run one `next()` at a time in the bounded thread pool.
Because a Python thread cannot be killed safely, a timed-out or disconnected
request keeps its BFF admission slot until the outstanding `next()` exits and
the iterator closes. Repeated abandoned streams therefore cannot exceed the
configured BFF concurrency, though a permanently blocked iterator can retain
one slot permanently.

Generated sync, async, and streaming proxies decode only 2xx responses. Any
other response except 401 raises `PytinctureBFFError`, whose stable fields are
`status_code` (`status` is an alias), `operation`, and `correlation_id`. Its
message is built only from those fields; generated clients do not read an error
response body. A 401 keeps the established behavior of redirecting the browser
to the application login page and returning `None`. A rejected replay proof may
refill and retry once before a remaining 409 becomes a typed error.

`@bff_stream()` defaults to `text/event-stream` and newline-delimited JSON.
`raw=True` forwards string/byte chunks without JSON framing. The declared
`media_type` is preserved.

Trusted application code runs in the normal thread/async execution path by
default. `BFF_ASYNC_EXECUTION_MODE=worker-thread` is an additive option for
trusted, non-streaming coroutine methods and async policy hooks that might call
blocking code without yielding. It runs each such stage on a bounded worker
thread with its own event loop, keeping the request loop responsive. A timed-out
worker thread retains its BFF admission slot until it exits because Python
threads are not safely killable. The default remains `event-loop`, and explicit
async-generator streaming remains on its cooperative event-loop path. Code that
deliberately reuses an async client or other object bound to the ASGI event loop
must keep the default or create that resource inside the worker-thread call.

`BFF_EXECUTION_MODE=isolated-process` is an explicit harder boundary
for non-streaming methods: each call runs in a killable child with per-worker
and per-identity admission, wall-time, CPU, memory where the operating system
supports it, and serialized-output limits. Per-identity fairness uses a stable,
opaque key, so opening more sessions does not multiply process capacity.
Streaming operations return `501` in this optional mode. Process isolation is
not required for ordinary trusted BFF modules and does not change their browser
API.

## Generated proxy behavior

Generated browser classes preserve the exported class, method, and attribute
names. They construct the route from the module-relative identifier and call
the declared HTTP method. Sync methods retain synchronous browser requests for
the 1.x compatibility period and receive an additive `<method>_async`
companion. Async and streaming methods use deadline-bounded asynchronous
fetch/iteration behavior. Each generated BFF module exposes
`PytinctureBFFError` for callers that want to catch the typed failure.
Authentication redirects and optional replay-token refill are runtime concerns
but may not change existing user method signatures. Replay refill is
single-flight and cancellation-shielded: concurrent callers may consume more
than one configured batch, rechecking the shared pool and starting the next
refill only when needed. Cancelling one caller cannot cancel the refill shared
by other callers. A best-effort refill that fails after a completed mutation
may be reported separately but must not replace the completed mutation result
or cause it to be sent again.

## Evolution

Version 1 permits additive response headers, optional request fields, new
decorator options with defaults, and new error details that do not expose
secrets. Removing the compatibility route, changing canonical body fields, default method,
stream framing, or generated public method signatures requires a new contract
version and a major-release migration path.
