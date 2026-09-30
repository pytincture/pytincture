import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from pytincture.backend.bff_requests import BFFRequestValidationError, parse_canonical_bff_body
from pytincture.backend.execution import IsolatedExecutionFailed, _decode_isolated_message
from pytincture.backend.middleware import RequestBodyLimitMiddleware
from pytincture.backend.results import BFFResultLimitExceeded, encode_bff_result


def test_large_string_and_bytes_are_not_counted_as_items():
    for value in ("x" * 2_000_000, b"x" * 2_000_000):
        encoded = encode_bff_result({"data": value}, max_bytes=3_000_000, max_items=1)
        assert len(encoded) == 2_000_011


def test_nested_item_diagnostic_counts_structure_without_disclosing_data():
    with pytest.raises(BFFResultLimitExceeded) as caught:
        encode_bff_result(
            {"private_field": [{"secret": "do-not-disclose"}] * 3},
            max_bytes=1000, max_items=5,
        )
    message = str(caught.value)
    assert "BFF_RESULT_MAX_ITEMS=5" in message
    assert "observed at least 6 aggregate items during materialization" in message
    assert "not bytes" in message
    assert "private_field" not in message and "do-not-disclose" not in message


def test_byte_diagnostic_reports_encoding_limit_not_item_limit():
    with pytest.raises(BFFResultLimitExceeded) as caught:
        encode_bff_result({"data": "é" * 4}, max_bytes=15, max_items=1)
    assert "BFF_RESULT_MAX_BYTES=15" in str(caught.value)
    assert "observed at least 18 bytes during JSON encoding" in str(caught.value)


def test_converted_custom_object_has_separate_item_budget():
    class Row:
        def __init__(self):
            self.hidden_a = 1
            self.hidden_b = 2

    with pytest.raises(BFFResultLimitExceeded) as caught:
        encode_bff_result(Row(), max_bytes=1000, max_items=1)
    assert "BFF_RESULT_MAX_ITEMS=1" in str(caught.value)
    assert "observed at least 2 aggregate items during JSON conversion" in str(caught.value)
    assert "hidden" not in str(caught.value)


def test_depth_diagnostic_reports_observed_depth():
    with pytest.raises(BFFResultLimitExceeded) as caught:
        encode_bff_result([[[0]]], max_bytes=1000, max_depth=2)
    assert "BFF_RESULT_MAX_DEPTH=2" in str(caught.value)
    assert "observed at least 3 levels" in str(caught.value)


@pytest.mark.parametrize("limits,setting,observed", [
    ({"max_bytes": 5}, "BFF_REQUEST_MAX_BYTES=5", "bytes"),
    ({"max_items": 4}, "BFF_REQUEST_MAX_ITEMS=4", "5 aggregate items"),
    ({"max_depth": 2}, "BFF_REQUEST_MAX_DEPTH=2", "3 levels"),
])
def test_request_diagnostics_identify_effective_limit(limits, setting, observed):
    options = {"max_bytes": 1000, "max_items": 100, "max_depth": 10, **limits}
    with pytest.raises(BFFRequestValidationError) as caught:
        parse_canonical_bff_body(b'{"args":[1,2,3],"kwargs":{}}', **options)
    assert setting in str(caught.value)
    assert observed in str(caught.value)
    assert "request parsing" in str(caught.value)


@pytest.mark.parametrize("chunked", [False, True])
def test_global_body_limit_preserves_diagnostic_in_response(chunked):
    app = FastAPI()

    @app.post("/")
    async def read_body(request: Request):
        return {"bytes": len(await request.body())}

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=4)
    with TestClient(app) as client:
        content = iter([b"abc", b"def"]) if chunked else b"abcdef"
        response = client.post("/", content=content)
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "MAX_REQUEST_BODY_BYTES=4" in detail
    assert "observed at least 6 bytes" in detail
    assert ("request buffering" if chunked else "Content-Length") in detail


def test_isolated_limit_diagnostic_validates_numeric_metadata():
    data = {"setting": "BFF_RESULT_MAX_ITEMS", "limit": 100, "observed": 101, "stage": "materialization"}
    payload = json.dumps(data).encode()
    assert _decode_isolated_message(
        b"PTB1L" + payload, result_max_bytes=1024, result_max_depth=8, result_max_items=100,
    ) == (b"L", payload)


@pytest.mark.parametrize("change", [
    {"limit": 10000}, {"observed": 99}, {"observed": True},
    {"stage": "sensitive-data"}, {"setting": "SECRET_KEY"}, {"extra": "sensitive-data"},
])
def test_isolated_limit_metadata_cannot_forward_arbitrary_child_text(change):
    data = {"setting": "BFF_RESULT_MAX_ITEMS", "limit": 100, "observed": 101, "stage": "materialization", **change}
    with pytest.raises(IsolatedExecutionFailed, match="invalid isolated BFF response"):
        _decode_isolated_message(
            b"PTB1L" + json.dumps(data).encode(),
            result_max_bytes=1024, result_max_depth=8, result_max_items=100,
        )


@pytest.mark.parametrize("field,expected", [
    ("max_request_body_bytes", 16 * 1024 * 1024),
    ("bff_request_max_bytes", 8 * 1024 * 1024),
    ("bff_request_max_items", 100_000),
    ("bff_result_max_bytes", 50 * 1024 * 1024),
    ("bff_result_max_items", 1_000_000),
    ("bff_stream_max_bytes", 50 * 1024 * 1024),
    ("bff_stream_max_items", 1_000_000),
])
def test_rc11_payload_defaults_match_environment_loading(field, expected):
    from pytincture import PytinctureConfig
    assert getattr(PytinctureConfig(), field) == expected
    assert getattr(PytinctureConfig.from_env({}), field) == expected


def test_stream_nested_item_failure_reports_stream_setting():
    from pytincture.backend.streaming import limited_sync_stream
    observed = []
    reasons = []
    assert list(limited_sync_stream(
        iter([{"rows": [1, 2, 3]}]), raw=False,
        max_seconds=10, max_bytes=1000, max_items=3,
        on_limit=observed.append, on_finish=lambda reason, count: reasons.append(reason),
    )) == []
    assert reasons == ["item-limit"]
    assert observed[0].setting == "BFF_STREAM_MAX_ITEMS"
    assert observed[0].limit == 3 and observed[0].observed == 4


def test_partial_stream_byte_failure_reports_total_configured_budget():
    from pytincture.backend.streaming import limited_sync_stream
    observed = []
    output = list(limited_sync_stream(
        iter([b"abcd", b"efgh"]), raw=True,
        max_seconds=10, max_bytes=6, max_items=100,
        on_limit=observed.append,
    ))
    assert output == [b"abcd"]
    assert observed[0].setting == "BFF_STREAM_MAX_BYTES"
    assert observed[0].limit == 6 and observed[0].observed == 8


def test_admission_reasons_distinguish_queue_size_from_deadline():
    import asyncio
    from pytincture.backend.limits import AdmissionRejected, AsyncAdmissionGate
    async def check():
        for waiters, reason in [(0, "queue-full"), (1, "queue-timeout")]:
            gate = AsyncAdmissionGate(1, waiters, 0.01)
            await gate.acquire()
            with pytest.raises(AdmissionRejected) as caught:
                await gate.acquire()
            assert caught.value.reason == reason
            gate.release()
    asyncio.run(check())
