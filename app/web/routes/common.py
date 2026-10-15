"""Helpers shared by route modules."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.ai.client import MODEL, AiClient
from app.config import Config
from app.models import Application
from app.services.ai_scores import AiConfig
from app.services.applications import get_application
from app.services.auto_score import AutoScorer


def get_config_from(request: Request) -> Config:
    return request.app.state.config


def get_ai_client(request: Request) -> AiClient | None:
    return getattr(request.app.state, "ai_client", None)


def get_auto_scorer(request: Request) -> AutoScorer | None:
    return getattr(request.app.state, "auto_scorer", None)


def ai_config_from(request: Request) -> AiConfig:
    client = get_ai_client(request)
    if client is None:
        return AiConfig(model=MODEL, enabled=False)
    scorer = get_auto_scorer(request)
    running = scorer.running_keys() if scorer else frozenset()
    return AiConfig(model=client.model, enabled=True, running=running)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def htmx_target(request: Request) -> str | None:
    return request.headers.get("HX-Target")


def application_or_404(session: Session, application_id: int) -> Application:
    application = get_application(session, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return application
