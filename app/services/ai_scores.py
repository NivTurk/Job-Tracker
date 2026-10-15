"""AI half of the ATS score: run, cache, log and look up.

Flow for one score (run_score):
1. Call the API with the JD and CV text.
2. Validate the reply. If it is malformed or out of range, retry once:
   a cut-off or refused reply is re-sent as is (with more tokens if cut off),
   anything else is sent back with the validation error.
3. A valid reply is stored in ai_scores (status "ok"). Two invalid replies are
   stored as status "invalid" with an error note.
4. If the API is unreachable, nothing is stored: the score stays "pending"
   and the deterministic score is shown alone. The failed call is logged.

Every API attempt becomes one ai_calls row with tokens and estimated cost.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.ai.client import MAX_TOKENS, MODEL, RETRY_MAX_TOKENS, AiApiError, AiClient, AiResponse, estimate_cost
from app.ai.prompt import (
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    AiResult,
    ValidationFailure,
    build_user_message,
    correction_message,
    validate_output,
)
from app.models import AiCall, AiScore, Application, CvFile
from app.timeutil import utcnow

AiStatus = Literal["ok", "invalid", "pending", "unavailable"]
API_FAILURES = ("api_error", "timeout")


@dataclass(frozen=True)
class AiConfig:
    """What the read side needs to know: which model's cache to look in, and whether scoring can run."""

    model: str = MODEL
    enabled: bool = False
    # Cache keys a background job is scoring right now (see app/services/auto_score.py).
    running: frozenset[str] = frozenset()


@dataclass
class AiView:
    status: AiStatus
    score: int | None = None
    result: AiResult | None = None
    # Why there is no score: the error note for "invalid", the reason for "pending" / "unavailable".
    message: str | None = None
    scored_at: datetime | None = None
    model: str | None = None
    # The newest failed attempt after the stored result (or with none stored).
    last_failure: str | None = None
    last_failure_at: datetime | None = None
    can_run: bool = False
    # A background job is scoring this pair right now.
    running: bool = False

    @property
    def has_result(self) -> bool:
        return self.status in ("ok", "invalid")


def cache_key(cv_text: str, jd_text: str, model: str, prompt_version: str = PROMPT_VERSION) -> str:
    payload = "\x00".join((cv_text, jd_text, model, prompt_version))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def input_problem(application: Application, cv_file: CvFile) -> str | None:
    """Reason the AI score can't be computed at all, or None."""
    if not application.jd_text.strip():
        return "The job description is empty."
    if not cv_file.text_ok or not cv_file.text.strip():
        return "No text could be extracted from this CV."
    return None


# --- Read side --------------------------------------------------------------------


def _latest_failures(session: Session, keys: list[str]) -> dict[str, AiCall]:
    if not keys:
        return {}
    newest = (
        select(AiCall.cache_key, func.max(AiCall.id).label("max_id"))
        .where(AiCall.cache_key.in_(keys), AiCall.outcome != "ok")
        .group_by(AiCall.cache_key)
        .subquery()
    )
    calls = session.scalars(select(AiCall).join(newest, AiCall.id == newest.c.max_id))
    return {call.cache_key: call for call in calls if call.cache_key}


def _view(row: AiScore | None, failure: AiCall | None, config: AiConfig, key: str) -> AiView:
    if failure is not None and row is not None and failure.created_at < row.created_at:
        failure = None
    failure_text = None
    if failure is not None:
        failure_text = failure.error if failure.outcome in API_FAILURES \
            else f"The AI reply failed validation ({failure.error})."
    common: dict[str, Any] = {
        "can_run": config.enabled,
        "last_failure": failure_text,
        "last_failure_at": failure.created_at if failure else None,
    }
    if row is not None and row.status == "ok" and row.result_json is not None:
        return AiView(
            status="ok", score=row.score, result=AiResult.model_validate(row.result_json),
            scored_at=row.created_at, model=row.model, **common,
        )
    if row is not None:
        return AiView(status="invalid", message=row.error, scored_at=row.created_at, model=row.model, **common)
    if key in config.running:
        return AiView(status="pending", message="Scoring in the background, usually 5-20 seconds.",
                      running=True, **{**common, "can_run": False})
    if not config.enabled:
        message = "AI scoring is off. Add ANTHROPIC_API_KEY to .env and restart the app to turn it on."
    elif failure is not None:
        message = "The last attempt failed. The deterministic score is shown alone until a re-score succeeds."
    else:
        message = "Not scored by AI yet."
    return AiView(status="pending", message=message, **common)


def ai_views(
    session: Session, pairs: list[tuple[Application, CvFile]], config: AiConfig
) -> list[AiView]:
    """Look up the cached AI result for each (application, CV) pair, in two queries."""
    keys: list[str | None] = []
    views: list[AiView | None] = []
    for application, cv_file in pairs:
        problem = input_problem(application, cv_file)
        if problem:
            keys.append(None)
            views.append(AiView(status="unavailable", message=problem))
        else:
            keys.append(cache_key(cv_file.text, application.jd_text, config.model))
            views.append(None)

    wanted = sorted({key for key in keys if key})
    rows = {row.cache_key: row for row in session.scalars(select(AiScore).where(AiScore.cache_key.in_(wanted)))} \
        if wanted else {}
    failures = _latest_failures(session, wanted)
    return [
        view if view is not None else _view(rows.get(key), failures.get(key), config, key)  # type: ignore[arg-type]
        for view, key in zip(views, keys, strict=True)
    ]


