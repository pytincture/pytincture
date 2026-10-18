# Pytincture Lambda deployment files

Follow the [Lambda deployment guide](../../docs/aws-lambda.md) for packaging,
deployment, local forwarding and production qualification.

For application-owned login endpoints alongside the mounted Pytincture BFF
routes, see [separate login routes and BFF dispatch](../../docs/aws-lambda.md#separate-login-routes-and-bff-dispatch).
That example includes lazy login-backend imports, route ordering, mounted-app
lifespan configuration, and the required application authentication integration.

For faster public app loads, see [CloudFront caching and CloudWatch measurement](../../docs/aws-lambda.md#cache-app-loads-with-cloudfront-measure-them-with-cloudwatch).
It covers public HTML/data/assets, private-route exclusions, cache keys,
publish-time refresh and verification. The private template does not create a CDN.

- `run.sh`: start the normal ASGI app with one Uvicorn worker.
- The launcher explicitly selects asyncio, h11 and no WebSocket transport for
  this HTTP-only Lambda recipe. Package checked bytecode with
  `pytincture-prepare-lambda`; see the deployment guide for the staging command.
- `service.py`: minimal `create_app()` entrypoint; use your own when you register
  authentication or policy hooks.
- `template.yaml`: private Python 3.13 / ARM64 Lambda, Web Adapter layer 30,
  log-only execution role and retained seven-day log group. Supply an existing
  private S3 artifact; no public ingress is created.
- `BFFPrebuiltManifest` optionally selects the packaged server manifest.
  `ProvisionedConcurrency=1` optionally creates a published version and a `ready`
  alias with one initialized environment (paid). Invoke `ReadyFunctionArn` to
  use it; provisioned concurrency must not exceed `ReservedConcurrency`.
- `private-streaming.guard`: repository-specific checks for streaming settings,
  private ingress and the baseline execution permissions. These are not a
  general AWS security certification.

Run template checks with `python scripts/validate_lambda_template.py` after
installing `cfn-lint==1.46.0` and `guardpycfn==0.1.0`. The CI Lambda job runs the
same checks without cloud credentials. Check regional layer availability,
permissions and quotas before deploying an application-specific copy.

For application delivery automation, see
[CodePipeline and CodeBuild context](../../docs/aws-lambda.md#application-cicd-with-codepipeline-and-codebuild).
It describes the source/build/package/deploy/stream-verification stages,
artifact handoff, IAM and secret setup, and promotion/rollback boundaries.
The reusable CodePipeline template and CodeBuild `buildspec.yml` are not yet
included; the files here are the application deployment building blocks.
