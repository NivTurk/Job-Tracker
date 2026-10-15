"""Output validation, the JSON schema sent to the API, and cost estimates."""

import json
from decimal import Decimal
from typing import Any

import pytest
from fake_ai import valid_payload

from app.ai.client import PRICES, estimate_cost
from app.ai.prompt import RESPONSE_SCHEMA, ValidationFailure, build_user_message, validate_output


def check(payload: dict[str, Any] | str, stop_reason: str | None = "end_turn"):  # type: ignore[no-untyped-def]
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return validate_output(text, stop_reason)


def outcome_of(payload: dict[str, Any] | str, stop_reason: str | None = "end_turn") -> str:
    with pytest.raises(ValidationFailure) as info:
        check(payload, stop_reason)
    return info.value.outcome


def test_valid_output() -> None:
    result = check(valid_payload())
    assert result.score == 78
    assert len(result.edit_suggestions) == 3
    assert result.strengths[0].cv_evidence == "Network traffic analyzer in Python"


def test_hebrew_evidence_is_kept() -> None:
    payload = valid_payload(strengths=[{"point": "Linux", "cv_evidence": "ניסיון בלינוקס"}])
    assert check(payload).strengths[0].cv_evidence == "ניסיון בלינוקס"


def test_malformed_json() -> None:
    assert outcome_of('{"score": 78, ') == "invalid_json"
    assert outcome_of("") == "invalid_json"


def test_top_level_must_be_object() -> None:
    assert outcome_of("[1, 2]") == "invalid_schema"


def test_missing_field() -> None:
    payload = valid_payload()
    del payload["weak_matches"]
    assert outcome_of(payload) == "invalid_schema"


def test_extra_field() -> None:
    assert outcome_of(valid_payload(verdict="good")) == "invalid_schema"


def test_score_must_be_integer() -> None:
    assert outcome_of(valid_payload(score="78")) == "invalid_schema"
    assert outcome_of(valid_payload(score=77.5)) == "invalid_schema"


def test_blank_strings_rejected() -> None:
    payload = valid_payload(strengths=[{"point": "  ", "cv_evidence": "x"}])
    assert outcome_of(payload) == "invalid_schema"


@pytest.mark.parametrize("score", [-1, 101, 250])
def test_score_out_of_range(score: int) -> None:
    assert outcome_of(valid_payload(score=score)) == "out_of_range"


@pytest.mark.parametrize("score", [0, 100])
def test_score_bounds_accepted(score: int) -> None:
    assert check(valid_payload(score=score)).score == score


@pytest.mark.parametrize("count", [0, 2, 6])
def test_suggestion_count_out_of_range(count: int) -> None:
    suggestions = [{"section": "Skills", "change": f"Change {n}", "reason": "r"} for n in range(count)]
    assert outcome_of(valid_payload(edit_suggestions=suggestions)) == "out_of_range"


def test_long_lists_are_trimmed_not_rejected() -> None:
    strengths = [{"point": f"p{n}", "cv_evidence": "e"} for n in range(9)]
    assert len(check(valid_payload(strengths=strengths)).strengths) == 5


def test_refusal_and_cut_off() -> None:
    assert outcome_of(valid_payload(), stop_reason="refusal") == "refusal"
    assert outcome_of('{"score": 7', stop_reason="max_tokens") == "max_tokens"


def test_schema_uses_only_supported_keywords() -> None:
    """Structured outputs reject minimum/maximum/minLength and minItems above 1 with a 400."""
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                   "minLength", "maxLength", "maxItems", "pattern"}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert not unsupported & node.keys(), node
            if "minItems" in node:
                assert node["minItems"] in (0, 1)
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(RESPONSE_SCHEMA)


def test_schema_matches_validator() -> None:
    """Every field the schema requires is one the validator accepts, and the reverse."""
    assert set(RESPONSE_SCHEMA["required"]) == set(valid_payload())


def test_user_message_wraps_both_texts() -> None:
    message = build_user_message("  JD here ", "CV here\n")
    assert "<job_description>\nJD here\n</job_description>" in message
    assert "<cv>\nCV here\n</cv>" in message
    assert message.index("<job_description>") < message.index("<cv>")


def test_cost_estimate() -> None:
    # Sonnet 5.5: 4000 * $2/M + 800 * $10/M = 0.008 + 0.008
    assert estimate_cost("claude-sonnet-5-5", 4000, 800) == Decimal("0.016000")
    # Haiku 5.5: 4000 * $0.10/M + 800 * $0.50/M
    assert estimate_cost("claude-haiku-5-5", 4000, 800) == Decimal("0.000800")


def test_cost_includes_cache_tokens() -> None:
    cost = estimate_cost("claude-sonnet-5-5", 1000, 0, cache_read_tokens=10_000, cache_write_tokens=1000)
    # 1000 * 2 + 10000 * 0.10 + 1000 * 2.50 = 5500 per million
    assert cost == Decimal("0.005500")


def test_cost_unknown_model_is_none() -> None:
    assert estimate_cost("some-future-model", 4000, 800) is None
    assert estimate_cost("claude-sonnet-5-5", None, 800) is None


def test_default_model_has_a_price() -> None:
    from app.ai.client import MODEL

    assert MODEL in PRICES
