# BFF transport and generated-stub contract — version 1

This contract covers calls exported with `@backend_for_frontend` and the
browser proxies generated into `appcode.pyt`. It is the BFF contract supported
by Pytincture 1.x.

## Operation discovery

- Only classes decorated with Pytincture's `@backend_for_frontend` are
  exported. Static discovery proves that decorator names and module aliases
  were imported directly from `pytincture.dataclass`; local, unrelated,
  re-exported, or rebound same-named decorators are not security declarations.
- The class marker intentionally exports every public operation; a separate
  method-level export marker is not required by the 1.x developer contract.
- Public methods are operations. Public assigned/annotated attributes are
  read-only `GET` operations.
- Private names beginning with `_` are not exported.
- Module identifiers are relative POSIX-style paths under `MODULES_PATH` and
  include `.py`.
- Static manifest validation occurs before application code is imported.
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
remains a compatibility surface for unauthenticated services and sessions
that already carry an application audience; new generated proxies always use
the scoped route.

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
`kwargs`. For compatibility, the backend also accepts structured positional
entries containing a `value`, and treats a body without `args`/`kwargs` as
keyword arguments. New stubs must emit the canonical form.

State-changing dispatcher requests require `Content-Type: application/json`.
Browser requests must supply the exact service `Origin`; when Fetch Metadata is
present it must also report `Sec-Fetch-Site: same-origin`. Cross-site,
same-site, opaque/null-origin, and malformed browser contexts are rejected.
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
