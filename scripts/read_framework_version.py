#!/usr/bin/env python3
import pathlib
import re
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: read_framework_version.py <path-to-__init__.py>")

    version_file = pathlib.Path(sys.argv[1])
    if not version_file.is_file():
        raise SystemExit(f"File not found: {version_file}")

    content = version_file.read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise SystemExit(f"Unable to locate __version__ in {version_file}")

    print(match.group(1))


if __name__ == "__main__":
    main()
