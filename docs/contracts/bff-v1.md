# BFF transport and generated-stub contract — version 1

This contract covers calls exported with `@backend_for_frontend` and the
browser proxies generated into `appcode.pyt`. It is the BFF contract supported
by Pytincture 1.x.

## Operation discovery

- Only classes decorated with `@backend_for_frontend` are exported.
- Public methods are operations. Public assigned/annotated attributes are
  read-only `GET` operations.
- Private names beginning with `_` are not exported.
- Module identifiers are relative POSIX-style paths under `MODULES_PATH` and
  include `.py`.
- Static manifest validation occurs before application code is imported.

## HTTP request

The route is:

```text
/classcall/{file_path}/{class_name}/{function_name}
```

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
secrets. Changing the route shape, canonical body fields, default method,
stream framing, or generated public method signatures requires a new contract
version and a major-release migration path.
