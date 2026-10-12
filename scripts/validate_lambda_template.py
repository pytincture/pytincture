"""Validate the private Lambda recipe without contacting AWS."""

from pathlib import Path
import json
import subprocess
import sys

import guardpycfn


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "deploy/lambda-web-adapter"


def validate_rules(template_text):
    result = guardpycfn.validate_with_guard(
        template_text, (DIRECTORY / "private-streaming.guard").read_text(), verbose=True,
    )
    status = result.get("result", {}).get("container", {}).get("FileCheck", {}).get("status")
    if not result.get("success") or status != "PASS":
        print(json.dumps(result, indent=2))
        raise ValueError("Private Lambda rules did not pass")


def main():
    template = DIRECTORY / "template.yaml"
    subprocess.run([str(Path(sys.executable).parent / "cfn-lint"), str(template)], check=True)
    validate_rules(template.read_text())
    print("CloudFormation schema: PASS; private streaming Guard rules: PASS")


if __name__ == "__main__":
    main()
