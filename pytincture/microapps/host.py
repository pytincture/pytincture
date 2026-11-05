"""Authorized ASGI catalog, browser artifact delivery and versioned BFF proxy."""
from __future__ import annotations

import inspect
import json
from urllib.parse import quote

from starlette.responses import FileResponse, JSONResponse, Response

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}


def create_microapp_host(base_app, manager, *, authorize):
    """Wrap an existing ASGI app with explicitly authorized live microapp routes.

    authorize(scope, app_id) must return True (or await True). app_id is None
    for catalog access and the selected id for each app. It must validate the
    platform session/environment itself; base-app middleware has not run here.
    There is deliberately no public installation endpoint.
    """
    if not callable(authorize):
        raise TypeError("An explicit platform authorization callback is required")

    async def allowed(scope, app_id):
        result = authorize(scope, app_id)
        if inspect.isawaitable(result):
            result = await result
        return result is True

    async def host(scope, receive, send):
        if scope["type"] == "lifespan":
            async def lifecycle_send(message):
                if message["type"] == "lifespan.startup.complete":
                    try:
                        await manager.start()
                    except Exception:
                        await send({"type": "lifespan.startup.failed", "message": "Microapp manager startup failed"})
                        return
                elif message["type"] == "lifespan.shutdown.complete":
                    await manager.close()
                await send(message)
            try:
                await base_app(scope, receive, lifecycle_send)
            finally:
                await manager.close()
            return
        if scope["type"] != "http":
            await base_app(scope, receive, send)
            return
        path = scope["path"]
        root = scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        prefix = manager.prefix
        if path != prefix and not path.startswith(prefix + "/"):
            await base_app(scope, receive, send)
            return
        if path == prefix + "/catalog":
            if scope["method"] != "GET":
                await Response(status_code=405)(scope, receive, send)
                return
            if not await allowed(scope, None):
                await Response(status_code=403)(scope, receive, send)
                return
            document = manager.catalog()
            document["apps"] = [app for app in document["apps"] if await allowed(scope, app["id"])]
            if root:
                for app in document["apps"]:
                    app["bff_base"] = root + app["bff_base"]
                    app["browser"]["url"] = root + app["browser"]["url"]
            await JSONResponse(document, headers={"Cache-Control": "private, no-store",
                                                   "Vary": "Cookie, Authorization"})(scope, receive, send)
            return
        relative = path.removeprefix(prefix + "/releases/")
        parts = relative.split("/")
        if len(parts) < 4:
            await Response(status_code=404)(scope, receive, send)
            return
        app_id, release_id, kind = parts[:3]
        key = f"{app_id}/{release_id}"
        release = manager._releases.get(key)
        if release is None or kind not in {"browser", "backend"}:
            await Response(status_code=404)(scope, receive, send)
            return
        if not await allowed(scope, app_id):
            await Response(status_code=403)(scope, receive, send)
            return
        worker = manager._workers.get(key)
        if worker is None or worker["process"].returncode is not None:
            await Response(status_code=503)(scope, receive, send)
            return
        if kind == "browser":
            filename = "/".join(parts[3:])
            if filename != release["browser"]["wheel"] or scope["method"] not in {"GET", "HEAD"}:
                await Response(status_code=404)(scope, receive, send)
                return
            await FileResponse(manager.store / key / "browser" / filename,
                media_type="application/zip", headers={"Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff", "Vary": "Cookie, Authorization"})(scope, receive, send)
            return
        # Only BFF routes, not arbitrary worker endpoints, are exposed.
        if len(parts) < 6 or parts[3] != release["application"] or parts[4] != "classcall":
            await Response(status_code=404)(scope, receive, send)
            return
        import httpx
        async def body():
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise ConnectionError("Client disconnected")
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise ValueError("Microapp request exceeds 32 MiB")
                yield chunk
                if not message.get("more_body", False):
                    break
        headers = [(k, v) for k, v in scope["headers"]
                   if k.lower() not in _HOP and not k.lower().startswith((b"x-pytincture-", b"x-forwarded-"))]
        if scope.get("client"):
            headers.append((b"x-pytincture-client", json.dumps(scope["client"]).encode()))
        headers.extend([(b"x-pytincture-worker", worker["token"].encode()),
                        (b"x-pytincture-root", (root + prefix + "/releases/" + key + "/backend").encode()),
                        (b"x-pytincture-scheme", scope["scheme"].encode())])
        target_path = "/" + "/".join(quote(part, safe="") for part in parts[3:])
        target = worker["url"] + target_path
        if scope.get("query_string"):
            target += "?" + scope["query_string"].decode("ascii")
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(60, read=None)) as client:
            try:
                request = client.build_request(scope["method"], target, headers=headers, content=body())
                response = await client.send(request, stream=True)
            except (httpx.HTTPError, ValueError):
                await Response(status_code=502)(scope, receive, send)
                return
            try:
                response_headers = [(k, v) for k, v in response.headers.raw
                                    if k.lower() not in _HOP and k.lower() != b"cache-control"]
                response_headers.append((b"cache-control", b"private, no-store"))
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": response_headers})
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b""})
            finally:
                await response.aclose()

    return host
