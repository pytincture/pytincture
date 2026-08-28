from pathlib import Path

from pytincture import PytinctureConfig, create_app


HERE = Path(__file__).resolve().parent
app = create_app(
    PytinctureConfig(
        modules_path=str(HERE),
        default_application="hello",
    )
)
