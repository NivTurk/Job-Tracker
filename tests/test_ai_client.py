"""ClaudeClient against the real SDK with a mocked HTTP transport (no network).

Checks the request the SDK actually sends and how responses and errors are mapped.
"""

import json
from typing import Any

import httpx2 as httpx  # the SDK uses httpx2; its API matches httpx
import pytest
from fake_ai import valid_payload

from app.ai.client import AiApiError, ClaudeClient
from app.ai.prompt import RESPONSE_SCHEMA


def message_body(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5-20260601",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 3900,
            "output_tokens": 750,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def make_client(handler, max_retries: int = 0) -> ClaudeClient:  # type: ignore[no-untyped-def]
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ClaudeClient("sk-ant-test", max_retries=max_retries, http_client=http)


def complete(client: ClaudeClient):  # type: ignore[no-untyped-def]
    return client.complete("system text", [{"role": "user", "content": "hi"}], RESPONSE_SCHEMA, 2000)


def test_request_shape_and_response_mapping() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=message_body(json.dumps(valid_payload())), headers={"request-id": "req_123"})

    response = complete(make_client(handler))

    assert seen["url"].endswith("/v1/messages")
    assert seen["headers"]["x-api-key"] == "sk-ant-test"
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 2000
    assert body["system"] == "system text"
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
    assert "output_format" not in body  # the deprecated parameter

    assert json.loads(response.text)["score"] == 78
    assert response.stop_reason == "end_turn"
    assert response.model == "claude-sonnet-5-5"  # requested ID, so the price lookup works
    assert (response.input_tokens, response.output_tokens) == (3900, 750)
    assert response.request_id == "req_123"


def test_server_error_becomes_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})

    with pytest.raises(AiApiError) as info:
        complete(make_client(handler))
    assert info.value.outcome == "api_error"
    assert "529" in info.value.message


def test_bad_key_says_so() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    with pytest.raises(AiApiError) as info:
        complete(make_client(handler))
    assert "ANTHROPIC_API_KEY" in info.value.message


def test_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(AiApiError) as info:
        complete(make_client(handler))
    assert info.value.outcome == "api_error"


def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(AiApiError) as info:
        complete(make_client(handler))
    assert info.value.outcome == "timeout"


def test_sdk_retries_transient_errors() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, json={"type": "error", "error": {"type": "api_error", "message": "busy"}},
                                  headers={"retry-after-ms": "1"})
        return httpx.Response(200, json=message_body(json.dumps(valid_payload())))

    response = complete(make_client(handler, max_retries=2))
    assert attempts["count"] == 2
    assert response.stop_reason == "end_turn"
