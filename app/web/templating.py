"""Jinja2 environment and template filters."""

from datetime import date, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.services.applications import SOURCES
from app.services.pipeline import CLOSED_STAGES, stage_label
from app.timeutil import to_local

TEMPLATE_DIR = Path(__file__).with_name("templates")

templates = Jinja2Templates(directory=TEMPLATE_DIR)


def fmt_date(value: date | None) -> str:
    if value is None:
        return ""
    return f"{value.day} {value:%b %Y}"


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    local = to_local(value)
    return f"{local.day} {local:%b %Y, %H:%M}"


def score_class(score: int | None) -> str:
    if score is None:
        return "score-none"
    if score >= 75:
        return "score-high"
    if score >= 50:
        return "score-mid"
    return "score-low"


def stage_class(stage: str) -> str:
    if stage in CLOSED_STAGES:
        return f"stage stage-closed stage-{stage}"
    return f"stage stage-{stage}"


def file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


templates.env.filters.update(
    fmt_date=fmt_date,
    fmt_datetime=fmt_datetime,
    score_class=score_class,
    stage_class=stage_class,
    file_size=file_size,
)
templates.env.globals.update(
    stage_label=stage_label,
    sources=SOURCES,
)
