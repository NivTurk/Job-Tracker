"""Combined ATS score: deterministic and AI halves, mixed by a configurable share.

Computed at read time from the two sub-scores, never stored, so changing the
weight in settings updates every application at once.
"""

from dataclasses import dataclass
from typing import Literal

from app.scoring.deterministic import _round

Basis = Literal["both", "deterministic", "ai", "none"]


@dataclass(frozen=True)
class CombinedScore:
    value: int | None
    basis: Basis

    @property
    def label(self) -> str:
        return {
            "both": "combined",
            "deterministic": "deterministic only",
            "ai": "AI only",
            "none": "not scored",
        }[self.basis]


def combine(deterministic: int | None, ai: int | None, ai_share: float) -> CombinedScore:
    """Mix the sub-scores. A missing half falls back to the other one.

    ai_share is the AI weight (0-1). With both halves present, 0 means the
    deterministic score alone and 1 means the AI score alone.
    """
    ai_share = min(1.0, max(0.0, ai_share))
    if deterministic is not None and ai is not None:
        if ai_share == 0:
            return CombinedScore(deterministic, "deterministic")
        if ai_share == 1:
            return CombinedScore(ai, "ai")
        return CombinedScore(_round((1 - ai_share) * deterministic + ai_share * ai), "both")
    if deterministic is not None:
        return CombinedScore(deterministic, "deterministic")
    if ai is not None:
        return CombinedScore(ai, "ai")
    return CombinedScore(None, "none")
