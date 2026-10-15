from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class AiCall(Base):
    """One request to the Claude API, successful or not. Used for cost tracking and debugging."""

    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="score")
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(default=1)
    # ok, invalid_json, invalid_schema, out_of_range, refusal, max_tokens, api_error, timeout
    outcome: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cache_read_tokens: Mapped[int | None]
    cache_write_tokens: Mapped[int | None]
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6, asdecimal=True))
    latency_ms: Mapped[int] = mapped_column(default=0)
    request_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)


class AiScore(Base):
    """Cached AI result for one (CV text, JD text, model, prompt version) combination.

    Keyed by content, not by application: the same CV and JD in two
    applications share one row. No row means "AI pending".
    """

    __tablename__ = "ai_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # "ok" or "invalid"
    score: Mapped[int | None]
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    ai_call: Mapped[AiCall | None] = relationship()
