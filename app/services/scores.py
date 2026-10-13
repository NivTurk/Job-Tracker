"""Glue between stored applications/CVs and the scoring package."""

from sqlalchemy.orm import Session

from app.models import Application, ApplicationCv
from app.scoring.deterministic import DeterministicScore, ScoringWeights, compute_deterministic
from app.services.settings import scoring_weights


def score_version(application: Application, version: ApplicationCv, weights: ScoringWeights) -> DeterministicScore:
    cv_file = version.cv_file
    return compute_deterministic(application.jd_text, cv_file.text, cv_file.format_report, weights)


def score_all_versions(session: Session, application: Application) -> dict[int, DeterministicScore]:
    """Score every CV version of an application. Keyed by ApplicationCv.id."""
    weights = scoring_weights(session)
    return {version.id: score_version(application, version, weights) for version in application.cv_versions}


def score_sent_version(application: Application, weights: ScoringWeights) -> DeterministicScore | None:
    version = application.sent_cv
    if version is None:
        return None
    return score_version(application, version, weights)
