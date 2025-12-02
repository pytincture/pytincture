#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/pytincture/frontend"
PY_VERSION_FILE="$REPO_ROOT/pytincture/__init__.py"

if [[ ! -f "$PY_VERSION_FILE" ]]; then
    echo "Unable to locate $PY_VERSION_FILE"
    exit 1
fi

FRAMEWORK_VERSION="$(python3 "$SCRIPT_DIR/read_framework_version.py" "$PY_VERSION_FILE")"

echo "Framework version detected: $FRAMEWORK_VERSION"

cd "$FRONTEND_DIR"

npm install
npm run sync-version

PACKAGE_VERSION="$(node -p "require('./package.json').version")"
PACKAGE_NAME="$(node -p "require('./package.json').name")"

if [[ "$PACKAGE_VERSION" != "$FRAMEWORK_VERSION" ]]; then
    echo "Version mismatch after sync (package: $PACKAGE_VERSION, framework: $FRAMEWORK_VERSION)"
    exit 1
fi

echo "Building ${PACKAGE_NAME}@${PACKAGE_VERSION}"
npm run build

if npm view "${PACKAGE_NAME}@${PACKAGE_VERSION}" version >/dev/null 2>&1; then
    echo "${PACKAGE_NAME}@${PACKAGE_VERSION} is already published. Skipping publish."
    exit 0
else
    echo "${PACKAGE_NAME}@${PACKAGE_VERSION} not found on npm; publishing."
fi

npm publish --access public
