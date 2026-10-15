"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.ai.client import AiClient, ClaudeClient
from app.config import PROJECT_ROOT, Config, get_config
from app.db import make_engine, make_session_factory
from app.services.auto_score import AutoScorer
from app.web.routes import applications, cvs, settings
from app.web.security import SameOriginMiddleware
from app.web.templating import templates

STATIC_DIR = Path(__file__).parent / "web" / "static"


def run_migrations(database_url: str) -> None:
    """Bring the database schema up to date (alembic upgrade head)."""
    alembic_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    alembic_config.attributes["skip_logging_config"] = True
    command.upgrade(alembic_config, "head")


def create_app(config: Config | None = None, migrate: bool = True, ai_client: AiClient | None = None) -> FastAPI:
    """ai_client: inject a fake in tests. By default one is built when ANTHROPIC_API_KEY is set."""
    config = config or get_config()
    config.ensure_dirs()
    if migrate:
        run_migrations(config.database_url)
    engine = make_engine(config.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # The scheduler and Telegram bot start here in phase 3.
        yield
        engine.dispose()

    app = FastAPI(title="Job tracker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.session_factory = make_session_factory(engine)
    # None means AI scoring is off: the tracker works with deterministic scores only.
    if ai_client is None and config.anthropic_api_key.strip():
        ai_client = ClaudeClient(config.anthropic_api_key.strip())
    app.state.ai_client = ai_client
    app.state.auto_scorer = AutoScorer(app.state.session_factory, ai_client) if ai_client else None

    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_host_list)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(applications.router)
    app.include_router(cvs.router)
    app.include_router(settings.router)

    # Starlette's HTTPException also covers unknown URLs, not only errors raised in routes.
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": f"Error {exc.status_code}", "message": exc.detail, "back_url": "/applications"},
            status_code=exc.status_code,
        )

    return app
