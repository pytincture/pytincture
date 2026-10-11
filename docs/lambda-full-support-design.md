# Full Pytincture support on Lambda, including streaming

Status: **historical design, superseded for implementation by
[Lambda Web Adapter + Uvicorn](aws-lambda.md)**. The custom Mangum runtime below
was not implemented. The Web Adapter chat path has since been verified on AWS;
the broader acceptance requirements in this document remain qualification work.
Reviewed October 11, 2026
against repository revision `abc373e` and current upstream documentation.
The existing [Mangum assessment](mangum.md) describes the buffered baseline.

## Original proposal (superseded)

Build an optional Lambda integration that reuses Mangum's event-to-ASGI request
translation and supplies its own streaming ASGI response cycle and Lambda
Runtime API driver. Run the complete `create_app()` application through it.
Use a Regional API Gateway REST API with streaming enabled for every dynamic
route. Keep the existing BFF URLs, authentication, policy, browser packaging,
and response formats.

This is a **Mangum-based extension**, not a configuration switch on ordinary
`Mangum(app)`. Mangum's HTTP cycle collects the body, and its adapter invokes
that cycle before producing a proxy response. A custom event handler alone
cannot remove this buffering. Pin the supported Mangum version, put its request
translation behind a small compatibility module, and consider upstreaming a
response-cycle extension point. Avoid a global monkey patch.
[Mangum HTTP behavior](https://mangum.fastapiexpert.com/http/),
[adapter implementation](https://github.com/Kludex/mangum/blob/main/mangum/adapter.py).

Python streaming requires a custom Runtime API integration or Lambda Web
Adapter. Changing only the ingress to streaming leaves an ordinary Python
handler's response buffered.
[Lambda language support](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html).

If maintaining Mangum is optional, AWS Lambda Web Adapter plus Uvicorn is the
lower-maintenance alternative: it already supplies the HTTP-to-runtime
streaming bridge. It replaces Mangum in that execution path. Use it as a
reference implementation during qualification; it does not solve shared state,
browser-disconnect propagation, or framework lifecycle issues by itself.
[AWS Lambda Web Adapter](https://github.com/aws/aws-lambda-web-adapter).

## Public topology

```text
Browser / API client / MCP client
                |
          service.example.com
                |
       CloudFront (optional initially)
          |                  |
  explicit public       dynamic/default behavior
  immutable assets      caching disabled, all methods
          |                  |
      private S3       Regional REST API, STREAM
      origin                 |
                      Lambda custom Python runtime
                             |
                      Mangum request translation
                             |
                      full Pytincture ASGI app
                             |
                      streaming response cycle
                             |
                      Lambda Runtime API
```

Start with a root-mapped REST API custom domain and stream assets through
Lambda too. That gives a complete functional baseline before adding a CDN
split. No browser URL changes or special `/stream` endpoint are needed.
Ordinary JSON is simply a short streamed HTTP response; the application still
produces its normal JSON content. Do not execute a BFF once to classify its
response and then execute it again through another adapter.

Configure `ANY /` and `ANY /{proxy+}`, with `AWS_PROXY`, integration method
`POST`, and `responseTransferMode: STREAM`. The integration URI uses
`2021-11-15/functions/<function-arn>/response-streaming-invocations`, rather
than the buffered invocation URI. Scope Lambda invoke permission to the API
and deployed alias. Public API mappings must expose the origin root; stripping
an internal stage name does not make a public `/prod` prefix supported.
[REST streaming integration](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode-lambda.html).

API Gateway HTTP APIs are not a streaming ingress. A Function URL with
`InvokeMode=RESPONSE_STREAM` is a secondary profile with its own event/metadata
tests and access controls; do not assume REST API metadata is interchangeable.
Check streaming availability in the target region and validate the chosen VPC
topology before selecting that profile.
[Gateway streaming](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode.html),
[Lambda streaming](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html).

## Runtime and response transport

The proposed optional integration has four responsibilities:

| Component | Responsibility |
| --- | --- |
| `events.py` | Pinned Mangum compatibility, event validation, ASGI scope/body construction, trusted ingress normalization |
| `asgi.py` | Streaming response state machine, bounded writes, ASGI receive/disconnect behavior, header preservation |
| `runtime.py` | Runtime API invocation loop, context/tracing, deadline, response/error submission, environment retirement |
| `lifespan.py` | One app and event loop per environment, startup validation, MCP lifespan, bounded shutdown |

These are proposed module names, not currently available imports.

The runtime owns the invocation loop. It must not run alongside the managed
Python runtime's separate event loop/response submission. Package it as a Linux
container with a custom entrypoint, or as `provided.al2023` with an executable
bootstrap and a packaged Python interpreter. A container is preferable for
Pytincture's browser runtimes and optional SAML native libraries.

1. Load configuration, construct `create_app()` once, register existing policy,
   authenticator and replay hooks, and complete ASGI startup before admitting
   requests. Initialization failures must prevent service.
2. Fetch `/2018-06-01/runtime/invocation/next`; establish context from the
   request ID, optional invocation ID, deadline and tracing headers. Keep the
   long poll free of an ordinary HTTP read timeout. Process one invocation at
   a time in the initial standard-Lambda profile.
3. Translate the event; invoke the full ASGI middleware/router stack exactly
   once. Preserve `aws.event`, `aws.context` and a per-request shallow copy of
   lifespan state. Never dispatch directly to the BFF function.
4. Complete the HTTP response and bounded request cleanup, check Runtime API
   acknowledgement, then fetch the next invocation. Do not retry application
   execution after an ambiguous transport failure.

[Runtime API contract](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-api.html).

### Streaming wire contract

POST the response to
`/2018-06-01/runtime/invocation/<request-id>/response`, using
`Lambda-Runtime-Function-Response-Mode: streaming`, HTTP/1.1 chunked transfer,
and the HTTP integration content type
`application/vnd.awslambda.http-integration-response`.
The payload is metadata JSON, eight NUL bytes, then raw response bytes.
The transport owns HTTP chunk framing; application chunks must not be framed
twice. No base64 response envelope is used.
[Custom runtime streaming](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-custom.html),
[HTTP integration format](https://aws.amazon.com/blogs/compute/using-response-streaming-with-aws-lambda-web-adapter-to-optimize-performance/).

For REST API, metadata contains `statusCode` and `headers`/
`multiValueHeaders`/`cookies` as appropriate. Preserve separate `Set-Cookie`
values, and do not comma-join cookies. Validate metadata before committing;
its eight-byte separator must occur within the first 16 KB. Do not emit the
buffered `body` or `isBase64Encoded` fields.
[REST metadata specification](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode-lambda.html).

The response state machine must:

- Retain `http.response.start` until it can validate/commit the response, then
  flush metadata and successive `http.response.body` messages incrementally.
  It must never collect the whole response in a bytes buffer.
- Bound transport memory by bytes, including a single oversized ASGI message.
  Slice writes into bounded pieces and await network flow control. A bounded
  number of queue items is insufficient when items have arbitrary size.
- Preserve status, redirects, content type/encoding, caching/CSP headers,
  service-worker scope, `Vary`, correlation IDs and duplicate response headers.
  Drop hop-by-hop headers; handle HEAD, empty responses, 204/304 and declared
  content length correctly. Validate any retained length against emitted data.
- Supply the buffered request as ASGI `http.request`. Subsequent `receive()`
  calls must wait for actual local closure/cancellation rather than immediately
  returning `http.disconnect` and prematurely cancelling Starlette streams.
- Reject unsupported ASGI extensions explicitly. Do not advertise sendfile,
  trailers or WebSocket support that the adapter has not implemented. Basic
  file responses must use ordinary ASGI body messages.
- Distinguish errors before commitment, errors after commitment, missing final
  body, duplicate starts and application exceptions after the final body.
  Return a sanitized HTTP error when possible before commitment. Afterwards,
  abort/report the incomplete stream; never append JSON to an arbitrary binary
  or raw stream or attempt to change its already-sent status.

Runtime error trailers can report failures after streaming starts; declare
them at the start of the Runtime API request. Do not promise that browsers or
every gateway will expose these trailers. Logs and metrics remain necessary.
[Runtime streaming error rules](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-custom.html).

### Event fidelity and trust

REST events preserve multi-value parameters, but do not retain the original
raw query string in the same way as v2 events. Mangum reconstructs it and
normalizes request headers. Test duplicate query values, percent encoding,
Cookie handling and SAML/OAuth callbacks. Correct the compatibility wrapper
where required, without inventing bytes already lost at ingress. Exact raw
request signatures are not a universal compatibility promise.
[Mangum request translation](https://raw.githubusercontent.com/Kludex/mangum/main/mangum/handlers/api_gateway.py).

Use a fixed public origin, exact allowed hosts and authenticated ingress
metadata. Strip caller-supplied forwarding headers before installing trusted
values. With CloudFront, establish an explicit verified viewer-address policy;
do not blindly treat the leftmost X-Forwarded-For address as authentic. Test
Pytincture's edge-client diagnostic and per-peer admission through the deployed
path. Restrict alternate origin entrypoints or apply equivalent protections.

## Pytincture feature coverage

This table is the implementation/qualification target, not a claim that these
features have already been tested on AWS.

| Existing feature | Required treatment |
| --- | --- |
| BFF objects, methods, properties, GET/POST/PUT/PATCH/DELETE, sync/async execution | Preserve the existing dispatcher, request/result limits, policy hooks, admission, audience, CSRF and replay checks. |
| `@bff_stream()`, async generators, legacy sync streams, explicit StreamingResponse | Forward serialized bytes incrementally; retain idle, write, duration, item, byte and cleanup rules. |
| Raw streams, binary responses, downloads | No encoding conversion or injected stream records; compare hashes against direct ASGI output. |
| Loader pages, Pyodide, MicroPython, portable runtime, widgetsets | Ship matching manifests, runtime files, browser packages and integrity metadata. Execute UI Python in the browser as before. |
| Appcode ZIPs, wheels, declared public assets, prebuilt appcode | Preserve authorization, source validation, MIME, ETag/range behavior where implemented and bounded download lifetime. Stream protected files through the application. |
| Password, OAuth, Microsoft and SAML authentication | Shared signing keys/configuration, correct public callbacks, separate cookies, native dependency packaging, real provider-flow tests. |
| Signed sessions, logout, optional replay/revocation | Ordinary sessions remain stateless; configured fleet-wide revocation/replay needs shared storage. |
| API-client credentials and token revocation | Add a shared registry provider; immutable SQLite snapshots do not preserve live administration across environments. |
| API docs, diagnostics, logs, health/readiness | Same routes, access controls and redaction. CDN caching disabled for dynamic/private results. |
| MCP stateless streamable HTTP | Preserve the mounted transport and combined lifespan, JWT verification, origin/host checks and its supported request/stream behavior. |
| Process-isolated non-streaming BFF | Qualify spawn, Pipe, CPU/address-space limits, failure cleanup and child reaping in the actual Lambda image. |
| Service worker | Preserve its same-origin script, scope and manifest cache rules; use a release-stable asset namespace. |

Relevant implementation: [factory](../pytincture/factory.py),
[routes](../pytincture/backend/app.py),
[streaming](../pytincture/backend/streaming.py),
[MCP](../pytincture/backend/mcp.py),
[isolated execution](../pytincture/backend/execution.py), and
[BFF contract](contracts/bff-v1.md).

Two existing restrictions remain explicit: isolated-process BFF execution does
not support streaming, and streaming BFF methods cannot be exposed as MCP tools.
MCP's HTTP transport streaming is a separate capability. Lambda support does
not change those framework contracts.

## Framework changes needed for complete support

### Preserve the stream protocol

Pytincture's default `@bff_stream()` uses newline-delimited JSON even though its
default media type is `text/event-stream`. `raw=True` forwards application
framing. The transport must preserve both forms exactly. Do not insert SSE
comments, transform records into `data:` events or assume EventSource is the
existing client. Heartbeats require explicit protocol support; they are unsafe
for arbitrary bytes. Disable buffering compression for interactive streams.

Expose a request completion/cleanup signal to the Lambda integration, including
stream termination reason and outstanding worker ownership. Current bounded
streams can terminate on a limit without raising an exception to the outer
adapter. That must not be misreported as an ordinary successful completion in
Lambda observability. Preserve the current wire contract; a future typed
terminal-error protocol would need separate versioning.

### Invocation lifetime, cancellation and cleanup

Maintain one persistent loop and ASGI lifespan per execution environment,
including MCP's combined lifespan. Reused connections must tolerate freeze/thaw.
Shutdown is best effort, not a durable commit hook. A separate buffered Mangum
profile retains its own documented per-invocation lifespan semantics.

Convert each Lambda wall-clock deadline to a local monotonic deadline and
reserve time for cleanup. Bound the entire request, including auth, policy,
archive building, MCP and response background callbacks. The effective deadline
is the minimum of the configured stage/stream budget and the remaining
invocation budget. Keep the current 300-second BFF stream default unless the
application explicitly changes it. An illustrative starting Lambda timeout is
360 seconds, with a five-second cleanup reserve; validate the total path rather
than relying on those numbers alone.

AWS explicitly says a broken client connection does not stop a streamed Lambda
response. ASGI write backpressure here measures delivery to AWS, not proof
that the browser is reading. Immediate network-disconnect equivalence with
Uvicorn cannot be promised.
[Lambda disconnect behavior](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html).

For applications requiring prompt cooperative cancellation, add an optional
shared stream-control store and authenticated cancellation endpoint. A stream
ID must be bound to its owner/application, have a TTL, and be checked under
bounded store deadlines. A user stop action cancels local fetch and sends a
separate cancellation request. A renewable client lease can cover lost tabs or
networks; its expiry defines the maximum orphan lifetime. This requires client
support and a shared backend because requests land in different environments.
Reserve capacity for the control endpoint so active streams cannot starve
their own cancellation requests. It does not make blocking Python threads
killable or add automatic cancellation to arbitrary MCP clients.

Keep sync-worker admission until the underlying worker exits, as today.
Before returning an environment to service, drain cleanup under a deadline.
If a worker remains stuck, retire the environment after response submission
rather than admitting another request alongside abandoned work. Terminate and
reap isolated children. Add explicit tracking for request-owned tasks without
cancelling application lifespan/MCP service tasks. Response background callbacks
must complete within the invocation; detached post-return work is not durable.

### Shared API-client registry

`api_clients.py` currently reads SQLite and checks each token's client revision
against it. Add an injectable provider for lookup, create, rotate, enable,
disable, grants and audit, retaining SQLite for ordinary deployments. A shared
provider must update credentials/grants/revision atomically, use authoritative
reads for revocation, fail closed on unavailable storage, and preserve the
current hashing and application-scoping semantics. Extend the existing CLI to
use the same provider. A Redis-backed implementation can reuse the deployment's
optional shared-service choice, but requires atomic operations beyond a simple
mapping facade.

Do not copy a writable registry into `/tmp` and call that shared state. Keep
ordinary signed-cookie sessions independent of this feature; neither Redis
nor sticky routing becomes mandatory for every Pytincture application.

### Stable browser release identity

`FRONTEND_INSTANCE_UUID` is currently random per app instance and participates
in the worker's cache namespace. Add a typed optional deployment asset ID,
defaulting to current behavior outside immutable deployments. Derive its Lambda
value from the release's frontend/runtime/widget manifests and relevant browser
configuration. Every replica of the same release uses the same value.

During rollouts, old and new pages must still resolve their matching immutable
assets. Retain release-addressed S3 objects and include the release selector in
cache/origin mapping; merely assigning every version the same UUID would hide
stale code. Private appcode must never enter the public worker/CDN cache.

## Assets and persistence

The current Pyodide WASM exceeds the buffered Lambda response ceiling. The
streaming path handles it without base64 inflation, subject to the streamed
limit. CDN offload is subsequently an efficiency improvement, not a prerequisite
for private appcode correctness.

Export only explicitly public manifest-authorized files to private S3 behind
CloudFront origin access control. Map both `/frontend/...` and
`/{application}/frontend/...`; keep service-worker scripts on the application
path with their generated headers/query behavior. Do not route a broad wildcard
to public S3 before checking Pytincture's manifest rules. Other public application
assets require the same explicit admission/export rules.

CloudFront's dynamic behavior must forward authentication cookies,
Authorization, Origin, CSRF/replay headers, relevant MCP headers and query
parameters with caching disabled. Verify response/idle and completion timeouts
and disable streaming compression that delays first bytes. Retain private
appcode and session-specific replay packages on the application origin. The
existing required-prebuilt/session-specific-replay incompatibility still applies.

Package modules read-only. Use `/tmp` only for disposable caches. Application
data, shared mutable files and jobs that outlive a request need an explicit
external persistence/execution design; an adapter cannot supply durable global
Python objects. Review connection counts against fleet concurrency.

## Limits and the meaning of full support

For standard synchronous Lambda, the relevant ceilings include a 900-second
invocation, a 6 MB request event, and a 200 MB streamed response. Event encoding
and metadata consume request budget. Enforce lower application budgets and
test boundary rejection; streaming does not enlarge uploads.
[Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html).

REST streaming supports up to 15 minutes, with a five-minute idle timeout for
Regional/private endpoints and 30 seconds for edge-optimized endpoints. It
does not support request streaming, gateway response caching, gateway content
encoding or VTL response transformation. Any CloudFront layer adds its own
timeouts. Lambda and gateway bandwidth caps both apply; size and time budgets
must account for the slower segment.
[API Gateway constraints](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode.html).

Do not promise literal unlimited server parity. Persistent WebSockets, arbitrary
streaming uploads, infinite streams, larger-than-service-limit transfers,
in-memory global state and reliable work after return need different contracts
or compute. They cannot be made transparent by extending Mangum. For long
operations, a job API with explicit polling/resumption is a separate application
feature; retries must not silently re-run a non-idempotent BFF.

The target is every currently supported Pytincture feature, with documented
Lambda bounds, preserving existing restrictions. Any remaining failed feature
is a release blocker for the comprehensive profile, not silently disabled.

## Packaging and operational profile

- Pin Pytincture, Mangum, Python, framework dependencies and optional extras in
  a Linux image for the selected architecture. Pin image digests; retain native
  SAML libraries and the exact browser manifests. No dev reloader.
- Add a deployment capability validator: reject non-streaming ingress for the
  full profile, unsupported transport types, public path prefixes, missing
  shared providers for requested fleet semantics and contradictory deadlines.
  Infrastructure tests must inspect actual gateway configuration; environment
  variables alone cannot prove the ingress is correct.
- Store stable signing material outside the image and load it through the
  deployment's secret mechanism. Retain exact host/origin, cookie, CSP, CSRF,
  policy and no-auth controls from the production runbook.
- Use reserved concurrency to protect dependencies; consider provisioned
  concurrency only after measuring cold starts. One active stream occupies a
  standard Lambda invocation. Rough capacity planning is arrival rate times
  average stream duration, plus short requests and control capacity.
- Record ingress request ID, Lambda request ID, Pytincture correlation ID,
  release ID, first-body time, bytes, termination reason, cleanup duration,
  cold/warm state, throttling and memory. Never log cookies, tokens or bodies.
- Roll back by immutable alias and matching asset release. Keep the buffered
  Mangum profile for small-request deployments, but never silently downgrade
  the comprehensive profile to buffering.

## Implementation sequence and acceptance gates

1. **Transport proof.** Build the Runtime API driver and streaming ASGI cycle
   around pinned Mangum translation. Use a fake Runtime API server for exact
   framing/error/acknowledgement tests, then deploy an isolated streaming REST
   API proof. Require first body data to arrive before the producer finishes.
2. **Framework integration.** Add lifespan/deadline/cleanup integration and the
   stable browser asset ID. Run existing BFF/auth/appcode/streaming conformance
   suites through the new event adapter as well as direct ASGI.
3. **Complete distributed features.** Add the registry provider and CLI support;
   test optional shared replay/revocation. Implement stream-control leases only
   when selecting that explicit cancellation profile.
4. **Full UI and MCP qualification.** Exercise real browsers, both interpreters,
   real provider callbacks, native SAML, MCP and isolated-process BFF in the
   deployed image. Add CDN offload after the direct ingress baseline passes.
5. **Release and operate.** Publish reproducible infrastructure, images,
   configuration reference, support matrix and measured AWS test evidence.

The release evidence must include:

| Area | Required test |
| --- | --- |
| Incremental delivery | Timestamp at least three delayed producer records at the real client; first record arrives before final production. Exercise direct API and CDN paths with compression on/off as supported. |
| Protocol fidelity | Unicode split across network chunks, raw SSE, NDJSON, raw bytes, zero-length chunks, ordinary JSON and client error handling. Compare complete bytes and decoded records to direct ASGI. |
| Response semantics | Multiple auth cookies, redirects, duplicate headers, HEAD/304/204, supported ranges, invalid metadata, oversized headers, pre/post-commit failure and absent final body. |
| Large transfers | WASM, private appcode and binary results over 6 MB; hashes match and no full-body transport buffer. Exercise configured stream and AWS boundaries. |
| Lifecycle | Repeated warm requests, cold starts, freeze/thaw, MCP initialization, startup failure, bounded background callbacks, expired pools and abandoned workers. |
| Termination | Browser abort, explicit cancellation/lease expiry if enabled, upstream stall, failed Runtime API writes, near-Lambda deadline and partial download. Do not assert impossible immediate browser-disconnect propagation. |
| Fleet state | Login on one environment and BFF on another, revision rotation/revocation across environments, replay single consumption when enabled, service-worker behavior during scale-out and rollout. |
| Security | Host/origin/forwarded-header spoofing, CSRF, traversal, undeclared assets, private cache leakage, MCP JWT checks and every supported identity-provider flow. |
| Resource isolation | Actual Linux child process startup, result size caps, CPU/memory/wall deadlines, reaping and parent readiness following failure. |
| Capacity | Long streams alongside short calls and cancellation controls, dependency limits, throttling, cold-start/first-byte latency and cost measurements. |

A Lambda console invocation or a local buffered TestClient response cannot
prove incremental delivery. AWS streaming ingress and a real streaming HTTP
client are required before this design becomes a supported deployment.
