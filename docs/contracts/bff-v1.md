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

For body-bearing methods, send a JSON object with the function's argument
names. For example, `dataset_page(page, page_size)` accepts:

```json
{"page": 1, "page_size": 2}
```

Swagger documents this named-input format. The server normalizes it to the
same internal arguments used by generated clients before signature validation.
Positional-only parameters may be named in this format. A `*values` parameter
is represented by a `values` array; extra fields bind to `**kwargs` when
present. If filling a positional gap requires a default that cannot be read
statically, provide that argument explicitly.

Existing generated clients retain their compatible JSON envelope:

```json
{
  "args": [],
  "kwargs": {}
}
```

Generated stubs may send positional values in `args` and named values in
`kwargs`. An object containing exactly those two keys, with an array and an
object respectively, is recognized as the envelope. Other JSON objects are
named inputs validated against the method signature. For a method declaring
both `args` and `kwargs` as its own parameters, use the envelope to disambiguate.
JSON-string bodies and legacy structured positional entries are never decoded
or unwrapped a second time.

Before application code is imported or a BFF class is constructed, Pytincture
rejects duplicate keys, non-finite numbers, excessive bytes/nesting/items,
invalid argument shapes, signature binding errors, missing/unexpected
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

## Optional API bearer credentials

An operator-configured `bff_api_client_registry` additionally enables
`POST /{application}/auth/client-token`. Its JSON body contains exactly
`grant_type: "client_credentials"`, `client_id`, and `client_secret`. This is a
JSON credential exchange, not a general OAuth/OIDC authorization server.
It requires HTTPS outside explicit loopback development, uses bounded auth
ingress and the configured login rate limiter (per peer, per worker), and needs
no browser cookie or user login. Success returns `access_token`, `token_type`,
`expires_in` (900 seconds), `application`, `client_id`, and `grants`.

Client tokens require an exact application match, a currently enabled client
with the same credential revision, a matching module/class/method grant, and
the source-level `@bff_external` decorator. They cannot use session-only or
undecorated legacy-public methods, even if a class grant covers them. The
registry is read on each call; rotation, disabling/re-enabling, or replacing
grants invalidates all earlier tokens for that client. Already admitted calls
and streams are not cancelled. Missing/unavailable registries fail closed.
Cookie user admission remains separate from operator-assigned client grants;
normal BFF policy hooks still receive the client identity and may deny access.
Client identity is exposed as `_user["client_id"]`, with `auth_provider` and
`auth_type` equal to `api_client`, empty email/roles, and the assigned application.
These credentials do not grant access to login, user token issuance, appcode,
or any other non-BFF route. See [setup and lifecycle](../api-clients.md).

`enable_bff_api_tokens=True` enables short-lived, signed BFF access credentials
issued at `/{application}/auth/bff-token` from an authenticated browser session.
They use `Authorization: Bearer <access_token>` and accept the same named JSON
or legacy envelope bodies. A token is restricted to its application and either
external-decorated or legacy public methods (`scope=external`; legacy alias `public`) or the issuer's existing permissions
(`scope=session`). Admission and BFF policies still apply. Expiry is at most
15 minutes and never extends the originating session; configured shared session
revocation also applies. These credentials do not authenticate non-BFF routes.

Bearer credentials replace browser CSRF/replay proofs for that call, as they
are explicitly supplied rather than ambient cookies. Browser-origin validation
still applies when Origin/Fetch Metadata headers are supplied. Invalid bearer
credentials fail closed. Existing cookie clients keep CSRF/replay checks.
With `require_public_bff_token=True`, public allowlisted methods require a valid
bearer token or authenticated browser session; the default preserves anonymous
access. See [API access configuration](../public-api.md#external-methods-and-api-tokens).

`@bff_external` is a source-level external API declaration, not an anonymous
access grant. The method requires a valid scoped token or authenticated app
session, including in no-auth services or when a legacy anonymous grant exists.
`@backend_for_frontend(include_session_methods_in_docs=True)` controls documentation visibility only;
it never broadens external token permissions. The omitted/`None` setting includes
session methods in explicit loopback development mode (`allow_development_auth_origin`
or `enable_dev_email_login`) and hides them otherwise. An explicit boolean
overrides that default; `api_docs_scope="public"` still excludes session methods. Module docs are at `/{application}/{extensionless-module}/bff-docs`,
including nested module folders; aggregate docs return 404. Swagger operations
are class/method paths relative to an extensionless module class-call server URL.
