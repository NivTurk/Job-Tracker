"""Settings page: search preferences, base CVs, score weights and AI usage.

Follow-up thresholds join this page in phase 3.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.db import get_db
from app.models import AiCall, Company
from app.scoring.normalize import normalize_company
from app.services.cv_store import CvTooLargeError, base_cvs, set_base_cv, store_cv
from app.services.extraction import ExtractionError, UnsupportedFileError
from app.services.ai_scores import usage_since
from app.services.settings import ai_auto_score, ai_share, get_all_settings, get_setting, set_setting
from app.timeutil import utcnow
from app.web.forms import lines_to_list
from app.web.routes.common import ai_config_from, get_config_from
from app.web.templating import templates

router = APIRouter()

BASE_ROLES = {"systems": "Systems / low-level", "netsec": "Network / security"}
LIST_SETTINGS = ["target_companies", "preferred_locations", "role_keywords", "seniority"]


def _render(request: Request, db: Session, message: str | None = None, error: str | None = None,
            status_code: int = 200) -> Response:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": get_all_settings(db),
            "base_cvs": base_cvs(db),
            "base_roles": BASE_ROLES,
            "message": message,
            "error": error,
            "ai_share_percent": round(ai_share(db) * 100),
            "ai_auto_score": ai_auto_score(db),
            "ai": ai_config_from(request),
            "usage_30d": usage_since(db, utcnow() - timedelta(days=30)),
            "usage_all": usage_since(db),
            "recent_calls": list(db.scalars(select(AiCall).order_by(AiCall.id.desc()).limit(10))),
        },
        status_code=status_code,
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), saved: str = "") -> Response:
    return _render(request, db, message="Settings saved." if saved else None)


@router.post("/settings")
async def save_settings(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    for key in LIST_SETTINGS:
        set_setting(db, key, lines_to_list(str(form.get(key) or "")))
    # Keep the companies table's target flag in line with the list.
    targets = {normalize_company(name) for name in lines_to_list(str(form.get("target_companies") or ""))}
    for company in db.scalars(select(Company)):
        company.is_target = company.name_norm in targets
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/base-cv", response_class=HTMLResponse)
async def upload_base_cv(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    role = str(form.get("role") or "")
    upload = form.get("cv_file")
    if role not in BASE_ROLES:
        return _render(request, db, error="Unknown base CV type.", status_code=422)
    if not isinstance(upload, UploadFile) or not upload.filename:
        return _render(request, db, error="Choose a PDF or DOCX file.", status_code=422)
    config = get_config_from(request)
    try:
        cv_file, _created = store_cv(db, config.resolved_data_dir, upload.filename,
                                     await upload.read(), config.max_upload_bytes)
    except (UnsupportedFileError, ExtractionError, CvTooLargeError) as exc:
        db.rollback()
        return _render(request, db, error=str(exc), status_code=422)
    set_base_cv(db, role, cv_file)
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/scoring", response_class=HTMLResponse)
async def save_scoring(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    raw = str(form.get("ai_share_percent") or "").strip()
    try:
        percent = int(raw)
    except ValueError:
        percent = -1
    if not 0 <= percent <= 100:
        return _render(request, db, error="The AI share must be a whole number from 0 to 100.", status_code=422)
    scoring = dict(get_setting(db, "scoring"))
    scoring["ai_share"] = percent / 100
    set_setting(db, "scoring", scoring)
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/ai")
async def save_ai(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    values = dict(get_setting(db, "ai"))
    values["auto_score"] = form.get("auto_score") == "1"
    set_setting(db, "ai", values)
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
