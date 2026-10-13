"""Application list, add/edit forms, detail page and stage changes."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.db import get_db
from app.models import Application, Stage
from app.services import pipeline
from app.services.applications import (
    ApplicationInput,
    ApplicationValidationError,
    ListFilters,
    archive_application,
    create_application,
    find_duplicates,
    list_applications,
    stage_counts,
    unarchive_application,
    update_application,
    validate_input,
)
from app.services.cv_store import CvTooLargeError, attach_cv, refresh_format_report, store_cv
from app.services.extraction import ExtractionError, UnsupportedFileError
from app.services.jd_fetch import fetch_jd
from app.services.scores import score_all_versions
from app.services.settings import scoring_weights
from app.timeutil import today_local
from app.web.forms import FormValueError, parse_date, parse_score
from app.web.routes.common import application_or_404, get_config_from, htmx_target, is_htmx
from app.web.templating import templates

router = APIRouter()

FORM_FIELDS = [
    "company", "title", "location", "source", "jd_url", "jd_text", "jd_fetch_status",
    "contact_name", "contact_details", "notes", "applied_on", "offer_deadline",
    "initial_stage", "cv_label",
]
SORT_KEYS = {"updated", "applied", "company", "score", "stage"}


# --- List ----------------------------------------------------------------------


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/applications", status_code=303)


@router.get("/applications", response_class=HTMLResponse)
def list_view(
    request: Request,
    db: Session = Depends(get_db),
    stage: str = "active",
    company: str = "",
    min_score: str = "",
    max_score: str = "",
    archived: str = "",
    sort: str = "updated",
    dir: str = "desc",
) -> Response:
    filters = ListFilters(
        stage=stage,
        company=company,
        min_score=parse_score(min_score),
        max_score=parse_score(max_score),
        show_archived=archived == "1",
        sort=sort if sort in SORT_KEYS else "updated",  # type: ignore[arg-type]
        descending=dir != "asc",
    )
    context = {
        "rows": list_applications(db, filters),
        "filters": filters,
        "counts": stage_counts(db),
        "pipeline_order": pipeline.PIPELINE_ORDER,
        "closed_stages": pipeline.CLOSED_STAGES,
    }
    template = "applications/list.html"
    if is_htmx(request) and htmx_target(request) == "app-table":
        template = "applications/_table.html"
    return templates.TemplateResponse(request, template, context)


# --- Add / edit ------------------------------------------------------------------


def _blank_form() -> dict[str, str]:
    values = {field: "" for field in FORM_FIELDS}
    values.update(source="company_site", initial_stage="applied", applied_on=today_local().isoformat(),
                  jd_fetch_status="manual")
    return values


def _form_values(form: FormData) -> dict[str, str]:
    return {field: str(form.get(field) or "") for field in FORM_FIELDS}


def _form_from_application(application: Application) -> dict[str, str]:
    values = _blank_form()
    values.update(
        company=application.company.name,
        title=application.title,
        location=application.location,
        source=application.source,
        jd_url=application.jd_url or "",
        jd_text=application.jd_text,
        jd_fetch_status=application.jd_fetch_status,
        contact_name=application.contact_name or "",
        contact_details=application.contact_details or "",
        notes=application.notes,
        applied_on=application.applied_on.isoformat() if application.applied_on else "",
        offer_deadline=application.offer_deadline.isoformat() if application.offer_deadline else "",
    )
    return values


def _render_form(
    request: Request,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    duplicates: list[Application] | None = None,
    application: Application | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "applications/form.html",
        {
            "form": values,
            "errors": errors or {},
            "duplicates": duplicates or [],
            "application": application,
        },
        status_code=status_code,
    )


async def _fill_jd_from_url(values: dict[str, str], errors: dict[str, str]) -> None:
    """If a URL was given without text, fetch it. On failure, ask for pasted text."""
    if values["jd_text"].strip():
        if not values["jd_fetch_status"]:
            values["jd_fetch_status"] = "manual"
        return
    if not values["jd_url"].strip():
        errors["jd_text"] = "Paste the job description, or give a link and press Fetch."
        return
    result = await run_in_threadpool(fetch_jd, values["jd_url"])
    if result.status == "fetched":
        values["jd_text"] = result.text
        values["jd_fetch_status"] = "fetched"
        for field, found in (("title", result.title), ("company", result.company), ("location", result.location)):
            if found and not values[field].strip():
                values[field] = found
    else:
        values["jd_fetch_status"] = "failed"
        errors["jd_text"] = f"{result.reason} The text is needed for scoring."


def _input_from_values(values: dict[str, str], errors: dict[str, str]) -> ApplicationInput:
    try:
        applied_on = parse_date(values["applied_on"], "Applied on")
    except FormValueError as exc:
        errors["applied_on"] = str(exc)
        applied_on = None
    try:
        offer_deadline = parse_date(values["offer_deadline"], "Offer deadline")
    except FormValueError as exc:
        errors["offer_deadline"] = str(exc)
        offer_deadline = None
    initial_stage = Stage.SAVED if values["initial_stage"] == "saved" else Stage.APPLIED
    return ApplicationInput(
        company=values["company"],
        title=values["title"],
        location=values["location"],
        source=values["source"] or "other",
        jd_url=values["jd_url"] or None,
        jd_text=values["jd_text"],
        jd_fetch_status=values["jd_fetch_status"] or "manual",
        contact_name=values["contact_name"],
        contact_details=values["contact_details"],
        notes=values["notes"],
        applied_on=applied_on,
        offer_deadline=offer_deadline,
        initial_stage=initial_stage,
    )


@router.get("/applications/new", response_class=HTMLResponse)
def new_form(request: Request) -> Response:
    return _render_form(request, _blank_form())


@router.post("/applications/fetch-jd", response_class=HTMLResponse)
async def fetch_jd_partial(request: Request) -> Response:
    """HTMX: fetch the URL and return the JD field with the text filled in, or an error."""
    form = await request.form()
    url = str(form.get("jd_url") or "").strip()
    context: dict[str, Any] = {"form": {"jd_text": str(form.get("jd_text") or ""), "jd_fetch_status": "manual"},
                               "errors": {}, "detected": None, "message": None}
    if not url:
        context["errors"]["jd_text"] = "Enter a link first."
    else:
        result = await run_in_threadpool(fetch_jd, url)
        if result.status == "fetched":
            context["form"] = {"jd_text": result.text, "jd_fetch_status": "fetched"}
            context["detected"] = {"title": result.title, "company": result.company, "location": result.location}
            context["message"] = f"Fetched {len(result.text):,} characters. Check the text before saving."
        else:
            context["form"]["jd_fetch_status"] = "failed"
            context["errors"]["jd_text"] = result.reason
    return templates.TemplateResponse(request, "applications/_jd_field.html", context)


@router.get("/applications/duplicates", response_class=HTMLResponse)
def duplicates_partial(
    request: Request,
    company: str = "",
    title: str = "",
    exclude: int | None = None,
    db: Session = Depends(get_db),
) -> Response:
    duplicates = find_duplicates(db, company, title, exclude_id=exclude)
    return templates.TemplateResponse(request, "applications/_duplicates.html", {"duplicates": duplicates})


@router.post("/applications", response_class=HTMLResponse)
async def create(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    values = _form_values(form)
    errors: dict[str, str] = {}
    await _fill_jd_from_url(values, errors)
    data = _input_from_values(values, errors)
    errors.update(validate_input(data))

    duplicates = find_duplicates(db, data.company, data.title)
    if duplicates and not form.get("confirm_duplicate"):
        errors["duplicate"] = "This looks like an application you already have. Tick the box to add it anyway."

    upload = form.get("cv_file")
    cv_upload = upload if isinstance(upload, UploadFile) and upload.filename else None
    if errors:
        if cv_upload is not None:
            errors["cv_file"] = "Select the CV file again (files are not kept when the form has errors)."
        return _render_form(request, values, errors, duplicates, status_code=422)

    config = get_config_from(request)
    cv_file = None
    if cv_upload is not None:
        try:
            cv_file, _created = store_cv(db, config.resolved_data_dir, cv_upload.filename or "cv",
                                         await cv_upload.read(), config.max_upload_bytes)
        except (UnsupportedFileError, ExtractionError, CvTooLargeError) as exc:
            db.rollback()
            errors["cv_file"] = str(exc)
            return _render_form(request, values, errors, duplicates, status_code=422)

    try:
        application = create_application(db, data)
    except ApplicationValidationError as exc:
        db.rollback()
        return _render_form(request, values, exc.errors, duplicates, status_code=422)
    if cv_file is not None:
        attach_cv(db, application, cv_file, values["cv_label"] or None)
    db.commit()
    return RedirectResponse(f"/applications/{application.id}", status_code=303)


@router.get("/applications/{application_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    return _render_form(request, _form_from_application(application), application=application)


@router.post("/applications/{application_id}/edit", response_class=HTMLResponse)
async def edit(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    form = await request.form()
    values = _form_values(form)
    errors: dict[str, str] = {}
    await _fill_jd_from_url(values, errors)
    data = _input_from_values(values, errors)
    errors.update(validate_input(data, creating=False))

    duplicates = find_duplicates(db, data.company, data.title, exclude_id=application.id)
    identity_changed = (data.company.strip(), data.title.strip()) != (application.company.name, application.title)
    if duplicates and identity_changed and not form.get("confirm_duplicate"):
        errors["duplicate"] = "Another application has the same company and a similar title. Tick the box to save anyway."
    if errors:
        return _render_form(request, values, errors, duplicates, application=application, status_code=422)
    try:
        update_application(db, application, data)
    except ApplicationValidationError as exc:
        db.rollback()
        return _render_form(request, values, exc.errors, duplicates, application=application, status_code=422)
    db.commit()
    return RedirectResponse(f"/applications/{application.id}", status_code=303)


@router.post("/applications/{application_id}/archive")
def archive(application_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    archive_application(application_or_404(db, application_id))
    db.commit()
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/unarchive")
def unarchive(application_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    unarchive_application(application_or_404(db, application_id))
    db.commit()
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


# --- Detail ------------------------------------------------------------------------


def _status_context(application: Application, error: str | None = None, oob: bool = False) -> dict[str, Any]:
    return {
        "oob": oob,  # also refresh the stage badge in the page header
        "application": application,
        "timeline": pipeline.timeline(application),
        "next_stages": pipeline.suggested_next_stages(application),
        "all_stages": pipeline.PIPELINE_ORDER + pipeline.CLOSED_STAGES,
        "can_undo": len(pipeline.active_stage_events(application)) > 1,
        "today": today_local(),
        "status_error": error,
    }


def _detail_context(request: Request, db: Session, application: Application, selected_cv: int | None) -> dict[str, Any]:
    config = get_config_from(request)
    changed = False
    for version in application.cv_versions:
        changed |= refresh_format_report(version.cv_file, config.resolved_data_dir)
    if changed:
        db.commit()

    scores = score_all_versions(db, application)
    versions = application.cv_versions
    selected = next((v for v in versions if v.id == selected_cv), None)
    selected = selected or application.sent_cv or (versions[-1] if versions else None)
    return {
        **_status_context(application),
        "scores": scores,
        "selected_version": selected,
        "selected_score": scores.get(selected.id) if selected else None,
        "duplicates": find_duplicates(db, application.company.name, application.title, exclude_id=application.id),
        "weights": scoring_weights(db),
    }


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def detail(request: Request, application_id: int, cv: int | None = None, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    return templates.TemplateResponse(request, "applications/detail.html",
                                      _detail_context(request, db, application, cv))


@router.get("/applications/{application_id}/score", response_class=HTMLResponse)
def score_partial(request: Request, application_id: int, cv: int | None = None, db: Session = Depends(get_db)) -> Response:
    """HTMX: the ATS breakdown for one CV version."""
    application = application_or_404(db, application_id)
    return templates.TemplateResponse(request, "applications/_score.html",
                                      _detail_context(request, db, application, cv))


def _status_response(request: Request, application_id: int, application: Application, error: str | None) -> Response:
    if is_htmx(request):
        return templates.TemplateResponse(request, "applications/_status.html",
                                          _status_context(application, error, oob=True),
                                          status_code=422 if error else 200)
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/stage", response_class=HTMLResponse)
async def change_stage(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    form = await request.form()
    error = None
    raw_stage = str(form.get("to_stage") or "")
    if raw_stage not in {stage.value for stage in Stage}:
        return _status_response(request, application_id, application, "Choose a stage.")
    try:
        to_stage = Stage(raw_stage)
        occurred_on = parse_date(str(form.get("occurred_on") or ""), "Date") or today_local()
        expected = parse_date(str(form.get("expected_answer_by") or ""), "Expected answer by")
        pipeline.change_stage(db, application, to_stage, occurred_on, str(form.get("note") or ""), expected)
        db.commit()
    except (pipeline.StageChangeError, FormValueError) as exc:
        db.rollback()
        error = str(exc)
    return _status_response(request, application_id, application, error)


@router.post("/applications/{application_id}/stage/undo", response_class=HTMLResponse)
def undo_stage(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    error = None
    try:
        pipeline.undo_last_stage_change(db, application)
        db.commit()
    except pipeline.StageChangeError as exc:
        db.rollback()
        error = str(exc)
    return _status_response(request, application_id, application, error)


@router.post("/applications/{application_id}/expected", response_class=HTMLResponse)
async def set_expected(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    form = await request.form()
    error = None
    try:
        value = parse_date(str(form.get("expected_answer_by") or ""), "Expected answer by")
        if value is not None and value < application.stage_entered_on:
            raise FormValueError("\"Expected answer by\" can't be before the stage started.")
        pipeline.set_expected_answer_by(application, value)
        db.commit()
    except FormValueError as exc:
        db.rollback()
        error = str(exc)
    return _status_response(request, application_id, application, error)


@router.post("/applications/{application_id}/notes", response_class=HTMLResponse)
async def add_note(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    form = await request.form()
    error = None
    try:
        pipeline.add_note(db, application, str(form.get("note") or ""))
        db.commit()
    except pipeline.StageChangeError as exc:
        db.rollback()
        error = str(exc)
    return _status_response(request, application_id, application, error)
