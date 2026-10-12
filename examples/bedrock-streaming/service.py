"""Private Lambda demo, accessed through the authenticated localhost forwarder."""

from pathlib import Path

from pytincture import PytinctureConfig, create_app


app = create_app(PytinctureConfig.from_env(
    modules_path=str(Path(__file__).parent / "apps"),
    default_application="bedrock_chat",
    enable_service_worker=False,
    bff_stream_max_seconds=60,
    bff_stream_max_bytes=256 * 1024,
    bff_stream_max_items=10000,
    bff_stream_idle_timeout_seconds=25,
    bff_stream_write_timeout_seconds=15,
))
