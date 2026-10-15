"""User settings: defaults in code, overrides in the `settings` table.

A row exists only for keys the user changed, so changing a default in code
takes effect for everyone who never touched that setting.
"""

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting
from app.scoring.deterministic import ScoringWeights

DEFAULTS: dict[str, Any] = {
    "target_companies": [
        "NVIDIA", "Check Point", "Upwind", "Wiz", "Stratasys", "AT&T",
        "Mobileye", "VAST Data", "Microsoft Israel",
    ],
    "preferred_locations": ["Tel Aviv", "Herzliya", "Ra'anana", "Rehovot", "Yokneam"],
    "role_keywords": [
        "embedded", "firmware", "systems", "low level", "linux", "networking",
        "security", "c++", "kernel",
    ],
    "seniority": ["student", "junior", "entry-level"],
    "scoring": {
        "required_weight": 3.0,
        "preferred_weight": 1.0,
        "implied_credit": 0.75,
        "keyword_share": 0.8,
        # Share of the AI score in the combined score (0 = deterministic only, 1 = AI only).
        "ai_share": 0.5,
    },
    "ai": {
        # Score with AI in the background when a CV is attached or the JD text changes.
        "auto_score": True,
    },
}


def get_setting(session: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting {key!r}")
    row = session.get(Setting, key)
    if row is None:
        return deepcopy(DEFAULTS[key])
    return row.value


def get_all_settings(session: Session) -> dict[str, Any]:
    values = deepcopy(DEFAULTS)
    for row in session.scalars(select(Setting)):
        if row.key in values:
            values[row.key] = row.value
    return values


def set_setting(session: Session, key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting {key!r}")
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def reset_setting(session: Session, key: str) -> None:
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)


def scoring_weights(session: Session) -> ScoringWeights:
    values = {**DEFAULTS["scoring"], **get_setting(session, "scoring")}
    return ScoringWeights(
        required_weight=float(values["required_weight"]),
        preferred_weight=float(values["preferred_weight"]),
        implied_credit=float(values["implied_credit"]),
        keyword_share=float(values["keyword_share"]),
    )


def ai_share(session: Session) -> float:
    """Weight of the AI score in the combined score, clamped to 0-1."""
    values = {**DEFAULTS["scoring"], **get_setting(session, "scoring")}
    return min(1.0, max(0.0, float(values["ai_share"])))


def ai_auto_score(session: Session) -> bool:
    values = {**DEFAULTS["ai"], **get_setting(session, "ai")}
    return bool(values["auto_score"])
