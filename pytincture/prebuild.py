"""Build immutable browser appcode archives for production deployments."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from pytincture import __version__
from pytincture.backend.browser_packages import create_appcode_archive
from pytincture.backend.safe_paths import canonical_root, validate_application_name
from pytincture.dataclass import get_parsed_output


def build_prebuilt_appcode(
    application: str,
    output_directory: str | os.PathLike[str],
    *,
    modules_path: str | os.PathLike[str] = ".",
    browser_files: str = "",
    max_files: int = 512,
    max_file_bytes: int = 4 * 1024 * 1024,
    max_total_bytes: int = 32 * 1024 * 1024,
) -> Path:
    """Build ``<application>.pyt`` atomically without importing app modules."""
    application = validate_application_name(application)
    modules_root = canonical_root(os.fspath(modules_path))
    destination_root = Path(output_directory).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {}
    archive = create_appcode_archive(
        "",
        "",
        application,
        modules_root,
        get_parsed_output,
        raw_patterns=browser_files,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        manifest_out=manifest,
    )
    manifest["pytincture_version"] = __version__
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    target = destination_root / f"{application}.pyt"
    manifest_target = destination_root / f"{application}.pyt.json"
    temporary_paths: list[Path] = []

    def stage(payload: bytes, suffix: str) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{application}.",
            suffix=suffix,
            dir=destination_root,
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            staged = Path(temporary.name)
        temporary_paths.append(staged)
        return staged

    try:
        archive_stage = stage(archive.getvalue(), ".pyt.tmp")
        manifest_stage = stage(manifest_bytes, ".pyt.json.tmp")
        os.replace(archive_stage, target)
        temporary_paths.remove(archive_stage)
        os.replace(manifest_stage, manifest_target)
        temporary_paths.remove(manifest_stage)
    finally:
        archive.close()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a deployment-ready Pytincture browser archive."
    )
    parser.add_argument("application", help="Application entrypoint name")
    parser.add_argument(
        "--modules-path",
        default=".",
        help="Directory containing the application and backend modules",
    )
    parser.add_argument(
        "--output-directory",
        required=True,
        help="Directory that will receive <application>.pyt",
    )
    parser.add_argument(
        "--browser-files",
        default="",
        help="Optional browser file declaration, matching PYTINCTURE_BROWSER_FILES",
    )
    arguments = parser.parse_args()
    built = build_prebuilt_appcode(
        arguments.application,
        arguments.output_directory,
        modules_path=arguments.modules_path,
        browser_files=arguments.browser_files,
    )
    print(built)


if __name__ == "__main__":  # pragma: no cover
    main()
