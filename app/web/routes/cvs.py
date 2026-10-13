"""CV upload, versions, download."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.db import get_db
from app.models import ApplicationCv, CvFile
from app.services.cv_store import CvTooLargeError, attach_cv, mark_sent, store_cv
from app.services.extraction import ExtractionError, UnsupportedFileError
from app.web.routes.common import application_or_404, get_config_from
from app.web.templating import templates

router = APIRouter()


@router.post("/applications/{application_id}/cvs", response_class=HTMLResponse)
async def upload_cv(request: Request, application_id: int, db: Session = Depends(get_db)) -> Response:
    application = application_or_404(db, application_id)
    form = await request.form()
    upload = form.get("cv_file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return _error(request, application_id, "Choose a PDF or DOCX file to upload.")
    config = get_config_from(request)
    try:
        cv_file, _created = store_cv(db, config.resolved_data_dir, upload.filename,
                                     await upload.read(), config.max_upload_bytes)
    except (UnsupportedFileError, ExtractionError, CvTooLargeError) as exc:
        db.rollback()
        return _error(request, application_id, str(exc))
    version = attach_cv(db, application, cv_file, str(form.get("cv_label") or "").strip() or None)
    db.commit()
    return RedirectResponse(f"/applications/{application_id}?cv={version.id}#cvs", status_code=303)


@router.post("/applications/{application_id}/cvs/{version_id}/sent")
def mark_version_sent(application_id: int, version_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    version = db.get(ApplicationCv, version_id)
    if version is None or version.application_id != application_id:
        raise HTTPException(status_code=404, detail="CV version not found.")
    mark_sent(db, version)
    db.commit()
    return RedirectResponse(f"/applications/{application_id}?cv={version_id}#cvs", status_code=303)


@router.get("/cvs/{cv_id}/download")
def download_cv(request: Request, cv_id: int, db: Session = Depends(get_db)) -> FileResponse:
    cv_file = db.get(CvFile, cv_id)
    if cv_file is None:
        raise HTTPException(status_code=404, detail="CV not found.")
    data_dir = get_config_from(request).resolved_data_dir.resolve()
    path = (data_dir / cv_file.stored_path).resolve()
    if not path.is_relative_to(data_dir) or not path.exists():
        raise HTTPException(status_code=404, detail="The CV file is missing from the data folder.")
    return FileResponse(path, media_type=cv_file.mime_type, filename=cv_file.original_name)


def _error(request: Request, application_id: int, message: str) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Upload failed", "message": message, "back_url": f"/applications/{application_id}#cvs"},
        status_code=422,
    )
