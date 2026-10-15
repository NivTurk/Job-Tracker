"""A stand-in for ClaudeClient: returns queued replies and records what it was sent."""

import json
from typing import Any

from app.ai.client import AiApiError, AiResponse


def valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "score": 78,
        "strengths": [
            {"point": "Hands-on packet parsing in Python", "cv_evidence": "Network traffic analyzer in Python"},
            {"point": "C experience", "cv_evidence": "Checksum utilities in C"},
        ],
        "missing_must_haves": [
            {"requirement": "Linux kernel development", "note": "No kernel or driver work in the CV."},
        ],
        "weak_matches": [
            {"requirement": "Embedded C", "cv_evidence": "Checksum utilities in C", "why_weak": "User-space only."},
        ],
        "edit_suggestions": [
            {"section": "Skills", "change": "List C before Python.", "reason": "C is the main requirement."},
            {"section": "Projects", "change": "Add the packet rate the analyzer handles.", "reason": "Shows scale."},
            {"section": "Summary", "change": "If true, mention any RTOS coursework.", "reason": "RTOS is preferred."},
        ],
    }
    payload.update(overrides)
    return payload


def reply(payload: dict[str, Any] | str, stop_reason: str = "end_turn", **usage: Any) -> AiResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return AiResponse(
        text=text,
        stop_reason=stop_reason,
        model="claude-sonnet-5-5",
        input_tokens=usage.get("input_tokens", 4000),
        output_tokens=usage.get("output_tokens", 800),
        cache_read_tokens=0,
        cache_write_tokens=0,
        request_id="req_test",
        latency_ms=1234,
    )


def api_down(message: str = "Could not connect to the Claude API.") -> AiApiError:
    return AiApiError("api_error", message, latency_ms=50)


class FakeAiClient:
    def __init__(self, *replies: AiResponse | Exception, model: str = "claude-sonnet-5-5") -> None:
        self.model = model
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def queue(self, *replies: AiResponse | Exception) -> None:
        self.replies.extend(replies)

    def complete(
        self, system: str, messages: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> AiResponse:
        self.calls.append({"system": system, "messages": messages, "schema": schema, "max_tokens": max_tokens})
        if not self.replies:
            raise AssertionError("FakeAiClient got an unexpected call")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
