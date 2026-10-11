# Pytincture on AWS Lambda with Mangum

For live streaming, start with [Lambda Web Adapter + Uvicorn](aws-lambda.md).
That path has a working Bedrock reference app and a reusable deployment
template. The assessment below describes the buffered Mangum alternative.

Mangum can adapt Pytincture's existing ASGI application to Lambda without
changing BFF signatures or browser clients. It is a reasonable option for
short, stateless HTTP operations. It is **not full deployment parity** with
Pytincture under Uvicorn: live streaming, large responses, persistent processes,
and browser-asset delivery need separate consideration.

Assessment date: October 6, 2026. Local checks passed against both the published
RC12 wheel and the RC13 development tree at `abc373e`, using Python 3.13.5 and
Mangum 0.22.0 in separate environments. This is an integration guide, not a
claim of AWS production qualification. The installation example uses RC12.

## What works, and what changes

| Pytincture feature | With ordinary Mangum on Lambda |
| --- | --- |
| Normal JSON BFF calls, including async methods and declared HTTP verbs | Compatible within the endpoint's duration and payload limits. Existing method signatures and generated clients remain applicable. |
| Loader pages, redirects, API documentation, health endpoints | Compatible. Publish at the origin root and retain query strings, response headers, and cookies. |
| Pyodide/MicroPython UI execution | Still executes in the browser. Lambda serves its assets and BFF requests; it does not execute the browser interpreter. Large assets need compression or separate delivery. Portable mode still requires its normal build and manifests. |
| Small appcode ZIPs, wheels, and binary responses | Mangum base64-encodes binary bodies for the gateway. They work when the complete encoded response fits. |
| Browser service worker | Can operate normally over HTTPS with the application scope and headers preserved. It does not eliminate Lambda cold starts on API calls. |
| Signed sessions and OAuth/SAML redirects | Architecturally compatible with shared signing configuration and correct public origin/callback URLs. Identity-provider flows and optional native dependencies still require deployment testing. Sticky routing and Redis are not required for ordinary signed-cookie sessions. |
| Finite BFF generators | Their complete body can return, but all chunks are buffered. The client loses incremental delivery. |
| Live BFF streaming, SSE/token-by-token chat | Not preserved by Mangum. An open stream can consume the invocation until a timeout without delivering an early chunk. |
| MCP | The current transport is stateless streamable HTTP, which helps with scaling. Bounded request/response operations may fit; SSE/progress behavior and repeated transport lifespans require separate testing. Do not assume complete MCP compatibility. |
| Long BFF jobs or work continuing after an HTTP response | Not a durable in-process execution model. Move long work to a job service and return an identifier for polling, or keep that workload on an always-running service. |
| WebSockets added by an application | Mangum's HTTP adapter does not provide persistent ASGI WebSocket connections. API Gateway WebSocket APIs require a different event-based implementation. |
| In-memory application data, mutable local files, background schedulers | Not shared or durable across Lambda environments. Use external persistence where the application needs it. |
| Process-isolated BFF execution | Requires a Lambda-specific resource/process test; do not assume container-level CPU/memory/process behavior carries over. Start the pilot with the normal trusted-thread mode. |

