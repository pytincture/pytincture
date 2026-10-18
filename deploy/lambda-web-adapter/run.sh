#!/bin/sh
set -eu
cd "${LAMBDA_TASK_ROOT:-$(dirname "$0")}"
exec python -m uvicorn "${PYTINCTURE_ASGI_APP:-service:app}" \
  --host 127.0.0.1 --port 8080 --workers 1 --no-access-log --no-proxy-headers \
  --loop asyncio --http h11 --ws none --lifespan on
