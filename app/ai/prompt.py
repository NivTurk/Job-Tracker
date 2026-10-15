"""Prompt, JSON schema and output validation for the AI half of the score.

Bump PROMPT_VERSION whenever the prompt or schema changes in a way that
should invalidate cached results. It is part of the cache key.
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

PROMPT_VERSION = "score-v1"

MIN_SUGGESTIONS = 3
MAX_SUGGESTIONS = 5
# Longer lists are trimmed rather than retried.
MAX_STRENGTHS = 5
MAX_WEAK = 8
MAX_MISSING = 10

SYSTEM_PROMPT = f"""You review how well a CV fits a job description, the way an experienced technical recruiter would.

The job description or the CV may be in Hebrew, English or both. Always answer in English.

Rules:
- Base every claim on the two texts. Quote CV evidence verbatim (in its original language), short enough to locate.
- Never invent experience, skills, dates or numbers the CV does not contain.
- "Missing must-haves" are requirements the JD presents as mandatory that the CV does not show. Skip nice-to-haves there.
- "Weak matches" are requirements the CV covers only partly, indirectly or by implication (related tool, coursework instead of work experience, shorter than asked).
- Edit suggestions must be concrete: name the CV section and say exactly what to change. They may only reword, reorder or surface things the CV already supports. If a suggestion depends on something the CV does not state, start it with "If true, ".
- Give {MIN_SUGGESTIONS} to {MAX_SUGGESTIONS} edit suggestions and at most {MAX_STRENGTHS} strengths, most important first.

Score from 0 to 100 using this scale:
- 90-100: meets every required qualification and most preferred ones.
- 70-89: meets nearly all required qualifications.
- 50-69: missing some required qualifications.
- 0-49: missing several core requirements, or a different kind of role.
Judge equivalent experience fairly (for example, a strong personal project can cover a skill), but do not inflate the score for effort or potential."""


def build_user_message(jd_text: str, cv_text: str) -> str:
    return (
        "<job_description>\n"
        f"{jd_text.strip()}\n"
        "</job_description>\n\n"
        "<cv>\n"
        f"{cv_text.strip()}\n"
        "</cv>\n\n"
        "Evaluate how well this CV fits this job."
    )


def correction_message(reason: str) -> str:
    return f"That output failed validation: {reason}. Return the corrected JSON only."


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}

# Sent to the API as output_config.format. The structured-outputs grammar does
# not support minimum/maximum or minItems above 1, so ranges and counts are
# checked locally in validate_output().
RESPONSE_SCHEMA: dict[str, Any] = _object({
    "score": {"type": "integer"},
    "strengths": {"type": "array", "items": _object({"point": _STRING, "cv_evidence": _STRING})},
    "missing_must_haves": {"type": "array", "items": _object({"requirement": _STRING, "note": _STRING})},
    "weak_matches": {
        "type": "array",
        "items": _object({"requirement": _STRING, "cv_evidence": _STRING, "why_weak": _STRING}),
    },
    "edit_suggestions": {
        "type": "array",
        "items": _object({"section": _STRING, "change": _STRING, "reason": _STRING}),
    },
})


# --- Validation ------------------------------------------------------------------


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class Strength(_Item):
    point: str
    cv_evidence: str


class MissingMustHave(_Item):
    requirement: str
    note: str


class WeakMatch(_Item):
    requirement: str
    cv_evidence: str
    why_weak: str


class EditSuggestion(_Item):
    section: str
    change: str
    reason: str


class AiResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    score: int
    strengths: list[Strength]
    missing_must_haves: list[MissingMustHave]
    weak_matches: list[WeakMatch]
    edit_suggestions: list[EditSuggestion]


@dataclass
class ValidationFailure(Exception):
    outcome: str  # invalid_json, invalid_schema, out_of_range, refusal, max_tokens
    reason: str

    def __str__(self) -> str:
        return self.reason


def validate_output(text: str, stop_reason: str | None = None) -> AiResult:
    """Parse and check the model's reply. Raises ValidationFailure."""
    if stop_reason == "refusal":
        raise ValidationFailure("refusal", "the model declined to answer")
    if stop_reason == "max_tokens":
        raise ValidationFailure("max_tokens", "the reply was cut off at the token limit")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure("invalid_json", f"not valid JSON ({exc.msg} at position {exc.pos})") from exc
    if not isinstance(data, dict):
        raise ValidationFailure("invalid_schema", "the top level must be a JSON object")
    try:
        result = AiResult.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "output"
        raise ValidationFailure("invalid_schema", f"{where}: {first['msg']}") from exc

    if not 0 <= result.score <= 100:
        raise ValidationFailure("out_of_range", f"score must be between 0 and 100, got {result.score}")
    count = len(result.edit_suggestions)
    if not MIN_SUGGESTIONS <= count <= MAX_SUGGESTIONS:
        raise ValidationFailure(
            "out_of_range", f"expected {MIN_SUGGESTIONS}-{MAX_SUGGESTIONS} edit suggestions, got {count}"
        )

    result.strengths = result.strengths[:MAX_STRENGTHS]
    result.weak_matches = result.weak_matches[:MAX_WEAK]
    result.missing_must_haves = result.missing_must_haves[:MAX_MISSING]
    return result
