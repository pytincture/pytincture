"""Create a private, tagged demo Lambda. No public URL or API Gateway is created."""

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError


TAG = {"project": "pytincture-bedrock-streaming-demo"}
REGION = "us-east-1"
LAYER = "arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerArm64:30"


def deploy(args):
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    account = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    functions = session.client("lambda")
    logs = session.client("logs")
    # Preflight read access before creating anything.
    functions.get_layer_version_by_arn(Arn=LAYER)
    model = session.client("bedrock").get_foundation_model(modelIdentifier="amazon.nova-micro-v1:0")
    if not model["modelDetails"].get("responseStreamingSupported"):
        raise ValueError("Nova Micro streaming is unavailable in this region")
    archive = args.package.read_bytes()
    if len(archive) >= 49 * 1024 * 1024:
        raise ValueError("Package exceeds this demo's direct-upload budget")
    if args.state.exists():
        raise ValueError("State file already exists; preserve it for cleanup or choose a new file")
    name = "pytincture-bedrock-demo-" + uuid.uuid4().hex[:8]
    role_name = name + "-role"
    log_group = "/aws/lambda/" + name
    state = {"profile": args.profile, "region": REGION, "account": account,
             "function": name, "role": role_name, "log_group": log_group, "created": []}
    args.state.parent.mkdir(parents=True, exist_ok=True)

    def record(resource):
        state["created"].append(resource)
        args.state.write_text(json.dumps(state, indent=2) + "\n")
        print("Created", resource, flush=True)

    logs.create_log_group(logGroupName=log_group, tags=TAG)
    record("log_group")
    logs.put_retention_policy(logGroupName=log_group, retentionInDays=1)
    role = iam.create_role(RoleName=role_name, Tags=[{"Key": k, "Value": v} for k, v in TAG.items()],
        AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))
    record("role")
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "bedrock:InvokeModelWithResponseStream",
         "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:{log_group}:*"},
    ]}
    iam.put_role_policy(RoleName=role_name, PolicyName="demo-only", PolicyDocument=json.dumps(policy))
    settings = dict(FunctionName=name, Runtime="python3.13", Architectures=["arm64"],
        Handler="run.sh", Role=role["Role"]["Arn"], Code={"ZipFile": archive},
        Timeout=90, MemorySize=1024, Layers=[LAYER], Tags=TAG,
        Description="Temporary private Pytincture/wapyt Nova Micro streaming demo",
        Environment={"Variables": {
            "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
            "AWS_LWA_PORT": "8080", "AWS_LWA_READINESS_CHECK_PATH": "/readyz",
            "AWS_LWA_READINESS_CHECK_HEALTHY_STATUS": "200",
            "AWS_LWA_INVOKE_MODE": "response_stream",
            "AWS_LWA_ENABLE_COMPRESSION": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTINCTURE_ALLOWED_HOSTS": "127.0.0.1,localhost",
        }})
    # IAM trust changes may take a few seconds to reach Lambda.
    for attempt in range(8):
        try:
            functions.create_function(**settings)
            break
        except functions.exceptions.InvalidParameterValueException as exc:
            if "cannot be assumed" not in str(exc) or attempt == 7:
                raise
            time.sleep(3)
    record("function")
    functions.get_waiter("function_active_v2").wait(FunctionName=name,
        WaiterConfig={"Delay": 2, "MaxAttempts": 25})
    functions.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=3)
    print("Private Lambda ready:", name)
    print("State file:", args.state)


def cleanup(args):
    state = json.loads(args.state.read_text())
    session = boto3.Session(profile_name=state["profile"], region_name=state["region"])
    if session.client("sts").get_caller_identity()["Account"] != state["account"]:
        raise ValueError("Refusing cleanup in a different account")
    functions = session.client("lambda")
    iam = session.client("iam")
    logs = session.client("logs")
    if "function" in state["created"]:
        try:
            arn = functions.get_function_configuration(FunctionName=state["function"])["FunctionArn"]
            if functions.list_tags(Resource=arn)["Tags"].get("project") != TAG["project"]:
                raise ValueError("Refusing to delete an untagged function")
            functions.delete_function(FunctionName=state["function"])
        except functions.exceptions.ResourceNotFoundException:
            pass
    if "role" in state["created"]:
        try:
            role = iam.get_role(RoleName=state["role"])["Role"]
            tags = {item["Key"]: item["Value"] for item in role.get("Tags", [])}
            if tags.get("project") != TAG["project"]:
                raise ValueError("Refusing to delete an untagged role")
            try:
                iam.delete_role_policy(RoleName=state["role"], PolicyName="demo-only")
            except iam.exceptions.NoSuchEntityException:
                pass
            iam.delete_role(RoleName=state["role"])
        except iam.exceptions.NoSuchEntityException:
            pass
    if "log_group" in state["created"]:
        try:
            tags = logs.list_tags_log_group(logGroupName=state["log_group"])["tags"]
            if tags.get("project") != TAG["project"]:
                raise ValueError("Refusing to delete an untagged log group")
            logs.delete_log_group(logGroupName=state["log_group"])
        except logs.exceptions.ResourceNotFoundException:
            pass
    state["cleaned_up"] = True
    args.state.write_text(json.dumps(state, indent=2) + "\n")
    print("Removed the demo function, role and log group.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "cleanup"])
    parser.add_argument("--profile", default="default")
    parser.add_argument("--package", type=Path, default=Path("build/bedrock-streaming/lambda.zip"))
    parser.add_argument("--state", type=Path, default=Path("build/bedrock-streaming/deployment.json"))
    args = parser.parse_args()
    try:
        (deploy if args.action == "deploy" else cleanup)(args)
        return 0
    except (ClientError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
