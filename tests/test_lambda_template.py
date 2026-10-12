"""Negative cases keep the optional template validator from silently passing."""

import importlib.util
from pathlib import Path

import pytest


pytest.importorskip("guardpycfn", reason="CloudFormation checks use optional deployment tooling")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lambda_template", ROOT / "scripts/validate_lambda_template.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
TEMPLATE = (ROOT / "deploy/lambda-web-adapter/template.yaml").read_text()


def test_private_streaming_template_passes():
    validator.validate_rules(TEMPLATE)


@pytest.mark.parametrize("before,after", [
    ("AWS_LWA_INVOKE_MODE: response_stream", "AWS_LWA_INVOKE_MODE: buffered"),
    ("Resource: !GetAtt LogGroup.Arn", 'Resource: "*"'),
    ("- logs:PutLogEvents", "- bedrock:InvokeModel"),
    ("Resources:\n", "Resources:\n  PublicURL:\n    Type: AWS::Lambda::Url\n"
     "    Properties:\n      TargetFunctionArn: !GetAtt Function.Arn\n      AuthType: NONE\n"),
])
def test_unsafe_or_buffered_recipe_changes_fail(before, after):
    assert before in TEMPLATE
    with pytest.raises(ValueError, match="did not pass"):
        validator.validate_rules(TEMPLATE.replace(before, after))
