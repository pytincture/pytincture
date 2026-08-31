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

## Response

Non-streaming return values are FastAPI JSON responses. Timeouts return `504`.
Authentication, policy, validation, missing export, and method errors retain
their HTTP status semantics; server errors are sanitized and include a
correlation id. Responses carry `X-Request-ID`.

`@bff_stream()` defaults to `text/event-stream` and newline-delimited JSON.
`raw=True` forwards string/byte chunks without JSON framing. The declared
`media_type` is preserved.

## Generated proxy behavior

Generated browser classes preserve the exported class, method, and attribute
names. They construct the route from the module-relative identifier and call
the declared HTTP method. Sync methods use synchronous browser requests; async
and streaming methods use asynchronous fetch/iteration behavior. Authentication
redirects and optional replay-token refill are runtime concerns but may not
change user method signatures.

## Evolution

Version 1 permits additive response headers, optional request fields, new
decorator options with defaults, and new error details that do not expose
secrets. Removing the compatibility route, changing canonical body fields, default method,
stream framing, or generated public method signatures requires a new contract
version and a major-release migration path.
