"""Private loopback worker, started only by the deployment-owned manager."""
import json
import hmac
import socket
import sys


def run():
    payload = json.loads(sys.stdin.read())
    sys.path.insert(0, payload["site"])
    from pytincture.configuration import PytinctureConfig
    from pytincture.packaged_apps import create_packaged_app
    import uvicorn

    config = PytinctureConfig.from_env(payload["config"])
    app = create_packaged_app(payload["distribution"], config,
                              expected_version=payload["version"])
    token = payload["token"].encode()

    async def private(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if not hmac.compare_digest(headers.get(b"x-pytincture-worker", b""), token):
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            scope = dict(scope)
            scope["root_path"] = headers.get(b"x-pytincture-root", b"").decode()
            scope["scheme"] = headers.get(b"x-pytincture-scheme", b"http").decode()
            if b"x-pytincture-client" in headers:
                scope["client"] = tuple(json.loads(headers[b"x-pytincture-client"]))
            scope["headers"] = [(k, v) for k, v in scope["headers"] if not k.startswith(b"x-pytincture-")]
        await app(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(private, log_level="warning", proxy_headers=False,
                                           lifespan="on"))
    import asyncio
    asyncio.run(server.serve(sockets=[socket.socket(fileno=payload["socket"])]))
