"""Authorized ASGI catalog, browser artifact delivery and versioned BFF proxy."""
from __future__ import annotations

import inspect
import json
from urllib.parse import quote, urlsplit

from starlette.responses import FileResponse, JSONResponse, Response

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}


def create_microapp_host(base_app, manager, *, authorize, authorize_install=None):
    """Wrap an existing ASGI app with explicitly authorized live microapp routes.

    authorize(scope, app_id) must return True (or await True). app_id is None
    for catalog access and the selected id for each app. It must validate the
    platform session/environment itself; base-app middleware has not run here.
    An installation endpoint exists only with EnvironmentMicroappManager and an
    explicit authorize_install callback returning a trusted approved app record.
    """
    if not callable(authorize):
        raise TypeError("An explicit platform authorization callback is required")

    async def allowed(scope, app_id):
        result = authorize(scope, app_id)
        if inspect.isawaitable(result):
            result = await result
        return result is True

    if hasattr(manager, 'read_manifest') and hasattr(base_app, 'state'):
        base_app.state.pytincture_environment_catalog_url = manager.prefix + '/catalog'
        base_app.state.pytincture_environment_asset_origins = list(manager.asset_origins)

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
        if path == prefix + '/installations' and hasattr(manager, 'request_install'):
            if authorize_install is None:
                await Response(status_code=404)(scope, receive, send); return
            if scope['method'] != 'POST':
                await Response(status_code=405)(scope, receive, send); return
            headers = dict(scope['headers'])
            origin = headers.get(b'origin', b'').decode()
            expected_origin = manager.config.canonical_origin or (scope['scheme'] + '://' + headers.get(b'host', b'').decode())
            if (origin and origin != expected_origin) or (b'cookie' in headers and not origin):
                await Response(status_code=403)(scope, receive, send); return
            if headers.get(b'x-pytincture-install') != b'1' or headers.get(b'content-type', b'').split(b';')[0] != b'application/json':
                await Response(status_code=415)(scope, receive, send); return
            if not await allowed(scope, None):
                await Response(status_code=403)(scope, receive, send); return
            data = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect': return
                data.extend(message.get('body', b''))
                if len(data) > 1024 * 1024:
                    await Response(status_code=413)(scope, receive, send); return
                if not message.get('more_body'): break
            from .object_store import ObjectConflict
            try:
                command = json.loads(data)
                if not isinstance(command, dict) or set(command) - {'app_id', 'version', 'installed', 'expected_revision'}:
                    raise ValueError('Invalid install command')
                if type(command.get('installed')) is not bool or not isinstance(command.get('app_id'), str):
                    raise ValueError('Invalid install selection')
                approved = authorize_install(scope, command)
                if inspect.isawaitable(approved): approved = await approved
                if not isinstance(approved, dict):
                    await Response(status_code=403)(scope, receive, send); return
                if approved.get('installed') != command['installed']:
                    raise ValueError('Approved state differs from requested state')
                if command['installed']:
                    from packaging.version import Version
                    if Version(approved['version']) != Version(command['version']):
                        raise ValueError('Approved version differs from requested version')
                result = await manager.request_install(command['app_id'], approved,
                                                       expected_revision=command.get('expected_revision'))
            except ObjectConflict:
                await JSONResponse({'error': 'Environment revision changed'}, status_code=409)(scope, receive, send); return
            except (ValueError, KeyError, TypeError):
                await JSONResponse({'error': 'Invalid approved installation'}, status_code=400)(scope, receive, send); return
            await JSONResponse(result, status_code=202, headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return
        if path == prefix + '/environment' and hasattr(manager, 'read_manifest'):
            if scope['method'] != 'GET':
                await Response(status_code=405)(scope, receive, send); return
            if not await allowed(scope, None):
                await Response(status_code=403)(scope, receive, send); return
            from .environment_manifest import selection_id
            document, _ = await manager.read_manifest()
            await JSONResponse({'revision': document['revision'], 'desired': selection_id(document),
                'active': document['active'] and document['active']['selection'],
                'serving': manager._bundle and manager._bundle['selection'],
                'serving_error': manager._last_error,
                'building': document['build'] is not None, 'error': document['error']},
                headers={'Cache-Control': 'private, no-store'})(scope, receive, send)
            return
        if path.startswith(prefix + '/bundles/') and hasattr(manager, 'browser_bundle'):
            import re
            matched = re.fullmatch(re.escape(prefix) + r'/bundles/([a-f0-9]{64})/environment\.zip', path)
            if not matched or scope['method'] not in {'GET', 'HEAD'}:
                await Response(status_code=404)(scope, receive, send); return
            if not await allowed(scope, None):
                await Response(status_code=403)(scope, receive, send); return
            candidates = ([manager._bundle] if manager._bundle else [])
            if manager._document: candidates += manager._document['history']
            bundle = next((item for item in candidates if item['selection'] == matched[1]), None)
            if bundle is None:
                await Response(status_code=404)(scope, receive, send); return
            if not all([await allowed(scope, app_id) for app_id in bundle['inventory']['releases']]):
                await Response(status_code=403)(scope, receive, send); return
            resolved = await manager.browser_bundle(matched[1])
            if resolved is None:
                await Response(status_code=404)(scope, receive, send); return
            bundle, bundle_file = resolved
            await FileResponse(bundle_file, media_type='application/zip',
                headers={'Cache-Control': 'private, no-cache', 'ETag': '"' + bundle['sha256'] + '"',
                         'X-Content-Type-Options': 'nosniff', 'Vary': 'Cookie, Authorization'})(scope, receive, send)
            return
        if path == prefix + "/catalog":
            if scope["method"] != "GET":
                await Response(status_code=405)(scope, receive, send)
                return
            if not await allowed(scope, None):
                await Response(status_code=403)(scope, receive, send)
                return
            document = manager.catalog()
            all_apps = document['apps']
            document["apps"] = [app for app in all_apps if await allowed(scope, app["id"])]
            if len(document['apps']) != len(all_apps):
                document.pop('snapshot', None)  # Never leak other apps through an aggregate archive.
            if root:
                for app in document["apps"]:
                    app["bff_base"] = root + app["bff_base"]
                    if app['browser']['url'].startswith('/'):
                        app["browser"]["url"] = root + app["browser"]["url"]
                if document.get('snapshot') and document['snapshot']['url'].startswith('/'):
                    document['snapshot']['url'] = root + document['snapshot']['url']
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
        if release is None and kind == 'backend' and app_id in manager._active and hasattr(manager, 'ensure_release'):
            if await allowed(scope, app_id):
                await manager.ensure_release(app_id, release_id)
                release = manager._releases.get(key)
        if release is None or app_id not in manager._active or kind not in {"browser", "backend"}:
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

    async def environment_host(scope, receive, send):
        async def with_revision(message):
            bundle = getattr(manager, '_bundle', None)
            if scope['type'] == 'http' and message['type'] == 'http.response.start' and bundle:
                message = dict(message)
                message['headers'] = [(key, value) for key, value in message.get('headers', [])
                    if key.lower() not in {b'x-pytincture-bundle', b'x-pytincture-revision'}]
                message['headers'].extend([(b'x-pytincture-bundle', bundle['selection'].encode()),
                                          (b'x-pytincture-revision', str(bundle['revision']).encode())])
            await send(message)
        await host(scope, receive, with_revision)
    return environment_host
