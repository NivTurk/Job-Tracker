"""Creating, editing, archiving, listing and duplicate-checking applications."""

import hashlib
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Application, ApplicationCv, Company, Stage
from app.scoring.normalize import normalize_company, normalize_text, normalize_title
from app.services import pipeline
from app.services.ai_scores import AiConfig
from app.services.scores import ScoreView, score_views
from app.services.settings import get_setting
from app.timeutil import today_local, utcnow

SOURCES: dict[str, str] = {
    "linkedin": "LinkedIn",
    "company_site": "Company site",
    "referral": "Referral",
    "recruiter": "Recruiter",
    "job_board": "Job board (Drushim, AllJobs...)",
    "search": "In-app job search",
    "other": "Other",
}

# Titles at least this similar (difflib ratio on normalized titles) at the
# same company trigger a duplicate warning.
TITLE_SIMILARITY_THRESHOLD = 0.8


class ApplicationValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass
class ApplicationInput:
    company: str
    title: str
    location: str = ""
    source: str = "other"
    jd_url: str | None = None
    jd_text: str = ""
    jd_fetch_status: str = "manual"
    contact_name: str | None = None
    contact_details: str | None = None
    notes: str = ""
    applied_on: date | None = None
    offer_deadline: date | None = None
    initial_stage: Stage = Stage.APPLIED  # only used on create


