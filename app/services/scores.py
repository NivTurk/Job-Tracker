"""Glue between stored applications/CVs and the scoring package.

A ScoreView bundles the three numbers shown everywhere: the deterministic
score (recomputed on every read), the AI score (read from the cache) and the
combined score (mixed at read time with the `ai_share` setting).
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Application, ApplicationCv
from app.scoring.combined import CombinedScore, combine
from app.scoring.deterministic import DeterministicScore, ScoringWeights, compute_deterministic
from app.services.ai_scores import AiConfig, AiView, ai_views
from app.services.settings import ai_share, scoring_weights


@dataclass
class ScoreView:
    deterministic: DeterministicScore
    ai: AiView
    combined: CombinedScore

    @property
    def deterministic_value(self) -> int | None:
        return self.deterministic.score if self.deterministic.scorable else None

    @property
    def ai_value(self) -> int | None:
        return self.ai.score if self.ai.status == "ok" else None

    @property
    def value(self) -> int | None:
        return self.combined.value


def score_version(application: Application, version: ApplicationCv, weights: ScoringWeights) -> DeterministicScore:
    cv_file = version.cv_file
    return compute_deterministic(application.jd_text, cv_file.text, cv_file.format_report, weights)


def score_views(
    session: Session, items: list[tuple[Application, ApplicationCv]], ai_config: AiConfig
) -> list[ScoreView]:
    weights = scoring_weights(session)
    share = ai_share(session)
    deterministic = [score_version(application, version, weights) for application, version in items]
    ai = ai_views(session, [(application, version.cv_file) for application, version in items], ai_config)
    views = []
    for det, ai_view in zip(deterministic, ai, strict=True):
        det_value = det.score if det.scorable else None
        ai_value = ai_view.score if ai_view.status == "ok" else None
        views.append(ScoreView(det, ai_view, combine(det_value, ai_value, share)))
    return views


def score_all_versions(session: Session, application: Application, ai_config: AiConfig) -> dict[int, ScoreView]:
    """Score every CV version of an application. Keyed by ApplicationCv.id."""
    versions = list(application.cv_versions)
    views = score_views(session, [(application, version) for version in versions], ai_config)
    return {version.id: view for version, view in zip(versions, views, strict=True)}
