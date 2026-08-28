"""Reusable ASGI middleware owned by the Pytincture application factory."""

import base64
import json

from fastapi import HTTPException
from itsdangerous import BadSignature, TimestampSigner
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse


class RotatingSessionMiddleware(SessionMiddleware):
    """Accept old session signing keys and re-sign with the current key."""

    def __init__(self, app, secret_key, previous_secret_keys=None, **kwargs):
        super().__init__(app, secret_key=secret_key, **kwargs)
        self.previous_signers = [
            TimestampSigner(str(key))
            for key in (previous_secret_keys or [])
            if str(key)
        ]

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        if self.session_cookie in connection.cookies:
            signed_data = connection.cookies[self.session_cookie].encode("utf-8")
            for signer in (self.signer, *self.previous_signers):
                try:
                    decoded = signer.unsign(signed_data, max_age=self.max_age)
                    scope["session"] = json.loads(base64.b64decode(decoded))
                    initial_session_was_empty = False
                    break
                except (BadSignature, ValueError, json.JSONDecodeError):
                    continue
            else:
                scope["session"] = {}
        else:
            scope["session"] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if scope["session"]:
                    data = base64.b64encode(
                        json.dumps(scope["session"]).encode("utf-8")
                    )
                    signed = self.signer.sign(data).decode("utf-8")
                    max_age = f"Max-Age={self.max_age}; " if self.max_age else ""
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}={signed}; path={self.path}; "
                        f"{max_age}{self.security_flags}",
                    )
                elif not initial_session_was_empty:
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}=null; path={self.path}; "
                        f"expires=Thu, 01 Jan 1970 00:00:00 GMT; {self.security_flags}",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestBodyLimitMiddleware:
    """Reject request bodies that exceed the configured byte limit."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    response = JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(
                        status_code=413, detail="Request body too large"
                    )
            return message

        try:
            await self.app(scope, limited_receive, send)
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            response = JSONResponse(
                {"detail": "Request body too large"}, status_code=413
            )
            await response(scope, receive, send)
