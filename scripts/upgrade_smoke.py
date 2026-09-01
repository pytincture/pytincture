#!/usr/bin/env python3
"""Probe the documented 0.10-compatible service surface in an installed version."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="pytincture-upgrade-") as directory:
        root = Path(directory)
        (root / "widget.py").write_text(
            '__widgetset__ = "dhxpyt"\n__version__ = "0.9.18"\nclass MainWindow:\n    pass\n',
            encoding="utf-8",
        )
        (root / "data.py").write_text(
            "from pytincture.dataclass import backend_for_frontend\n"
            "@backend_for_frontend\n"
            "class Data:\n"
            "    server_secret = 'must-not-ship'\n"
            "    def status(self):\n"
            "        return {'ready': True}\n",
            encoding="utf-8",
        )
        (root / "demo.py").write_text(
            "from widget import MainWindow\n"
            "from data import Data\n"
            "class demo(MainWindow):\n"
            "    def load_ui(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(root))
        os.environ["MODULES_PATH"] = str(root)
        os.environ["ENABLE_MCP"] = "false"

        import pytincture
        from fastapi.testclient import TestClient
        from pytincture import get_modules_path, set_modules_path
        from pytincture.backend.app import app, get_widgetset
        from pytincture.dataclass import backend_for_frontend, bff_stream

        set_modules_path(str(root))
        with TestClient(app) as client:
            page = client.get("/demo")
            archive_response = client.get("/demo/appcode/appcode.pyt")
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            names = sorted(archive.namelist())
            data_source = archive.read("data.py").decode()

        result = {
            "version": pytincture.__version__,
            "modules_path": get_modules_path() == str(root),
            "page_status": page.status_code,
            "archive_status": archive_response.status_code,
            "archive_names": names,
            "server_secret_absent": "must-not-ship" not in data_source,
            "widgetset": get_widgetset("demo", str(root)),
            "public_imports": all(
                value is not None
                for value in (
                    backend_for_frontend,
                    bff_stream,
                    get_modules_path,
                    set_modules_path,
                )
            ),
        }
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
