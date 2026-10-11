"""Compatibility entrypoint for the shared private Lambda forwarder."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/lambda_forward.py"),
        run_name="__main__",
    )
