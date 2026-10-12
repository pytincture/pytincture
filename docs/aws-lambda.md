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
        if path.is_file() and "__pycache__" not in path.parts:
            output.write(path, path.relative_to(root))
PY
```

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
