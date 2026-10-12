"""Example entrypoint: replace with your existing create_app() and auth hooks."""

from pathlib import Path

from pytincture import PytinctureConfig, create_app


app = create_app(PytinctureConfig.from_env(
    modules_path=str(Path(__file__).parent / "apps"),
))
