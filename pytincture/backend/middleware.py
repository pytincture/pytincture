"""Reusable ASGI middleware owned by the Pytincture application factory."""

import base64
import json
import math
import time

from fastapi import HTTPException
from itsdangerous import BadSignature, TimestampSigner
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse


class RotatingSessionMiddleware(SessionMiddleware):
    """Accept old session signing keys and re-sign with the current key."""

    def __init__(
        self,
        app,
        secret_key,
        previous_secret_keys=None,
        absolute_max_age=None,
        max_cookie_bytes=3800,
        **kwargs,
    ):
        super().__init__(app, secret_key=secret_key, **kwargs)
        self.absolute_max_age = int(absolute_max_age or self.max_age or 0)
        self.max_cookie_bytes = int(max_cookie_bytes)
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
        presented_session_cookie = self.session_cookie in connection.cookies
        if (
            presented_session_cookie
            and len(connection.cookies[self.session_cookie].encode("utf-8"))
            <= self.max_cookie_bytes
        ):
            signed_data = connection.cookies[self.session_cookie].encode("utf-8")
            for signer in (self.signer, *self.previous_signers):
                try:
                    decoded = signer.unsign(signed_data, max_age=self.max_age)
                    scope["session"] = json.loads(base64.b64decode(decoded))
                    user = scope["session"].get("user")
                    issued_at = scope["session"].get("auth_issued_at")
                    auth_expires_at = scope["session"].get("auth_expires_at")
                    if isinstance(user, dict) and user.get("is_authenticated") is True:
                        if (
                            not isinstance(issued_at, (int, float))
                            or issued_at > time.time() + 60
                            or time.time() - issued_at > self.absolute_max_age
                            or (
                                auth_expires_at is not None
                                and (
                                    not isinstance(auth_expires_at, (int, float))
                                    or not math.isfinite(auth_expires_at)
                                    or auth_expires_at <= time.time()
                                )
                            )
                        ):
                            scope["session"] = {}
                            initial_session_was_empty = False
                            break
                    initial_session_was_empty = False
                    break
                except (BadSignature, ValueError, json.JSONDecodeError):
                    continue
            else:
                scope["session"] = {}
        else:
            scope["session"] = {}
            initial_session_was_empty = not presented_session_cookie

        path = str(scope.get("path") or "")
        path_parts = path.split("/")
        is_frontend_asset = (
            scope["type"] == "http"
            and str(scope.get("method") or "GET").upper() in {"GET", "HEAD"}
            and (
                path.startswith("/frontend/")
                or (len(path_parts) > 2 and path_parts[2] == "frontend")
            )
        )

        overflowed = False
        overflow_body_sent = False

        async def send_wrapper(message):
            nonlocal overflowed, overflow_body_sent
            if overflowed:
                if message["type"] == "http.response.body" and not overflow_body_sent:
                    overflow_body_sent = True
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b'{"detail":"Session exceeds configured size limit"}',
                            "more_body": False,
                        }
                    )
                return
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if is_frontend_asset and message.get("status") == 200:
                    if "set-cookie" in headers:
                        del headers["set-cookie"]
                    if "cache-control" not in headers:
                        headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif scope["session"]:
                    data = base64.b64encode(
                        json.dumps(
                            scope["session"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    signed = self.signer.sign(data).decode("utf-8")
                    if len(signed.encode("utf-8")) > self.max_cookie_bytes:
                        scope["session"] = {}
                        overflowed = True
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 500,
                                "headers": [
                                    (b"content-type", b"application/json"),
                                    (b"cache-control", b"private, no-store, max-age=0"),
                                    (b"content-length", b"50"),
                                    (
                                        b"set-cookie",
                                        (
                                            f"{self.session_cookie}=null; path={self.path}; "
                                            "expires=Thu, 01 Jan 1970 00:00:00 GMT; "
                                            f"{self.security_flags}"
                                        ).encode("latin-1"),
                                    ),
                                ],
                            }
                        )
                        return
                    response_max_age = self.max_age
                    auth_expires_at = scope["session"].get("auth_expires_at")
                    if isinstance(auth_expires_at, (int, float)) and math.isfinite(
                        auth_expires_at
                    ):
                        response_max_age = min(
                            response_max_age,
                            max(1, math.ceil(auth_expires_at - time.time())),
                        )
                    max_age = (
                        f"Max-Age={response_max_age}; " if response_max_age else ""
                    )
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
