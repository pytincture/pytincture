#!/bin/sh
set -eu
cd "${LAMBDA_TASK_ROOT:-$(dirname "$0")}"
exec python -m uvicorn service:app --host 127.0.0.1 --port 8080 --no-access-log --no-proxy-headers
