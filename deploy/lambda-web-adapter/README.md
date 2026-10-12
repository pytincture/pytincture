# Pytincture Lambda deployment files

Follow the [Lambda deployment guide](../../docs/aws-lambda.md) for packaging,
deployment, local forwarding and production qualification.

- `run.sh`: start the normal ASGI app with one Uvicorn worker.
- `service.py`: minimal `create_app()` entrypoint; use your own when you register
  authentication or policy hooks.
- `template.yaml`: private Python 3.13 / ARM64 Lambda, Web Adapter layer 30,
  log-only execution role and retained seven-day log group. Supply an existing
  private S3 artifact; no public ingress is created.
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