def validate_input(data: ApplicationInput, creating: bool = True) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.company.strip():
        errors["company"] = "Company is required."
    if not data.title.strip():
        errors["title"] = "Job title is required."
    if data.source not in SOURCES:
        errors["source"] = "Unknown source."
    if data.jd_url:
        parsed = urlparse(data.jd_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors["jd_url"] = "The link must start with http:// or https://."
    if data.applied_on and data.applied_on > today_local():
        errors["applied_on"] = "The applied date can't be in the future."
    if creating and data.initial_stage not in (Stage.SAVED, Stage.APPLIED):
        errors["initial_stage"] = "New applications start as Saved or Applied."
    return errors


def jd_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def get_or_create_company(session: Session, name: str) -> Company:
    norm = normalize_company(name)
    company = session.scalar(select(Company).where(Company.name_norm == norm))
    if company is not None:
        return company
    targets = {normalize_company(target) for target in get_setting(session, "target_companies")}
    company = Company(name=name.strip(), name_norm=norm, is_target=norm in targets)
    session.add(company)
    session.flush()
    return company


def titles_similar(a: str, b: str) -> bool:
    a_norm = normalize_title(a)
    b_norm = normalize_title(b)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm or a_norm in b_norm or b_norm in a_norm:
        return True
    return SequenceMatcher(None, a_norm, b_norm).ratio() >= TITLE_SIMILARITY_THRESHOLD


def find_duplicates(
    session: Session, company_name: str, title: str, exclude_id: int | None = None
) -> list[Application]:
    """Applications at the same company with a similar title (archived ones included)."""
    if not company_name.strip() or not title.strip():
        return []
    company = session.scalar(select(Company).where(Company.name_norm == normalize_company(company_name)))
    if company is None:
        return []
    return [
        application
        for application in company.applications
        if application.id != exclude_id and titles_similar(application.title, title)
    ]


def _apply_input(application: Application, data: ApplicationInput) -> None:
    application.title = data.title.strip()
    application.title_norm = normalize_title(data.title)
    application.location = data.location.strip()
    application.source = data.source
    application.jd_url = (data.jd_url or "").strip() or None
    application.jd_text = data.jd_text.strip()
    application.jd_hash = jd_hash(application.jd_text)
    application.jd_fetch_status = data.jd_fetch_status
    application.contact_name = (data.contact_name or "").strip() or None
    application.contact_details = (data.contact_details or "").strip() or None
    application.notes = data.notes.strip()
    application.applied_on = data.applied_on
    application.offer_deadline = data.offer_deadline


def create_application(session: Session, data: ApplicationInput) -> Application:
    errors = validate_input(data)
    if errors:
        raise ApplicationValidationError(errors)
    company = get_or_create_company(session, data.company)
    application = Application(company=company)
    _apply_input(application, data)

    if data.initial_stage == Stage.APPLIED:
        application.applied_on = data.applied_on or today_local()
        pipeline.record_initial_stage(application, Stage.APPLIED, application.applied_on)
    else:
        application.applied_on = None
        pipeline.record_initial_stage(application, Stage.SAVED, today_local())

    session.add(application)
    session.flush()
    return application


def update_application(session: Session, application: Application, data: ApplicationInput) -> None:
    errors = validate_input(data, creating=False)
    if errors:
        raise ApplicationValidationError(errors)
    if normalize_company(data.company) != application.company.name_norm:
        application.company = get_or_create_company(session, data.company)
    _apply_input(application, data)
    session.flush()


def archive_application(application: Application) -> None:
    application.archived_at = application.archived_at or utcnow()


def unarchive_application(application: Application) -> None:
    application.archived_at = None


def get_application(session: Session, application_id: int) -> Application | None:
    return session.scalar(
        select(Application)
        .where(Application.id == application_id)
        .options(
            selectinload(Application.company),
            selectinload(Application.events),
            selectinload(Application.cv_versions).selectinload(ApplicationCv.cv_file),
        )
    )


# --- List view ---------------------------------------------------------------

SortKey = Literal["updated", "applied", "company", "score", "stage"]
StageFilter = str  # "active", "closed", "all", or a Stage value


@dataclass
class ListFilters:
    stage: StageFilter = "active"
    company: str = ""
    min_score: int | None = None
    max_score: int | None = None
    show_archived: bool = False
    sort: SortKey = "updated"
    descending: bool = True


@dataclass
class ApplicationRow:
    application: Application
    score: ScoreView | None  # for the sent CV version
    cv_count: int = 0

    @property
    def score_value(self) -> int | None:
        """The combined score, used for filtering and sorting."""
        return self.score.value if self.score else None


_STAGE_SORT_ORDER = {stage: index for index, stage in enumerate(pipeline.PIPELINE_ORDER + pipeline.CLOSED_STAGES)}


def list_applications(session: Session, filters: ListFilters, ai_config: AiConfig | None = None) -> list[ApplicationRow]:
    query = select(Application).options(
        selectinload(Application.company),
        selectinload(Application.cv_versions).selectinload(ApplicationCv.cv_file),
    )
    if not filters.show_archived:
        query = query.where(Application.archived_at.is_(None))
    if filters.stage == "active":
        query = query.where(Application.stage.not_in(pipeline.CLOSED_STAGES))
    elif filters.stage == "closed":
        query = query.where(Application.stage.in_(pipeline.CLOSED_STAGES))
    elif filters.stage in {stage.value for stage in Stage}:
        query = query.where(Application.stage == filters.stage)
    if filters.company.strip():
        query = query.join(Company).where(Company.name_norm.contains(normalize_company(filters.company)))

    applications = list(session.scalars(query))
    sent = [(application, application.sent_cv) for application in applications if application.sent_cv is not None]
    views = score_views(session, sent, ai_config or AiConfig())  # type: ignore[arg-type]
    by_application = {application.id: view for (application, _), view in zip(sent, views, strict=True)}
    rows = [
        ApplicationRow(
            application=application,
            score=by_application.get(application.id),
            cv_count=len(application.cv_versions),
        )
        for application in applications
    ]

    if filters.min_score is not None or filters.max_score is not None:
        low = filters.min_score if filters.min_score is not None else 0
        high = filters.max_score if filters.max_score is not None else 100
        rows = [row for row in rows if row.score_value is not None and low <= row.score_value <= high]

    _sort_rows(rows, filters)
    return rows


def _sort_rows(rows: list[ApplicationRow], filters: ListFilters) -> None:
    if filters.sort == "score":
        # Unscored rows always go last, whatever the direction.
        scored = [row for row in rows if row.score_value is not None]
        unscored = [row for row in rows if row.score_value is None]
        scored.sort(key=lambda row: row.score_value or 0, reverse=filters.descending)
        rows[:] = scored + unscored
        return
    if filters.sort == "company":
        rows.sort(key=lambda row: (row.application.company.name.lower(), row.application.title.lower()),
                  reverse=filters.descending)
    elif filters.sort == "applied":
        rows.sort(key=lambda row: row.application.applied_on or date.min, reverse=filters.descending)
    elif filters.sort == "stage":
        rows.sort(key=lambda row: _STAGE_SORT_ORDER.get(row.application.stage, 99), reverse=filters.descending)
    else:
        rows.sort(key=lambda row: row.application.updated_at, reverse=filters.descending)


def stage_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for stage in session.scalars(select(Application.stage).where(Application.archived_at.is_(None))):
        counts[stage] = counts.get(stage, 0) + 1
    return counts
