import importlib.util
import asyncio
import json
import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


pytest.importorskip("boto3", reason="Lambda tooling is an optional deployment dependency")

spec = importlib.util.spec_from_file_location(
    "lambda_forward", Path(__file__).resolve().parents[1] / "scripts/lambda_forward.py"
)
forward = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = forward
spec.loader.exec_module(forward)


class LambdaEvents:
    def __init__(self, items):
        self.items = items
        self.closed = False

    def __iter__(self):
        return iter(self.items)

    def close(self):
        self.closed = True


def make_client(monkeypatch, events):
    calls = []
    class AWSClient:
        def invoke_with_response_stream(self, **kwargs):
            calls.append(kwargs)
            return {"EventStream": events}
    class Session:
        def __init__(self, **kwargs):
            pass
        def client(self, *args, **kwargs):
            return AWSClient()
    monkeypatch.setattr(forward.boto3, "Session", Session)
    return TestClient(forward.create_forwarder("demo", "us-east-1", "default", 8098),
                      base_url="http://127.0.0.1:8098"), calls


def test_split_metadata_binary_body_and_separate_cookies(monkeypatch):
    metadata = json.dumps({"statusCode": 201, "headers": {"content-type": "application/octet-stream",
        "transfer-encoding": "chunked"}, "cookies": ["one=1; Path=/", "two=2; Path=/"]}).encode()
    payload = metadata + b"\x00" * 8 + b"\xff\x00first"
    events = LambdaEvents([{"PayloadChunk": {"Payload": payload[:13]}},
        {"PayloadChunk": {"Payload": payload[13:]}},
        {"PayloadChunk": {"Payload": b"last"}}, {"InvokeComplete": {}}])
    client, calls = make_client(monkeypatch, events)
    with client:
        response = client.post("/bedrock_chat/test?x=1&x=2", json={"hi": "there"})
    assert response.status_code == 201
    assert response.content == b"\xff\x00firstlast"
    assert response.headers.get_list("set-cookie") == ["one=1; Path=/", "two=2; Path=/"]
    assert "transfer-encoding" not in response.headers
    event = json.loads(calls[0]["Payload"])
    assert event["rawQueryString"] == "x=1&x=2"
    assert event["requestContext"]["http"]["method"] == "POST"
    assert events.closed


@pytest.mark.parametrize("headers", [{"host": "evil.example"}, {"origin": "https://evil.example"},
    {"sec-fetch-site": "cross-site"}])
def test_forwarder_rejects_external_browser_requests_without_invoking(monkeypatch, headers):
    client, calls = make_client(monkeypatch, LambdaEvents([]))
    with client:
        assert client.get("/", headers=headers).status_code == 403
    assert calls == []


def test_premature_lambda_completion_is_bad_gateway_and_closes_stream(monkeypatch):
    events = LambdaEvents([{"InvokeComplete": {"ErrorCode": "Runtime.ExitError"}}])
    client, _ = make_client(monkeypatch, events)
    with client:
        assert client.get("/").status_code == 502
    assert events.closed


def test_browser_requests_queue_until_a_stream_finishes():
    async def check():
        release = asyncio.Event()
        three_started = asyncio.Event()
        active = 0
        peak = 0
        started = 0

        async def upstream(scope, receive, send):
            nonlocal active, peak, started
            active += 1
            started += 1
            peak = max(peak, active)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            if started == 3:
                three_started.set()
            try:
                await release.wait()
                await send({"type": "http.response.body", "body": b"done"})
            finally:
                active -= 1

        async def send(message):
            pass

        limiter = forward.InvocationLimit(upstream)
        tasks = [asyncio.create_task(limiter({"type": "http"}, None, send)) for _ in range(4)]
        await asyncio.wait_for(three_started.wait(), timeout=2)
        assert started == 3  # Response headers alone must not release the slot.
        release.set()
        await asyncio.gather(*tasks)
        assert started == 4
        assert peak == 3

    asyncio.run(check())


def test_first_bytes_reach_client_before_upstream_finishes(monkeypatch):
    """Fails if the bridge starts buffering the entire upstream response."""
    first_delivered = threading.Event()

    class SpacedEvents(LambdaEvents):
        def __iter__(self):
            metadata = json.dumps({"statusCode": 200,
                "headers": {"content-type": "text/event-stream"}}).encode()
            yield {"PayloadChunk": {"Payload": metadata + b"\0" * 8 + b"data: first\n\n"}}
            assert first_delivered.wait(timeout=2), "First chunk was buffered"
            yield {"PayloadChunk": {"Payload": b"data: last\n\n"}}
            yield {"InvokeComplete": {}}

    events = SpacedEvents([])
    client, _ = make_client(monkeypatch, events)

    async def check():
        bodies = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            if message["type"] == "http.response.body":
                bodies.append(message.get("body", b""))
                if message.get("body") == b"data: first\n\n":
                    first_delivered.set()
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "method": "GET", "scheme": "http", "path": "/stream", "root_path": "",
                 "query_string": b"", "headers": [(b"host", b"127.0.0.1:8098")],
                 "server": ("127.0.0.1", 8098), "client": ("127.0.0.1", 1234)}
        await asyncio.wait_for(client.app(scope, receive, send), timeout=5)
        assert b"".join(bodies) == b"data: first\n\ndata: last\n\n"

    asyncio.run(check())
    assert first_delivered.is_set()
    assert events.closed


@pytest.mark.parametrize("completion", [[], [{"InvokeComplete": {"ErrorCode": "Runtime.ExitError"}}]])
def test_failed_completion_is_not_reported_as_a_successful_body(monkeypatch, completion):
    metadata = json.dumps({"statusCode": 200}).encode() + b"\0" * 8
    events = LambdaEvents([{"PayloadChunk": {"Payload": metadata + b"partial"}}] + completion)
    client, _ = make_client(monkeypatch, events)
    with client, pytest.raises(RuntimeError, match="Lambda"):
        client.get("/")
    assert events.closed


def test_large_upload_is_rejected_before_invoking(monkeypatch):
    client, calls = make_client(monkeypatch, LambdaEvents([]))
    with client:
        assert client.post("/", content=b"x" * (128 * 1024 + 1)).status_code == 413
    assert calls == []


def test_binary_request_and_untrusted_forwarding_headers(monkeypatch):
    import base64
    metadata = json.dumps({"statusCode": 204}).encode() + b"\0" * 8
    client, calls = make_client(monkeypatch, LambdaEvents([
        {"PayloadChunk": {"Payload": metadata}}, {"InvokeComplete": {}}]))
    with client:
        response = client.post("/upload?a=1&a=2", content=b"\xff\x00\xfe",
            headers={"x-forwarded-host": "evil.example", "x-forwarded-for": "203.0.113.9",
                     "x-forwarded-proto": "https", "accept-encoding": "gzip"})
    assert response.status_code == 204
    event = json.loads(calls[0]["Payload"])
    assert base64.b64decode(event["body"]) == b"\xff\x00\xfe"
    assert event["rawQueryString"] == "a=1&a=2"
    assert event["headers"]["x-forwarded-proto"] == "http"
    assert "x-forwarded-host" not in event["headers"]
    assert "x-forwarded-for" not in event["headers"]
    assert "accept-encoding" not in event["headers"]
