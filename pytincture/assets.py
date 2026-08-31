"""Export self-hosted standalone browser assets from an installed wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from . import __version__


_FRONTEND_ROOT = Path(__file__).resolve().parent / "frontend"
_EXPORT_PATHS = (
    "pytincture.js",
    "sw.js",
    "dist",
    "integrity",
    "pyodide",
    "vendor/materialdesignicons",
)


def verify_browser_assets() -> Path:
    """Verify every release-locked browser asset in the installed wheel."""

    manifest_path = _FRONTEND_ROOT / "integrity" / f"pytincture-{__version__}.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Installed Pytincture integrity manifest is unavailable") from exc
    if manifest.get("schema") != 1 or manifest.get("framework_version") != __version__:
        raise RuntimeError("Installed Pytincture integrity manifest has the wrong version")

    frontend_root = _FRONTEND_ROOT.resolve()
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("Installed Pytincture integrity manifest has no assets")
    seen: set[str] = set()
    for entry in assets:
        relative = entry.get("path") if isinstance(entry, dict) else None
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise RuntimeError("Installed Pytincture integrity manifest is malformed")
        candidate = (_FRONTEND_ROOT / relative).resolve()
        if candidate == frontend_root or frontend_root not in candidate.parents:
            raise RuntimeError("Installed Pytincture integrity manifest escapes its asset root")
        try:
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(f"Installed Pytincture asset is missing: {relative}") from exc
        if actual != expected:
            raise RuntimeError(f"Installed Pytincture asset failed integrity verification: {relative}")
        seen.add(relative)
    return manifest_path


def export_browser_assets(destination: str | Path) -> Path:
    """Copy the reviewed runtime dependency set into a static asset directory."""

    verify_browser_assets()
    target_root = Path(destination).expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    for relative in _EXPORT_PATHS:
        source = _FRONTEND_ROOT / relative
        if not source.exists():
            raise RuntimeError(f"Installed Pytincture asset is missing: {relative}")
        destination_path = target_root / relative
        if source.is_dir():
            shutil.copytree(source, destination_path, dirs_exist_ok=True)
        else:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination_path)
    return target_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export self-hosted Pytincture runtime, Pyodide, icons, and integrity metadata."
    )
    parser.add_argument("destination", nargs="?", help="Static directory to populate")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the installed asset set without copying it",
    )
    args = parser.parse_args()
    if args.verify_only:
        manifest = verify_browser_assets()
        print(f"Verified Pytincture browser assets with {manifest}")
        return
    if not args.destination:
        parser.error("destination is required unless --verify-only is used")
    exported = export_browser_assets(args.destination)
    print(f"Exported Pytincture browser assets to {exported}")


if __name__ == "__main__":
    main()
