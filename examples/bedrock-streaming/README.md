# Bedrock streaming chat demo

A small Pytincture UI using the same `wapyt.chat` widget family as
[wAwesomeChat](https://github.com/WAwesome-AI/wAwesomeChat), with a direct
Bedrock ConverseStream BFF. No LiteLLM, transcript database or provider admin.

This is the live reference application for the framework's
[Lambda Web Adapter deployment recipe](../../docs/aws-lambda.md). The reusable
private forwarder lives in `scripts/lambda_forward.py`; this directory's
`forward.py` remains a compatible command-line entrypoint.

The only permitted model is **Amazon Nova Micro** (`amazon.nova-micro-v1:0`),
with at most 512 output tokens, a 4,000-character prompt, seven recent messages,
and 16,000 total history characters. The UI shows first-text latency, elapsed
time, chunk count and output-token usage. Conversation text is sent to Bedrock;
the widget may also retain its transcript in this browser's local storage.
New conversation clears the current transcript and model context.

## Test topology

```text
Browser on your Mac, http://127.0.0.1:8098/bedrock_chat/
  -> loopback forwarding service (your local AWS profile)
  -> Lambda InvokeWithResponseStream (authenticated)
  -> AWS Lambda Web Adapter -> Uvicorn -> Pytincture BFF
  -> Amazon Bedrock ConverseStream (Lambda execution role)
```

There is no public Function URL, API Gateway, database, provisioned capacity or
ongoing model endpoint. The Lambda uses ARM64 Python 3.13, 1024 MB memory, a
90-second timeout and reserved concurrency of three. Streams are capped at
60 seconds. Lambda/Bedrock invocation charges still apply while testing.

This proves the Lambda/Web Adapter streaming path; it does not qualify REST API
Gateway or CloudFront streaming. Lambda does not reliably stop work immediately
when the browser disconnects. Closing a stream closes the local SDK stream;
the short model/output/invocation limits bound remaining upstream work.

## Build and deploy

Run commands from the Pytincture repository root. The selected AWS profile
needs permissions to create this demo's Lambda role/function/log group, pass
that role, read the public adapter layer and inspect Nova Micro. The local
forwarder needs `lambda:InvokeFunction` on the created function. The Lambda
role itself can only stream from Nova Micro and write its own logs.

```sh
uv venv --python 3.13 build/bedrock-streaming/venv
uv pip install --python build/bedrock-streaming/venv/bin/python -e . boto3 httpx pytest
uv pip install --python build/bedrock-streaming/venv/bin/python 'botocore[crt]'
build/bedrock-streaming/venv/bin/python examples/bedrock-streaming/package.py
build/bedrock-streaming/venv/bin/python examples/bedrock-streaming/deploy.py deploy --profile YOUR_TEST_PROFILE
```

The build pins the widget source at commit
`d3ac5847a09bd2e0b6b409d5bf6b78cb082782ef`, builds the current Pytincture checkout,
and includes the widget wheel in the app's served package directory. Generated
wheels, Linux dependencies and deployment state stay under ignored `build/`.
The adapter is pinned to the published ARM64 layer version 30.

Read the function name from `build/bedrock-streaming/deployment.json`, then:

```sh
build/bedrock-streaming/venv/bin/python examples/bedrock-streaming/forward.py \
  --profile YOUR_TEST_PROFILE --function FUNCTION_NAME --port 8098
```

Open <http://127.0.0.1:8098/bedrock_chat/>. Keep the forwarding process running.
It accepts only loopback hostnames and same-origin browser requests. Credentials
are resolved through boto3's normal credential chain and never sent to browser
Python, copied into the package, or placed in application source. The application
has no login because its deployed Lambda is accessible only through AWS IAM.
Do not add a public unauthenticated Function URL to this demo.

The `botocore[crt]` extra supports profiles authenticated with `aws login`.
The forwarder queues browser asset requests at three concurrent invocations,
matching the Lambda's reserved concurrency, and holds each slot until its
response stream finishes.

## Local development

With a profile that can invoke Bedrock directly:

```sh
uv pip install --python build/bedrock-streaming/venv/bin/python \
  build/bedrock-streaming/wheels/wapyt-0.2.0.dev1-py3-none-any.whl
AWS_PROFILE=YOUR_TEST_PROFILE build/bedrock-streaming/venv/bin/python -m uvicorn \
  service:app --app-dir examples/bedrock-streaming --host 127.0.0.1 --port 8099
```

Open <http://127.0.0.1:8099/bedrock_chat/>. This local mode does not exercise
Lambda. The UI displays a real error if Bedrock access is unavailable; it never
substitutes a simulated answer.

## Verification and removal

```sh
build/bedrock-streaming/venv/bin/python -m pytest -q examples/bedrock-streaming
build/bedrock-streaming/venv/bin/python -m pytest -q tests/test_lambda_forward.py
build/bedrock-streaming/venv/bin/python examples/bedrock-streaming/deploy.py cleanup
```

The cleanup command uses the recorded account/profile and checks resource tags
before removing only this demo's function, role and log group. It can also remove
resources recorded by a partially completed deployment. Stop the local forwarder
with Ctrl+C. Browser automation and AWS execution results must be recorded
separately; passing unit tests alone does not establish live Bedrock streaming.

Live verification on October 11, 2026 used Python 3.13/ARM64, Web Adapter layer
30 and Nova Micro. The browser received 51 text chunks / 128 output tokens,
with first text at 0.54 seconds and completion at 1.10 seconds. CloudWatch
execution records confirmed actual Lambda invocations. These are sample
measurements rather than latency guarantees. The chat-only UI explicitly hides
the artifact panel, including at narrow widths where the upstream widget's
closed-panel CSS otherwise covers the conversation.