def ai_view(session: Session, application: Application, cv_file: CvFile, config: AiConfig) -> AiView:
    return ai_views(session, [(application, cv_file)], config)[0]


# --- Write side -------------------------------------------------------------------


def _log_call(
    session: Session,
    *,
    application: Application,
    key: str,
    model: str,
    attempt: int,
    outcome: str,
    response: AiResponse | None = None,
    error: str | None = None,
    latency_ms: int = 0,
    request_id: str | None = None,
) -> AiCall:
    call = AiCall(
        purpose="score",
        application_id=application.id,
        cache_key=key,
        model=model,
        prompt_version=PROMPT_VERSION,
        attempt=attempt,
        outcome=outcome,
        error=error,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    if response is not None:
        call.input_tokens = response.input_tokens
        call.output_tokens = response.output_tokens
        call.cache_read_tokens = response.cache_read_tokens
        call.cache_write_tokens = response.cache_write_tokens
        call.est_cost_usd = estimate_cost(
            model, response.input_tokens, response.output_tokens,
            response.cache_read_tokens, response.cache_write_tokens,
        )
        call.latency_ms = response.latency_ms
        call.request_id = response.request_id
    session.add(call)
    session.flush()
    return call


def _store(session: Session, key: str, model: str, call: AiCall, **fields: Any) -> AiScore:
    row = session.scalar(select(AiScore).where(AiScore.cache_key == key))
    if row is None:
        row = AiScore(cache_key=key)
        session.add(row)
    row.model = model
    row.prompt_version = PROMPT_VERSION
    row.status = fields["status"]
    row.score = fields.get("score")
    row.result_json = fields.get("result_json")
    row.error = fields.get("error")
    row.ai_call_id = call.id
    row.created_at = utcnow()
    session.flush()
    return row


def run_score(session: Session, client: AiClient, application: Application, cv_file: CvFile) -> AiView:
    """Score one CV against one application's JD with a fresh API call.

    Always calls the API, even if a cached result exists (that's what the
    re-score button is for). The caller commits.
    """
    config = AiConfig(model=client.model, enabled=True)
    problem = input_problem(application, cv_file)
    if problem:
        return AiView(status="unavailable", message=problem)

    model = client.model
    key = cache_key(cv_file.text, application.jd_text, model)
    first_message = {"role": "user", "content": build_user_message(application.jd_text, cv_file.text)}
    messages: list[dict[str, Any]] = [first_message]
    max_tokens = MAX_TOKENS

    for attempt in (1, 2):
        try:
            response = client.complete(SYSTEM_PROMPT, messages, RESPONSE_SCHEMA, max_tokens)
        except AiApiError as exc:
            _log_call(session, application=application, key=key, model=model, attempt=attempt,
                      outcome=exc.outcome, error=exc.message, latency_ms=exc.latency_ms,
                      request_id=exc.request_id)
            return ai_view(session, application, cv_file, config)

        try:
            result = validate_output(response.text, response.stop_reason)
        except ValidationFailure as failure:
            call = _log_call(session, application=application, key=key, model=model, attempt=attempt,
                             outcome=failure.outcome, response=response, error=failure.reason)
            if attempt == 2:
                existing = session.scalar(select(AiScore).where(AiScore.cache_key == key))
                # Keep an earlier good result; the failed re-score shows as last_failure.
                if existing is None or existing.status != "ok":
                    _store(session, key, model, call, status="invalid",
                           error=f"The AI reply failed validation twice (last problem: {failure.reason}).")
                return ai_view(session, application, cv_file, config)
            if failure.outcome == "max_tokens":
                max_tokens = RETRY_MAX_TOKENS
                messages = [first_message]
            elif failure.outcome == "refusal" or not response.text.strip():
                messages = [first_message]
            else:
                messages = [
                    first_message,
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": correction_message(failure.reason)},
                ]
            continue

        call = _log_call(session, application=application, key=key, model=model, attempt=attempt,
                         outcome="ok", response=response)
        _store(session, key, model, call, status="ok", score=result.score, result_json=result.model_dump())
        return ai_view(session, application, cv_file, config)

    raise AssertionError("unreachable")  # pragma: no cover


# --- Usage --------------------------------------------------------------------------


@dataclass
class UsageSummary:
    calls: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def usage_since(session: Session, since: datetime | None = None) -> UsageSummary:
    query = select(
        func.count(AiCall.id),
        func.sum(case((AiCall.outcome != "ok", 1), else_=0)),
        func.coalesce(func.sum(AiCall.input_tokens), 0),
        func.coalesce(func.sum(AiCall.output_tokens), 0),
        func.coalesce(func.sum(AiCall.est_cost_usd), 0),
    )
    if since is not None:
        query = query.where(AiCall.created_at >= since)
    calls, failed, tokens_in, tokens_out, cost = session.execute(query).one()
    return UsageSummary(int(calls or 0), int(failed or 0), int(tokens_in), int(tokens_out), float(cost or 0))
