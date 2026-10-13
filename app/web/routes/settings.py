"""Settings page: search preferences and base CVs.

Follow-up thresholds and AI weights join this page in later phases.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.db import get_db
from app.models import Company
from app.scoring.normalize import normalize_company
from app.services.cv_store import CvTooLargeError, base_cvs, set_base_cv, store_cv
from app.services.extraction import ExtractionError, UnsupportedFileError
from app.services.settings import get_all_settings, set_setting
from app.web.forms import lines_to_list
from app.web.routes.common import get_config_from
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
