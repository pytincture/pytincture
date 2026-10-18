# AWS Lambda with response streaming

Use **AWS Lambda Web Adapter + Uvicorn** to run Pytincture's normal ASGI app on
Lambda. This is the recommended Lambda starting point when you need live BFF
streaming. Keep `create_app()`, backend policies, `@bff_stream`, and generated
browser clients unchanged. There is no Lambda-specific BFF API, framework fork,
or AWS SDK dependency in the Pytincture package.

The [deployment template](../deploy/lambda-web-adapter/template.yaml) and
[startup script](../deploy/lambda-web-adapter/run.sh) capture the configuration.
The [Bedrock chat example](../examples/bedrock-streaming/README.md) is a complete
application that exercises it. This recipe has live AWS evidence for that app;
it is not a claim of full production qualification for every Pytincture feature.

## Why this preserves streaming

```text
Browser -> streaming ingress -> Lambda Web Adapter -> Uvicorn -> create_app()
                                                                  |
                                                           @bff_stream
                                                                  |
                                                        application generator
```

Web Adapter starts the HTTP server once per execution environment and forwards
invocations to it. Uvicorn runs the normal ASGI lifespan. Warm invocations can
reuse connections and caches, but environments may be discarded at any time.
The adapter writes incremental HTTP response bytes to Lambda's streaming
Runtime API. Ordinary Mangum collects the entire response before returning;
turning on a streaming ingress alone cannot change that behavior.

