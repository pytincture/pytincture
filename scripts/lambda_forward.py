"""Expose one private Lambda on loopback using InvokeWithResponseStream."""

import argparse
import asyncio
import json
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
import uvicorn


HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}


def _next(iterator):
    return next(iterator, None)


class InvocationLimit:
    """Queue browser asset requests within the demo's Lambda concurrency cap."""

    def __init__(self, app, limit=3):
        self.app = app
        self.slots = asyncio.Semaphore(limit)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Hold the slot until the entire streaming response has finished.
        async with self.slots:
            await self.app(scope, receive, send)


def create_forwarder(function_name, region, profile, port, concurrency=3):
    if not 1 <= concurrency <= 100:
        raise ValueError("Concurrency must be between 1 and 100")
    client = boto3.Session(profile_name=profile, region_name=region).client("lambda", config=Config(
        connect_timeout=10, read_timeout=100, max_pool_connections=12,
        retries={"mode": "standard", "total_max_attempts": 1},
    ))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(InvocationLimit, limit=concurrency)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def forward(request: Request, path: str):
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if request.headers.get("host") not in allowed:
            raise HTTPException(403, "Loopback access only")
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            raise HTTPException(403, "Same-origin access only")
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            raise HTTPException(403, "Same-origin access only")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 128 * 1024:
                raise HTTPException(413, "Demo request is too large")
        headers = {key: value for key, value in request.headers.items()
                   if key not in HOP_HEADERS | {"content-length", "accept-encoding"}
                   and not key.startswith("x-forwarded-")}
        headers["x-forwarded-proto"] = "http"
        import base64
        event = {
            "version": "2.0", "routeKey": "$default", "rawPath": request.url.path,
            "rawQueryString": request.url.query, "headers": headers,
            "requestContext": {"requestId": uuid.uuid4().hex, "stage": "$default",
                "domainName": request.url.hostname,
                "http": {"method": request.method, "path": request.url.path,
                         "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1",
                         "userAgent": request.headers.get("user-agent", "local-forwarder")}},
            "body": base64.b64encode(body).decode(), "isBase64Encoded": True,
        }
        try:
            response = await run_in_threadpool(client.invoke_with_response_stream,
                FunctionName=function_name, Payload=json.dumps(event).encode())
        except (ClientError, BotoCoreError) as exc:
            # Do not disclose AWS credentials, signed requests or account details.
            print("Lambda invocation failed:", type(exc).__name__, flush=True)
            raise HTTPException(502, "Lambda invocation failed; check the forwarding log.") from None
        stream = response["EventStream"]
        iterator = iter(stream)
        pending = bytearray()
        metadata = None
        remainder = b""
        try:
            while metadata is None:
                item = await run_in_threadpool(_next, iterator)
                if item is None or "InvokeComplete" in item:
                    raise HTTPException(502, "Lambda ended before sending HTTP metadata")
                pending.extend(item.get("PayloadChunk", {}).get("Payload", b""))
                boundary = pending.find(b"\x00" * 8)
                if boundary >= 0:
                    if boundary + 8 > 16 * 1024:
                        raise HTTPException(502, "Oversized Lambda metadata")
                    metadata = json.loads(pending[:boundary])
                    remainder = bytes(pending[boundary + 8:])
                elif len(pending) > 16 * 1024:
                    raise HTTPException(502, "Invalid Lambda metadata")
        except BaseException:
            await run_in_threadpool(stream.close)
            raise

        async def chunks():
            try:
                if remainder:
                    yield remainder
                while True:
                    item = await run_in_threadpool(_next, iterator)
                    if item is None:
                        raise RuntimeError("Lambda stream ended without InvokeComplete")
                    if "PayloadChunk" in item:
                        payload = item["PayloadChunk"]["Payload"]
                        if payload:
                            yield payload
                    elif "InvokeComplete" in item:
                        if item["InvokeComplete"].get("ErrorCode"):
                            raise RuntimeError("Lambda reported a streaming invocation error")
                        break
            finally:
                await run_in_threadpool(stream.close)

        outgoing = StreamingResponse(chunks(), status_code=metadata.get("statusCode", 200))
        raw_headers = []
        multi = {key.lower(): values for key, values in metadata.get("multiValueHeaders", {}).items()}
        for key, value in metadata.get("headers", {}).items():
            if key.lower() not in multi:
                multi[key.lower()] = [value]
        for key, values in multi.items():
            if key not in HOP_HEADERS:
                raw_headers.extend((key.encode("latin-1"), str(value).encode("latin-1")) for value in values)
        raw_headers.extend((b"set-cookie", value.encode("latin-1")) for value in metadata.get("cookies", []))
        outgoing.raw_headers = raw_headers
        return outgoing

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--concurrency", type=int, choices=range(1, 101), default=3,
                        metavar="1-100", help="Maximum active invocations; match Lambda reserved concurrency")
    args = parser.parse_args()
    app = create_forwarder(args.function, args.region, args.profile, args.port, args.concurrency)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
