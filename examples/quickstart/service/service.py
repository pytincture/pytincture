from pathlib import Path

from pytincture import PytinctureConfig, create_app


HERE = Path(__file__).resolve().parent
app = create_app(
    PytinctureConfig(
        modules_path=str(HERE),
        default_application="hello",
        # wapyt is a pluggable widgetset, so trust its exact PyPI release.
        widget_public_index_allowlist=("wapyt==0.2.0.dev1",),
    )
)