[Mangum explicitly buffers ASGI response chunks](https://mangum.fastapiexpert.com/http/).
Its adapter handles HTTP Lambda events, not an ASGI network listener. This
changes backpressure and disconnect semantics too: Pytincture's ASGI write
deadlines measure writes into the adapter, not the browser's connection.

## Minimal handler

Keep the Lambda entrypoint outside the browser application directory:

```text
service/
├── lambda_handler.py
├── requirements.txt
└── apps/
    ├── dashboard.py
    └── dashboard_data.py
```

Use an existing valid Pytincture application in `apps/`, including its widget
metadata and any installed application dependencies. Example requirements:

```text
pytincture==1.0.0rc12
mangum==0.22.0
```

Select the Pytincture authentication extras your application uses, and pin its
widgetset and backend dependencies as usual. Mangum is a deployment dependency;
it does not need to become a dependency of every Pytincture installation.

```python
# lambda_handler.py
from pathlib import Path

from mangum import Mangum
from pytincture import PytinctureConfig, create_app

config = PytinctureConfig.from_env(
    modules_path=str(Path(__file__).resolve().parent / "apps"),
    default_application="dashboard",
)
app = create_app(config)
handler = Mangum(app, lifespan="on")
```

Configure Lambda's handler as `lambda_handler.handler`. Construct the app once
at module import, so warm invocations can reuse it. Do not call
`launch_service()`, spawn the development launcher, or start Uvicorn with this
adapter. Register any existing policy/authentication hooks on `app` before
constructing the handler.

Keep lifespan enabled: Pytincture registers startup validation, and MCP adds a
transport lifespan. Mangum runs startup and shutdown around **each invocation**,
even when the app object is reused. Application-owned lifespan resources must
tolerate repeated open/close cycles. Turning lifespan off would skip those
checks and does not solve resource-lifecycle bugs.
[Adapter](https://mangum.fastapiexpert.com/adapter/),
[lifespan behavior](https://mangum.fastapiexpert.com/lifespan/).

For a first deployment, use the supported `python3.13` Lambda runtime, build
dependencies for its Linux architecture, and connect an API Gateway **HTTP API**
using payload format 2.0 and a `$default` catch-all route/stage. Preserve all
methods, including OPTIONS, and both `/` and nested paths. A root-mapped custom
domain is preferable for production. A buffered Function URL is another option
when its access model fits the application.

An HTTP API has a **30-second maximum integration timeout**. Set application
and Lambda timeouts below that limit with time left for initialization,
serialization, and response delivery; a 25-second Lambda timeout is a starting
point for short operations, not a guarantee that every Pytincture request fits.
Lambda's own maximum is 15 minutes, which does not override the gateway timeout.
[HTTP API quotas](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-quotas.html),
[Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html),
[supported runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html).

## The large browser-asset issue

The default Pyodide runtime includes
`frontend/pyodide/0.29.3/full/pyodide.asm.wasm`, which is **8,647,684 bytes** in the
reviewed tree. The ordinary adapter produced an approximately **11.53 MB** JSON
response after base64 encoding. That cannot pass through Lambda's **6 MB
buffered synchronous response limit**, even though the HTTP API itself permits
10 MB. Likewise, Lambda's request limit applies to the encoded event, including
headers and body; gateway limits alone are not the application's usable budget.

For binary responses, base64 adds roughly one third before the JSON envelope.
Budget below roughly 4.5 MB of unencoded binary data, with room for headers and
the envelope. Measure the actual serialized response. Pytincture's own appcode,
BFF-result, and streaming limits are not automatically reduced to AWS limits.

### Small proof of concept: compression

A gzip wrapper can make the current WASM fit without changing Pytincture:

```python
from starlette.middleware.gzip import GZipMiddleware

handler = Mangum(
    GZipMiddleware(app, minimum_size=1000, compresslevel=6),
    lifespan="on",
)
```

With `Accept-Encoding: gzip`, the local experiment produced about 2.83 MB of
compressed WASM and 3.78 MB for the encoded response. Decompression reproduced
the original file. Mangum retains the binary encoding and content-encoding
metadata needed by the gateway and browser.

This is conditional: requests without gzip support still exceed the limit,
compression consumes invocation CPU, and already-compressed ZIPs/wheels may not
shrink enough. Preserve `Content-Encoding` and `Vary: Accept-Encoding` through
the edge. Compression is not a universal large-response solution and does not
enable streaming. Validate every asset and the actual browser/gateway path.

### Full UI deployment: serve immutable assets separately

The preferable architecture for a full UI is a single public HTTPS origin:

```text
Browser → CloudFront at the service hostname
            ├─ selected immutable runtime assets → S3
            └─ pages, auth, BFF, protected appcode → HTTP API → Lambda/Mangum
```

Upload only the framework's explicitly public assets from the exact deployed
release; do not upload the application source directory. Preserve the asset
bytes used by integrity manifests, MIME types (including `application/wasm`),
and versioning. Invalidation/versioned origin paths must keep the loader and
assets from the same release together.

The current browser asks for paths such as
`/dashboard/frontend/pyodide/0.29.3/full/pyodide.asm.wasm`. Configure the static
behavior and URI mapping for those **application-prefixed** paths as well as
any `/frontend/...` paths used by the deployment. An S3 behavior for only
`/frontend/*` misses the normal application-prefixed requests. A separate
hostname alone does not rewrite Pytincture's generated URLs.

Keep the generated service-worker script on the application origin, with its
query parameters and `Service-Worker-Allowed` header. Keep private appcode,
auth, and BFF responses on the authenticated origin with shared caching
disabled and cookies/authorization/query strings forwarded appropriately.
Large protected appcode or private wheel downloads still need a separately
designed authorized delivery path; prebuilding does not remove their payload
limit. Splitting CloudFront origins is deployment work, not something Mangum
configures automatically.

## Pytincture-specific deployment details

- **Origin root:** Pytincture uses root-relative BFF/auth/frontend URLs. A
  public `/prod` or `/api` prefix is not supported. Mangum's base-path stripping
  does not rewrite generated URLs, redirects, or worker scopes. Use an HTTP
  API `$default` stage or a custom domain mapped at the root.
- **Authentication:** supply the same stable `SAML_SECRET_KEY` across
  environments, exact `PYTINCTURE_ALLOWED_HOSTS`, and
  `PYTINCTURE_CANONICAL_ORIGIN`. Register that origin's provider callbacks and
  preserve all cookies. Gateway JWT/IAM authentication does not automatically
  become a Pytincture user session. Follow the existing
  [production configuration](production-deployment.md), including its proxy
  trust rules; do not blindly enable proxy trust to compensate for routing.
- **Packages:** include optional OAuth/password/SAML/MCP dependencies only as
  required. Native libraries, especially SAML's XML stack, must match Lambda's
  Linux runtime/architecture. A container image can simplify packaging but
  does not remove HTTP response or execution limits.
- **Files and state:** package source as immutable deployment files. `/tmp`
  is disposable per-environment storage, not a shared database. The current
  optional API-client credential registry uses SQLite: an immutable bundled
  snapshot can be read, but CLI credential changes require distribution to
  every environment. A `/tmp` copy cannot provide coordinated revocation.
- **Caches and admission:** archive/graph caches, counters, connection pools,
  and local replay proofs are per environment. They may be reused while warm
  but disappear on replacement. Use existing shared-store options when
  fleet-wide revocation or one-time replay consumption is required; normal
  sessions remain cookie-based. Coordinate fleet limits at the gateway.
- **Prebuilt appcode:** build it during packaging to reduce request-time
  archive work, following [production deployment](production-deployment.md).
  Include both `.pyt` and `.pyt.json`, preserve the matching source, and retain
  the documented incompatibility with session-specific replay clients.
- **Service-worker cache churn:** `FRONTEND_INSTANCE_UUID` is generated per
  app instance. Pages served by different Lambda environments can select
  different cache namespaces and trigger extra worker updates/downloads.
  This is not a session failure. Measure it during scale-out; disabling the
  optional worker is possible, but gives up its caching behavior.
- **Cold starts:** Python imports, BFF discovery, and any application startup
  work add latency. Mangum does not make every URL faster or guarantee a
  fixed startup saving. Browser runtime downloads and Lambda initialization
  are separate costs. Measure warm and cold behavior before choosing this
  topology for performance reasons.

Lambda can freeze the environment after an invocation; background execution
must not be relied upon to complete work after return.
[Execution lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html).

## If live streaming is required

Consider **AWS Lambda Web Adapter with Uvicorn** and a streaming-capable ingress,
or retain the existing container/server deployment. Web Adapter is a different
integration: it forwards HTTP to the running application and supports Lambda
response streaming. A streaming Function URL or correctly configured API
Gateway **REST API** can provide the streaming path. API Gateway HTTP APIs do
not provide this feature. Merely changing a gateway setting cannot make
Mangum's buffered handler stream.

AWS now supports REST API response streaming; advice that all API Gateway
streaming is impossible is outdated. The adapter, Lambda invocation mode, and
ingress must all be configured consistently. Web Adapter still has Lambda
duration, environment lifetime, state, and resource constraints, so it also
needs qualification with Pytincture's cancellation and streaming tests.
[AWS Lambda Web Adapter](https://github.com/aws/aws-lambda-web-adapter),
[API Gateway response streaming](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode.html).

## Verification and remaining qualification

Local synthetic HTTP API v2 events passed through the real Mangum handler for:

- repeated health/readiness invocations with lifespan enabled;
- the application loader and trailing-slash redirect, including raw query
  preservation;
- POST, PUT, PATCH, and DELETE BFF calls;
- a finite BFF stream returned as one complete response;
- a small appcode ZIP's base64 round-trip and archive contents;
- service-worker delivery and its application scope header;
- the oversized uncompressed WASM response and a working gzip/decompression
  round-trip below the buffered response ceiling.

These checks do not exercise AWS's actual payload rejection, a browser, an
identity provider, a complete widget UI, MCP, or Lambda scale-out. Before
claiming deployment support, validate those paths in staging, including
multiple environments with shared signing configuration, cold/warm navigation,
the largest real uploads/downloads, cookie callbacks, and the configured
gateway timeout. No framework runtime changes are required for this pilot.
