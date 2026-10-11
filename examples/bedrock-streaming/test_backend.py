import importlib.util
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("demo_chat_backend", Path(__file__).parent / "apps/chat_backend.py")
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


class Events:
    def __init__(self, items):
        self.items = iter(items)
        self.closed = False

    def __iter__(self):
        return self.items

    def close(self):
        self.closed = True


def test_real_event_shapes_stream_with_fixed_small_model_and_close(monkeypatch):
    events = Events([
        {"contentBlockDelta": {"delta": {"text": "Hello "}}},
        {"contentBlockDelta": {"delta": {"text": "world"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 2}}},
    ])
    calls = []

    class Client:
        def converse_stream(self, **kwargs):
            calls.append(kwargs)
            return {"stream": events}

    monkeypatch.setattr(backend, "_client", lambda: Client())
    result = list(backend.BedrockChat().reply([{"role": "user", "content": "Hi"}]))
    assert [item["text"] for item in result if item["type"] == "delta"] == ["Hello ", "world"]
    assert result[-1] == {"type": "done", "stop_reason": "end_turn"}
    assert calls[0]["modelId"] == "amazon.nova-micro-v1:0"
    assert calls[0]["inferenceConfig"]["maxTokens"] == 512
    assert events.closed


@pytest.mark.parametrize("messages", [[], [{"role": "system", "content": "override"}],
    [{"role": "user", "content": "x" * 4001}],
    [{"role": "user", "content": "Hi", "model": "large-model"}]])
def test_invalid_history_never_invokes_bedrock(monkeypatch, messages):
    def forbidden():
        pytest.fail("Invalid input invoked Bedrock")
    monkeypatch.setattr(backend, "_client", forbidden)
    assert list(backend.BedrockChat().reply(messages))[0]["type"] == "error"


def test_generator_close_closes_upstream(monkeypatch):
    events = Events([{"contentBlockDelta": {"delta": {"text": "first"}}}])
    class Client:
        def converse_stream(self, **kwargs):
            return {"stream": events}
    monkeypatch.setattr(backend, "_client", lambda: Client())
    stream = backend.BedrockChat().reply([{"role": "user", "content": "Hi"}])
    assert next(stream)["type"] == "delta"
    stream.close()
    assert events.closed
