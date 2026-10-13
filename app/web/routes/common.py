"""Helpers shared by route modules."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Config
from app.models import Application
from app.services.applications import get_application


def get_config_from(request: Request) -> Config:
    return request.app.state.config


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def htmx_target(request: Request) -> str | None:
    return request.headers.get("HX-Target")


def application_or_404(session: Session, application_id: int) -> Application:
    application = get_application(session, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return application
