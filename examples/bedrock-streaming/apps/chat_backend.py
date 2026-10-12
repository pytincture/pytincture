"""Bedrock stays on the backend; Pytincture ships a generated BFF stub."""

import functools

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pytincture.dataclass import backend_for_frontend, bff_stream


MODEL_ID = "amazon.nova-micro-v1:0"
MAX_TOKENS = 512


@functools.lru_cache(maxsize=1)
def _client():
    return boto3.client("bedrock-runtime", region_name="us-east-1", config=Config(
        connect_timeout=5, read_timeout=20,
        retries={"mode": "adaptive", "total_max_attempts": 2},
        max_pool_connections=4,
    ))


def _messages(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 7:
        raise ValueError("Send between one and seven messages.")
    result = []
    total = 0
    for index, item in enumerate(messages):
        role = "user" if index % 2 == 0 else "assistant"
        if not isinstance(item, dict) or set(item) != {"role", "content"} or item["role"] != role:
            raise ValueError("Messages must alternate user and assistant, starting with user.")
        content = item["content"]
        if not isinstance(content, str) or not content.strip() or len(content) > 6000:
            raise ValueError("Each message must contain between one and 6,000 characters.")
        total += len(content)
        result.append({"role": role, "content": [{"text": content}]})
    if result[-1]["role"] != "user" or total > 16000 or len(messages[-1]["content"]) > 4000:
        raise ValueError("Keep the prompt under 4,000 characters and conversation under 16,000.")
    return result


@backend_for_frontend
class BedrockChat:
    @bff_stream()
    def reply(self, messages: list[dict]):
        try:
            conversation = _messages(messages)
        except ValueError as exc:
            yield {"type": "error", "code": "InvalidInput", "message": str(exc)}
            return
        events = None
        try:
            response = _client().converse_stream(
                modelId=MODEL_ID,
                system=[{"text": "You are a helpful assistant in a small streaming demonstration. "
                         "Give clear, concise answers. Use plain text or ordinary Markdown."}],
                messages=conversation,
                inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0.5},
            )
            events = response["stream"]
            stop_reason = None
            for event in events:
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"]["delta"].get("text", "")
                    if text:
                        yield {"type": "delta", "text": text}
                elif "messageStop" in event:
                    stop_reason = event["messageStop"]["stopReason"]
                elif "metadata" in event:
                    usage = event["metadata"].get("usage", {})
                    yield {"type": "usage", "input_tokens": usage.get("inputTokens", 0),
                           "output_tokens": usage.get("outputTokens", 0)}
                elif any(key.endswith("Exception") for key in event):
                    yield {"type": "error", "code": "UpstreamStreamError",
                           "message": "Bedrock interrupted this answer. Please retry."}
                    return
            if stop_reason is None:
                yield {"type": "error", "code": "IncompleteResponse",
                       "message": "Bedrock ended the connection before completing the answer."}
            else:
                yield {"type": "done", "stop_reason": stop_reason}
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "BedrockError")
            yield {"type": "error", "code": code,
                   "message": "Bedrock could not serve this request (" + code + ")."}
        except BotoCoreError:
            yield {"type": "error", "code": "BedrockConnectionError",
                   "message": "The connection to Bedrock failed. Please retry."}
        finally:
            if events is not None:
                events.close()