See the [Web Adapter ZIP setup](https://aws.github.io/aws-lambda-web-adapter/getting-started/zip-packages.html),
[streaming configuration](https://aws.github.io/aws-lambda-web-adapter/configuration/response-streaming.html),
and the [buffered Mangum assessment](mangum.md).

## Application code

Use the same service entrypoint you run locally:

```python
from pathlib import Path
from pytincture import PytinctureConfig, create_app

app = create_app(PytinctureConfig.from_env(
    modules_path=str(Path(__file__).parent / "apps"),
))
# Register your existing authentication and policy hooks on app here.
```

The template sets `PYTINCTURE_DEFAULT_APPLICATION` to the application module
name. If your entrypoint has another name, set `ASGIApplication` to its import
target, such as `myservice:app`. Explicit configuration arguments take precedence
over environment settings: check that your service does not override the
template's stream deadlines with longer values.

Streaming methods remain ordinary Pytincture methods:

```python
import asyncio
from pytincture.dataclass import backend_for_frontend, bff_stream

@backend_for_frontend
class Progress:
    @bff_stream()
    async def count(self):
        for value in range(3):
            yield {"value": value}
            await asyncio.sleep(0.5)
```

The generated client uses `async for item in backend.count()`. Default framing
is newline-delimited JSON; explicit raw text/SSE uses the existing
`@bff_stream(raw=True, media_type=...)` contract. Use the normal trusted-thread
execution mode for streaming; isolated-process mode does not support streams.

## Separate login routes and BFF dispatch

KingdomGive uses an outer FastAPI application for login and other application
HTTP endpoints, then mounts the Pytincture application for browser assets and
BFF dispatch. This composition works with ordinary Uvicorn locally and with
Lambda Web Adapter. It is application routing, not a separate Lambda-specific
BFF engine or an automatic split into multiple Lambda functions.

Use this arrangement when a login request needs a large account/provider
backend that ordinary BFF requests should not import. Keep those imports inside
the login handler or the relevant BFF method. The outer app and Pytincture still
initialize once per execution environment; deferred code initializes on its
first use. This pattern complements the prebuilt manifest and bytecode steps.

For example, `service.py`:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from pytincture import PytinctureConfig, create_app
from pytincture.configuration import configuration_context

ROOT = Path(__file__).resolve().parent
ui = create_app(PytinctureConfig.from_env(modules_path=str(ROOT / "apps")))

# Application-owned integration, not a function supplied by Pytincture.
# Keep this module lightweight: register existing policy/authentication hooks
# and session-context middleware without importing the entire login backend.
from application.security import install_security
install_security(ui)


@asynccontextmanager
async def lifespan(app):
    # Calling a mounted app's router lifespan directly bypasses the child's
    # configuration middleware. Supply its config for startup AND shutdown.
    with configuration_context(ui.state.pytincture_config):
        async with ui.router.lifespan_context(ui):
            yield


app = FastAPI(
    lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
)


class LoginInput(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


@app.post("/auth/login")
async def login(request: Request, values: LoginInput):
    # Application-owned synchronous handler: authenticate, apply login limits
    # and CSRF/origin rules, create the session, and return a Response carrying
    # its cookies. Import its database/password/provider code only on demand.
    from application.auth import login_response
    return await run_in_threadpool(
        login_response, request, values.email, values.password,
    )


# Add logout, provider callbacks, and other application routes BEFORE this
# catch-all mount. Pytincture handles /<application>/classcall/... beneath it.
app.mount("/", ui)
```

`application.security.install_security` and `application.auth.login_response`
are explicit integration points for **your existing application**, not framework
APIs or a complete authentication implementation. If the login handler is async,
await it directly rather than passing it to `run_in_threadpool`. Return its full
Response so multiple `Set-Cookie` headers and redirects are preserved.

The parent app's `/auth/login` wins because it is registered before the `/`
mount; application BFF URLs continue through Pytincture's normal dispatch.
Point `ASGIApplication` at `service:app`, or run `uvicorn service:app` locally.
The same staging command and `BFFPrebuiltManifest` setting apply.

**Mounting does not connect authentication automatically.** Wire the application's
session into its BFF authentication/policy hooks or request-context middleware,
as KingdomGive does. A successful custom login must not leave BFF operations
anonymous. Middleware on `ui` only protects the mounted child; install the
appropriate host, origin/CSRF, request-size and rate controls on the outer app
and its login/provider routes too. Preserve the application's existing cookie
scope, logout/revocation behavior, identity checks and backend permissions.
Do not disable Pytincture startup validation to make the mount work.

When adopting this pattern, check that startup uses the intended `apps` root,
`/auth/login` reaches the parent route and preserves session cookies, protected
BFF calls reject anonymous users, authenticated permissions still apply, and
logout invalidates access. Keep application database migrations outside ordinary
request handling. This example changes routing/import timing, not authorization
requirements or browser bundle contents.

## Package an existing application

The checked-in template targets Python 3.13, ARM64, and the published commercial
region Web Adapter layer version 30. It does not package your application or
create an artifact bucket. Prepare a private bucket in the deployment region,
and use a new immutable S3 key for each package. Follow the normal dependency
locking and release checks for your application.

Your service directory contains:

```text
myservice/
  service.py          # create_app() and your policy/auth hooks
  requirements.txt    # exact Pytincture release, widgetset and backend dependencies
  apps/               # your existing browser entrypoints and BFF modules
```

Build in Linux ARM64, or use wheel-only cross-platform installation. For
example, from the repository root, with `myservice/` prepared as above:

```sh
package_dir=$(mktemp -d)
uv pip install --target "$package_dir" --python-version 3.13 \
  --python-platform aarch64-manylinux_2_28 --only-binary :all: \
  -r myservice/requirements.txt
cp myservice/service.py "$package_dir/service.py"
cp -R myservice/apps "$package_dir/apps"
cp deploy/lambda-web-adapter/run.sh "$package_dir/run.sh"
chmod 755 "$package_dir/run.sh"
mkdir -p build/lambda
python - "$package_dir" "$PWD/build/lambda/application.zip" <<'PY'
import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], "w", zipfile.ZIP_DEFLATED) as output:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            output.write(path, path.relative_to(root))
PY
```

For optimized cold starts, run the [precompilation step](#precompile-the-complete-deployment)
after copying the application and before the ZIP-writing command above. Keep the
generated `__pycache__` directories in the ZIP. That step requires the target
Python minor version and a compatible build environment for native dependencies.
Then select the private manifest with the `BFFPrebuiltManifest` stack parameter.

Only package reviewed application files; keep credentials, `.env`, local
databases, deployment state and developer tools outside `apps/`. Include any
extra backend modules imported by `service.py`. Ship the normal widget wheels
and browser resources your application requires. For an unreleased framework
checkout, build its wheel and install that wheel instead of a published release.
If a dependency has no matching ARM64 wheel (for example an optional native
authentication dependency), build in a compatible Linux environment or use an
adapter container image; never include macOS libraries in the Lambda ZIP.

## Deploy privately and test from localhost

The template creates a function, a log-only execution role, and a log group with
seven-day retention. It creates **no public Function URL, gateway, Bedrock
permission, database, or provisioned concurrency**. Extend `ExecutionRole` in
your application-owned template for the exact AWS resources your BFFs use.
Session signing secrets belong in your normal secret configuration, not source
files or browser-visible settings.

After uploading the ZIP to an existing private bucket:

```sh
aws cloudformation deploy --profile YOUR_TEST_PROFILE --region us-east-1 \
  --stack-name my-pytincture-service \
  --template-file deploy/lambda-web-adapter/template.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides CodeBucket=YOUR_BUCKET CodeKey=YOUR_IMMUTABLE_ZIP_KEY \
    DefaultApplication=YOUR_APPLICATION
aws cloudformation describe-stacks --profile YOUR_TEST_PROFILE --region us-east-1 \
  --stack-name my-pytincture-service --query 'Stacks[0].Outputs'
```

Use the `FunctionName` output with the repository's optional developer tool:

```sh
uv pip install boto3 'botocore[crt]'
python scripts/lambda_forward.py --profile YOUR_TEST_PROFILE \
  --region us-east-1 --function FUNCTION_NAME --port 8098
```

Open `http://127.0.0.1:8098/YOUR_APPLICATION/`. The local process signs
`InvokeWithResponseStream` calls and forwards raw response chunks. AWS
credentials remain on the developer machine. The Lambda execution role makes
backend AWS calls. The forwarder has no local application or model fallback.
Its caller needs `lambda:InvokeFunction` on this function. Profiles created with
`aws login` also need the `botocore[crt]` dependency.

This forwarder is a development tool, not a production reverse proxy. It binds
only to loopback, rejects foreign Host/Origin headers and cross-site browser
requests, limits request bodies to 128 KiB, and queues at three active
invocations until their streams finish. If you reduce the template's reserved
concurrency, pass the matching `--concurrency` value. It deliberately does not
retry a failed invocation automatically, because the BFF may have side effects.
It preserves separate cookies, binary bytes and response status, and treats a
missing/error `InvokeComplete` as an incomplete response.

Stop the forwarder with Ctrl+C. Remove a template-created test service with
`aws cloudformation delete-stack --stack-name my-pytincture-service` using the
same profile and region. Its log group is retained, and the externally owned
artifact bucket is untouched. The Bedrock example has its own deployment-state
cleanup command; do not use stack commands to manage that separately created
demo.

## Application CI/CD with CodePipeline and CodeBuild

An application can use an ordinary AWS build-and-deploy pipeline with this
recipe. Streaming is a runtime and ingress configuration, not a separate build
system: keep the application's `create_app()` entrypoint and `@bff_stream`
methods, and package the same service that runs locally.

**Available now:** the Lambda template, startup script, packaging instructions,
private developer forwarder, reference Bedrock app and local/CI protocol tests.
The repository's GitHub Actions Lambda job validates these building blocks;
it does not deploy an application to AWS. **Not included yet:** a reusable
CodePipeline infrastructure template, a CodeBuild `buildspec.yml`, or an
automated deployed streaming smoke-test stage. An application team supplies
those pieces using the workflow below.

| Stage | Responsibility | Output / acceptance |
| --- | --- | --- |
| Source | CodePipeline retrieves the selected app repository and branch through CodeConnections | Exact source revision |
| Build and test | CodeBuild installs pinned build/test dependencies, runs app and transport tests, and validates the application-owned infrastructure template | Passing checks and recorded dependency versions |
| Package | Build Linux ARM64 Python 3.13 dependencies; include the service, app modules, widget/browser resources and executable `run.sh` | Application ZIP, SHA-256 checksum, source revision and deployment template |
| Publish artifact | Upload the application ZIP to an existing private bucket in the target region under a new immutable key | `CodeBucket` and `CodeKey` values for that exact ZIP |
| Deploy test | CodePipeline's CloudFormation action creates or updates the app stack with those artifact parameters | Stack outputs, including `FunctionName` and `LogGroupName` |
| Verify deployment | Invoke readiness, an ordinary BFF, and a finite streaming BFF through the actual deployed transport | Healthy app, first chunk received before completion, normal end of stream and Lambda execution evidence |
| Promote | Apply the tested artifact to the next environment with its own configuration and permissions | Same artifact bytes; environment-specific stack outputs |

The CloudFormation action consumes the template and supplies `CodeBucket`,
`CodeKey`, `DefaultApplication`, and any application-specific parameter values.
Upload the **application ZIP itself** for Lambda's `CodeKey`; a CodePipeline
artifact ZIP containing another ZIP and a template is not the Lambda package.
Changing the S3 object's bytes under an unchanged key is also insufficient:
use a new key so CloudFormation sees an explicit code update. Retain the prior
artifact and configuration for rollback; this baseline does not automatically
provide alias-based canary releases or traffic rollback.

Before the first pipeline run, configure:

- The source repository/branch and an authorized CodeConnections connection.
  A connection created through infrastructure/API setup must have its pending
  authorization completed in the AWS console.
- The build environment, private artifact bucket and separate build, pipeline,
  and CloudFormation execution roles with the required scoped permissions.
  Build for the Lambda architecture; do not copy workstation native packages.
- The app's pinned Pytincture release, widgetset, optional authentication
  dependencies, entrypoint and application name.
- Backend permissions on the Lambda execution role. The generic template is
  log-only: a Bedrock app must add permission to stream from its chosen models.
- Runtime secrets and per-environment settings. Keep stable session signing
  material in the normal secret-management path, outside source and artifacts.
- The intended private or public ingress. The baseline creates no public
  endpoint; use the public-service guidance below when adding one.

CodeBuild and deployment stages use their service roles and the default AWS
credential chain. Do not copy a developer's credentials or `--profile` login
setup into the build. The localhost forwarder is for interactive developer
testing and is not required to serve a deployed application.

For a private deployment smoke test, an SDK client can call
`InvokeWithResponseStream` with an HTTP v2 event, as the developer forwarder
does. For public ingress, test through that ingress too. Use a deterministic
generator with spaced chunks so the test detects buffering; receiving HTTP 200
or the correct final text alone is not sufficient. Run any real Bedrock test
separately with a small model and bounded output. Match the invocation window
and request identifiers against CloudWatch records to confirm Lambda execution.

The repository's `private-streaming.guard` checks the **log-only private
baseline**. Application templates that intentionally add Bedrock permissions
or public ingress need their own reviewed rules; do not mistake that baseline
policy for a universal application policy. CI checks should continue to enforce
streaming mode, compatible deadlines, scoped permissions and the chosen
authentication boundary.

See AWS's [CodeBuild buildspec reference](https://docs.aws.amazon.com/codebuild/latest/userguide/build-spec-ref.html),
[CodePipeline CloudFormation action](https://docs.aws.amazon.com/codepipeline/latest/userguide/action-reference-CloudFormation.html),
and [pending connection authorization](https://docs.aws.amazon.com/dtconsole/latest/userguide/connections-update.html).

## Settings that make the difference

| Setting | Value / purpose |
| --- | --- |
| Lambda handler | `run.sh`, executable with LF line endings |
| Layer | `LambdaAdapterLayerArm64:30`, checked for the target region |
| `AWS_LAMBDA_EXEC_WRAPPER` | `/opt/bootstrap` |
| `AWS_LWA_INVOKE_MODE` | `response_stream` |
| `AWS_LWA_PORT` | `8080`, matching Uvicorn |
| Readiness | `/readyz`, only status `200` is healthy |
| Compression | Disabled on the reference streaming path |
| Lambda timeout | 90 seconds |
| BFF stream limits | 60 seconds total, 25 seconds idle, 15 seconds blocked write |
| Request body limit | 1 MiB at the app, smaller in the private test forwarder |
| Baseline sizing | 1024 MiB, reserved concurrency 3; measure your own workload |

Pytincture byte/item/admission limits still apply. A slow client can occupy a
Lambda invocation for the lifetime of the stream. Check every ingress timeout
as well as Lambda's timeout. Do not add buffering middleware around streaming
routes. A disconnect from the browser is **not** proof that the Lambda or model
call stopped immediately; cap upstream work and stream duration independently.

## Moving to a public service

Web Adapter's mode and the invocation transport must both support streaming:

- An authenticated Function URL with `InvokeMode: RESPONSE_STREAM` can stream.
  Browser use requires an appropriate authentication architecture; an ordinary
  browser does not sign requests with your local AWS profile.
- API Gateway **REST API** response streaming is a separate integration setup.
  Configure its response transfer mode and streaming Lambda integration, then
  qualify the full path. Do not substitute an HTTP API and assume parity.
- If adding CloudFront, disable shared caching for authenticated/dynamic paths,
  preserve cookies/query strings/methods, and check its response timeouts.

Follow the [production deployment runbook](production-deployment.md): stable
shared signing configuration, HTTPS cookies, canonical public origin, exact
allowed hosts, registered provider callbacks and validated proxy trust. This
startup script disables Uvicorn proxy-header trust. Configure the appropriate
Pytincture trust policy only after verifying which headers ingress supplies;
do not trust arbitrary client-supplied forwarding headers. Publish at the
origin root; removing an ingress path prefix does not rewrite generated URLs.

[Lambda response streaming](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html),
[API Gateway REST streaming](https://docs.aws.amazon.com/apigateway/latest/developerguide/response-transfer-mode.html).

## Cache app loads with CloudFront; measure them with CloudWatch

**CloudFront caches HTTP content. CloudWatch records logs and metrics; it does
not cache the app or freeze Python processes.** CloudFront hits can avoid both
Lambda initialization and public-data queries. They do not speed up a cache
miss, a private backend operation, or the browser's WASM initialization itself.

KingdomGive used this pattern alongside the startup optimizations. Its public
giving-page design and data were cacheable, while login, organizer operations,
donor-specific information and checkout stayed live. This is an application and
CDN configuration pattern; the Pytincture wheel and private deployment template
do not automatically create a CloudFront distribution or publish public data.

### Separate public content from private operations

| Content | Routing/cache policy |
| --- | --- |
| Content-addressed browser bundles, WASM runtimes, scripts, styles and public images | Cache immutable release URLs for up to a year |
| Anonymous page shell, published design and public organization settings | Separate public GET/HEAD routes; short shared TTL, such as 600 seconds |
| Login/logout, account pages, donor details, organizer/admin models and personalized prefills | Caching disabled; preserve identity/cookies to origin |
| BFF POST calls, payments, provider callbacks and streams | Live origin requests; no shared response cache |
| Source code, private BFF manifests, build reports and secrets | Do not publish through the CDN |

Cache the **public data as well as HTML**. Otherwise, cached HTML can immediately
make a live BFF request that still waits for Lambda to start. CloudFront caches
GET/HEAD responses (and optionally OPTIONS), not ordinary BFF POST responses;
see [cache behavior settings](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesCacheBehavior.html).
Do not change a state-changing operation to GET just to make it cacheable.

A useful application URL layout is:

```text
/_public/html/giving                 anonymous, release-specific shell
/_public/data/giving/<organization>  published public design/settings only
/releases/<content-hash>/...         immutable public browser assets
/auth/...                           live authentication
/<application>/classcall/...        live Pytincture BFF dispatch
```

These are example application-owned routes, not built-in framework endpoints.
Generate the public response using the same authorized service/read logic as
normal publishing, but project **only published public fields**. Never put donor
prefills, session/replay/CSRF tokens, signed private links, or payment sessions in
it. A public projection must stay anonymous even when requested by a signed-in
visitor. Personalized content uses a different uncached route. Do not populate
a shared cache using the publisher's authenticated response.

Only use a shared HTML shell when it contains no identity-specific data or
session tokens and sets no cookies. If the normal shell personalizes content,
use a dedicated public shell or keep it uncached. Do not remove protective
headers from normal Pytincture responses globally to force cache hits.

### Configure explicit CloudFront behaviors

1. Keep the default behavior and all authentication, private BFF, checkout,
   callback and streaming paths on a caching-disabled policy. Forward the
   cookies, authentication headers, query values and bodies their origin needs.
2. Add a narrowly scoped `/_public/*` GET/HEAD cache behavior. A custom policy
   with minimum TTL **0**, default TTL **0**, maximum TTL **600** allows the
   origin to opt eligible public responses in with this header:

   ```http
   Cache-Control: public, max-age=0, s-maxage=600, must-revalidate
   ```

3. Put immutable public assets on a separate behavior and use content-hashed
   filenames/directories with `public, max-age=31536000, immutable`. Changing
   content must produce a new URL; unversioned loader pointers need a short TTL
   or revalidation. Do not use a process-random UUID as the release cache key.
4. Include every value that changes the public representation in its path or
   cache key: organization, selected purpose, locale, runtime and release as
   applicable. Validate these inputs at origin. Exclude irrelevant tracking
   parameters only when they cannot affect the response. For a shared shell,
   ensure tenant details are resolved by the separate tenant-keyed data URL.
5. Avoid forwarding identity to dedicated anonymous projections; forward it on
   private behaviors. Dropping cookies/Authorization on an existing personalized
   endpoint is not a replacement for creating a safe public projection.

A positive minimum TTL can override origin `private`/`no-store` directives.
Keep minimum TTL zero when relying on these headers, and use caching-disabled
policies for private routes. See the [cache policy reference](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cache-key-understand-cache-policy.html).
Configure behavior priority so a broad asset/public pattern cannot capture an
authentication or backend path. Keep origin-access protection, request-body
signing requirements and provider callback routing intact; CDN caching is not
an authorization layer.

### Refresh after publication and deployment

After the business write commits, publish the new public representation and
invalidate the affected organization HTML/data paths. Use a new versioned URL
for changed images and bundles. First visits by new organizations populate their
own cache entries; no separate distribution is required per organization. A
short TTL is a fallback when refresh fails, not proof of immediate freshness.

For example, after a successful publish, an authorized deployment/worker can run:

```sh
aws cloudfront create-invalidation --distribution-id YOUR_DISTRIBUTION_ID \
  --paths '/_public/data/giving/example-org*' '/_public/html/giving*'
```

Invalidate the distribution's actual rewritten cache paths, including relevant
query variants. A common shell invalidation may affect all organizations; skip
it when only organization data changed. Invalidation is asynchronous: verify
completion before claiming the new version is served. Grant refresh workers
only the required distribution access, and retry/report refresh failures.
See [invalidation](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Invalidation.html)
and [versioned filenames](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/UpdatingExistingObjects.html).

### Verify caching and correlate CloudWatch evidence

Request a declared public URL twice and inspect its response headers:

```sh
curl -sS -D - -o /dev/null 'https://YOUR_DISTRIBUTION/_public/data/giving/example-org'
```

A later request can show `X-Cache: Hit from cloudfront` and an `Age` header.
Check the public HTML, data and asset requests individually, and inspect the
browser network waterfall to find live BFF calls still on the visible path.
Different edge locations and expirations can produce new misses. Do not label a
CloudFront hit a Lambda cold-start measurement.

Use CloudWatch Lambda logs to distinguish initialization from request processing.
For an application with KingdomGive-style duration logs, a Logs Insights starting
query is:

```text
fields @timestamp, @logStream, @message
| filter @message like /INIT_REPORT|REPORT RequestId|KG_STARTUP/
| sort @timestamp desc
| limit 100
```

`KG_STARTUP` is application instrumentation, not a built-in Pytincture event.
Correlate log streams/request IDs and Lambda initialization records with the
browser/edge timestamps; the absence of a new initialization record alone does
not prove a cache hit. Check CloudFront cache-hit metrics/access logs alongside
Lambda invocation counts. Background analytics, icons or private calls can
still invoke Lambda even when the public page is cached.

Before enabling caching broadly, test two different organizations, anonymous and
signed-in visitors, a publish/edit refresh, a new organization, login/logout and
checkout. Confirm public responses contain no private fields or Set-Cookie,
private requests never produce shared-cache hits, and failed/unauthorized
responses cannot become long-lived public entries. Capture both fresh-browser
and repeat-browser timings, reporting edge hit/miss and backend warm/cold state
separately.

## Compatibility and evidence

| Area | Status and boundary |
| --- | --- |
| Complete Pyodide/wapyt chat, generated BFF stub, real Bedrock stream | Verified through private Lambda invocation and the local forwarder on October 11, 2026 |
| Large WASM, appcode and widget assets | The live chat booted through Lambda; response bytes are streamed rather than base64-wrapped into a buffered response |
| Forwarder cookies, binary data, split metadata, incremental body delivery, error/closure handling, concurrency | Automated protocol tests; these do not substitute for an AWS runtime test |
| Other normal BFF verbs, policies, sessions, redirects, raw SSE, portable bundles | Use the same ASGI app and existing framework contracts; require application-specific AWS acceptance tests |
| OAuth/SAML, shared revocation, API-client registry, MCP | Require dedicated deployed tests, stable shared secrets/state, and compatible optional dependencies |
| In-memory caches and admission counters | Per execution environment, disposable; not global rate limits or persistent storage |
| Local mutable files and background work | Not durable or shared; use external storage/jobs where required |
| Persistent ASGI WebSockets or indefinitely open streams | Not supported by this ordinary Lambda HTTP deployment recipe |

The reference browser test returned first text in **0.54 s**, completed in
**1.10 s**, and received **51 text chunks / 128 output tokens** from Nova Micro.
Observed cold initialization was approximately **1.3–1.9 s**. These are small
test samples, not an SLA or a comparison benchmark against Mangum. Separate
CloudWatch `START`, `END`, and `REPORT` records confirmed execution in AWS.

The CI Lambda job runs the mocked transport checks, the example's
bounded Bedrock-call tests, and template validation without cloud credentials
or model invocations. It imports the AWS SDK locally to check the bridge, but
does not contact AWS. Re-run real AWS acceptance after changing the adapter
layer, ingress, Python runtime, framework release, optional dependencies, or
stream middleware. For a stream probe, use a finite generator with deliberately
spaced items and verify the first item arrives before the last one is produced.
Inspect the template's `LogGroupName` output with `aws logs tail` to confirm
actual Lambda invocations.

The earlier [full-support design](lambda-full-support-design.md) remains a
record of the wider qualification requirements; its custom Mangum runtime
proposal is superseded by this Web Adapter path.

## Measuring cold startup

Use the deployment's Python interpreter, installed dependencies and environment
with the startup profiler from a source checkout:

```sh
python scripts/profile_startup.py \
  --modules-path /path/to/application/modules \
  --runs 5 --top 50 --output /tmp/pytincture-startup.json
```

To include custom application construction, pass `--target service:create_app`
where the target is an importable, no-argument factory returning the ASGI app.
Use the same factory that installs the service's policy hooks and providers.
The profiler executes startup hooks and lifespan code, including any configured
external-service calls, but opens no HTTP listener. Do not run it against a
production environment unless those startup actions are appropriate.

Each sample uses a fresh Python subprocess. The JSON report separates target
module imports, factory execution, lifespan startup, and individual Starlette
startup hooks. It also ranks imports and functions by self and cumulative time.
Cumulative entries overlap and must not be added together; self time excludes
nested calls. Custom lifespan work is included in the lifespan total and function
profile even when it does not use Starlette's startup-hook list.

Profiling overhead increases these timings. Fresh processes can still benefit
from OS filesystem caches and previously compiled bytecode. The measurements
exclude Python startup before the worker's timer, Lambda/Web Adapter overhead,
HTTP requests, and browser work; subprocess wall time includes worker startup
and shutdown. Compare like-for-like runs and correlate with AWS initialization
logs rather than treating these results as Lambda cold-start totals.

Inspect optional dependency imports even when a feature is disabled, recursive
BFF registry scans, source parsing, and repeated scans across factory and startup
phases. Use the actual application module root: pointing at a whole checkout or
an image root can make discovery scan unrelated Python files. Parallel execution
is most promising for independent I/O; imports, model construction and registry
mutation have dependencies that require care. Avoiding unused or repeated work
should precede attempts to parallelize it.

MCP, OAuth and SAML extras now load only when enabled. Startup policy validation
and MCP registration reuse BFF manifests parsed during app construction when
securely reread source bytes have the same digest. Discovery still checks for
added, deleted and changed files, and explicit registry reloads still reparse.
This cache belongs to one app in one process; it does not persist between fresh
Lambda execution environments.

### Prebuilding server BFF discovery

The browser bundle contains client code and generated BFF proxies. The backend
still needs its own export, argument, policy and definition metadata. To move
that parsing to deployment build time, generate a separate private server file:

```sh
python -m pytincture.backend.bff_manifest \
  --modules-path /build/application/modules \
  --output /build/private/server-bff.json
```

Alternatively add `--server-bff-manifest /build/private/server-bff.json` to
`pytincture-build-browser --config pyproject.toml` or the appcode builder:

```sh
pytincture-build-appcode demo --modules-path /build/application/modules \
  --output-directory /build/appcode \
  --server-bff-manifest /build/private/server-bff.json
```

Both builders require this file to be outside the browser output directory.
For multi-app builds, invoke the option
per application; artifacts with distinct module roots need distinct paths.

Copy the application source and this private artifact into the Lambda image,
then set `PYTINCTURE_BFF_PREBUILT_MANIFEST=/var/task/private/server-bff.json`.
The typed factory equivalent is `PytinctureConfig(bff_prebuilt_manifest=...)`.
Relative artifact paths resolve against `modules_path`. Without this setting,
startup continues to discover everything from source.

The artifact is deployment-trusted metadata, like backend code: keep it out of
browser assets, uploads and client-controlled paths. Source hashes detect stale
source; they do not authenticate a maliciously rewritten manifest. The file
contains policies, definition identities, documentation and literal defaults,
so do not publish it. No pickle, eval or application imports are used to build
or load it.

Startup securely reads and hashes every discovered Python file. Matching entries
reuse the prebuilt metadata; new/changed files are parsed normally, deleted or
unsafe files cannot retain cached exports, and startup policy validation still
runs. Framework/parser or Python minor-version mismatches fall back to source
discovery. Missing or malformed configured artifacts fail startup. Build with the
same framework wheel and Python minor version used in Lambda; rebuild after
upgrades. Files containing unusual non-JSON literal defaults fall back to source
parsing. Explicit registry reload and request-time source/definition verification
retain their existing behavior.

Compare profiles with and without `PYTINCTURE_BFF_PREBUILT_MANIFEST` against the
same application root. This optimization moves parsing out of initialization;
it does not remove filesystem verification or optimize application data queries.

### Precompile the complete deployment

Do this **after** dependency installation, browser building and copying the
service and application sources, and **before** creating the application ZIP:

```sh
PYTHONPATH="$package_dir" python3.13 -m pytincture.lambda_build \
  --root "$package_dir" \
  --modules-path "$package_dir/apps" \
  --manifest "$package_dir/private/server-bff.json"
```

Use the target Python minor version and normal interpreter mode (no `-O` or
`-OO`). Prefer running the whole build in the target Linux environment,
particularly when dependencies contain native extensions. The staging command
requires the matching framework version on its import path. It writes standard
`__pycache__/*.pyc` files for the service, framework and installed dependencies,
plus the private server manifest. Include both sources and `__pycache__` in the
ZIP; don't run cleanup steps that delete bytecode afterward. Cross-platform
`pip --target` alone does not guarantee that bytecode has been compiled.

Compilation uses checked hashes, not unchecked bytecode. Python still validates
source bytes before accepting cached code. Assertions and docstrings remain
available. `PYTHONDONTWRITEBYTECODE=1` prevents runtime writes but does not prevent
reading packaged bytecode. Do not set `PYTHONPYCACHEPREFIX` or change the Python
minor version between build and deployment. The command records `/var/task`
code locations; pass `--runtime-root` if deploying elsewhere. It skips symlinks
and fails the build on invalid Python dependency files. It does not import the
application or retain live application objects.

Set the template parameter
`BFFPrebuiltManifest=/var/task/private/server-bff.json` to activate the manifest.
The launcher now chooses asyncio, h11 and no WebSocket protocol explicitly,
avoiding optional transport discovery; ASGI lifespan validation remains enabled.

Run the local comparison with the deployment's Python environment:

```sh
python scripts/benchmark_lambda_cold_start.py \
  --modules-path /path/to/application/modules --runs 5 \
  --output /tmp/pytincture-cold-start.json
```

It copies dependencies and current framework source into a disposable staging
area, starts fresh processes with runtime bytecode writes disabled, then compares
source-only, prebuilt BFF, checked bytecode, and explicit HTTP transport settings.
Unlike the detailed profiler, this benchmark does not use cProfile/importtime.
It includes Uvicorn configuration and application lifespan startup, but not a
bound network listener, Lambda initialization, the Web Adapter or data requests.
OS filesystem caches may remain warm. Whole-process timings include shutdown.

### Keep an initialized environment ready

For the shortest request startup path, the template supports optional
[provisioned concurrency](https://docs.aws.amazon.com/lambda/latest/dg/provisioned-concurrency.html).
Set `ProvisionedConcurrency=1` (or the required capacity), keeping it at or below
`ReservedConcurrency`. It creates an immutable version and a `ready` alias;
invoke the **ReadyFunctionArn** output, rather than the unqualified FunctionName.
Wait for provisioned capacity to become ready before measuring. Capacity beyond
the provisioned amount can still cold-start. This is a paid opt-in and defaults
to zero; no infrastructure is changed by building the wheel or staging files.

Use a new immutable CodeKey for each release, including configuration changes,
so the version description changes and CloudFormation publishes a replacement.
Retained old versions support rollback; manage their lifecycle in deployment
operations. Requests using `$LATEST` do not use the alias's initialized capacity.
This option avoids reinitialization on the request path; it does not make the
initialization computation itself faster.

[SnapStart](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html) is a
different platform mechanism that restores initialized state. The
[Web Adapter's newer releases](https://github.com/aws/aws-lambda-web-adapter/blob/main/CHANGELOG.md)
document snapshot/restore HTTP hooks. This recipe pins an older layer and does
not claim tested SnapStart compatibility. Do not simply enable snapshots on it:
qualify the adapter upgrade, refresh restored identifiers/connections/temporary
state, and test authentication, replay stores and streaming after restoration.
The initialized `ready` alias is the implemented option for this recipe.
