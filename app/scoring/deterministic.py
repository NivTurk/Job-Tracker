"""Deterministic half of the ATS score.

score = keyword_share * keyword_score + (1 - keyword_share) * format_score

keyword_score: weighted share of the JD's known skills found in the CV.
  Required skills weigh more than preferred ones; a skill that is only implied
  by another (e.g. Networking by TCP/IP) earns partial credit.
format_score: 100 minus fixed penalties for format warnings.

Same CV text + same JD text + same synonyms.yaml + same weights always give
the same result. Nothing here is cached or random.
"""

from dataclasses import dataclass, field
from typing import Any

from app.scoring.format_checks import format_score
from app.scoring.matching import Lexicon, MatchResult, SkillMatch, load_lexicon, match_skills
from app.scoring.requirements import JdSections, split_sections


@dataclass(frozen=True)
class ScoringWeights:
    required_weight: float = 3.0
    preferred_weight: float = 1.0
    implied_credit: float = 0.75
    keyword_share: float = 0.8


@dataclass
class DeterministicScore:
    scorable: bool
    reason: str | None = None
    score: int | None = None
    keyword_score: int | None = None
    format_score: int | None = None
    match: MatchResult | None = None
    sections: JdSections | None = None
    format_warnings: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lexicon_version: str = ""

    def _items(self, group: str, status: str) -> list[SkillMatch]:
        if self.match is None:
            return []
        items = self.match.required if group == "required" else self.match.preferred
        return [item for item in items if item.status == status]

    @property
    def matched(self) -> list[SkillMatch]:
        return self._items("required", "matched") + self._items("preferred", "matched")

    @property
    def implied(self) -> list[SkillMatch]:
        return self._items("required", "implied") + self._items("preferred", "implied")

    @property
    def missing_required(self) -> list[SkillMatch]:
        return self._items("required", "missing")

    @property
    def missing_preferred(self) -> list[SkillMatch]:
        return self._items("preferred", "missing")


def _round(value: float) -> int:
    """Round half up, so 72.5 is 73 (Python's round() would give 72)."""
    return int(value + 0.5)


def keyword_score(match: MatchResult, weights: ScoringWeights) -> int | None:
    credit = {"matched": 1.0, "implied": weights.implied_credit, "missing": 0.0}
    possible = 0.0
    earned = 0.0
    for item in match.required:
        possible += weights.required_weight
        earned += weights.required_weight * credit[item.status]
    for item in match.preferred:
        possible += weights.preferred_weight
        earned += weights.preferred_weight * credit[item.status]
    if possible == 0:
        return None
    return _round(100 * earned / possible)


def compute_deterministic(
    jd_text: str,
    cv_text: str,
    format_report: dict[str, Any],
    weights: ScoringWeights | None = None,
    lexicon: Lexicon | None = None,
) -> DeterministicScore:
    weights = weights or ScoringWeights()
    lexicon = lexicon or load_lexicon()
    warnings = list(format_report.get("warnings", []))

    if not jd_text.strip():
        return DeterministicScore(
            scorable=False, reason="The job description is empty.",
            format_warnings=warnings, lexicon_version=lexicon.version,
        )
    if not format_report.get("text_ok", False):
        return DeterministicScore(
            scorable=False,
            reason="No text could be extracted from this CV (scanned or image-only file). "
                   "Upload a text-based PDF or DOCX to score it.",
            format_warnings=warnings, lexicon_version=lexicon.version,
        )

    sections = split_sections(jd_text)
    match = match_skills(sections.required_text, sections.preferred_text, cv_text, lexicon)
    fmt = format_score(format_report)
    kw = keyword_score(match, weights)

    notes: list[str] = []
    if sections.fallback:
        notes.append(
            "No requirements section was found in the JD, so the whole text was treated as "
            "requirements (lines marked as an advantage count as preferred)."
        )
    if sections.years_required:
        notes.append(f"The requirements mention {sections.years_required}+ years of experience.")

    if kw is None:
        return DeterministicScore(
            scorable=False,
            reason="No skills from the synonym list were found in the JD's requirements. "
                   "Add the missing terms to synonyms.yaml, or rely on the AI score.",
            format_score=fmt, match=match, sections=sections,
            format_warnings=warnings, notes=notes, lexicon_version=lexicon.version,
        )

    total = weights.keyword_share * kw + (1 - weights.keyword_share) * fmt
    return DeterministicScore(
        scorable=True,
        score=_round(total),
        keyword_score=kw,
        format_score=fmt,
        match=match,
        sections=sections,
        format_warnings=warnings,
        notes=notes,
        lexicon_version=lexicon.version,
    )
