"""Application stage pipeline: transitions, interview rounds, timeline and undo.

The application's `stage`, `stage_round`, `stage_entered_on` and
`expected_answer_by` columns are always derived from the latest stage_change
event that has not been undone (see `recompute_stage`). Undo marks an event
as undone rather than deleting it.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.models import Application, ApplicationEvent, EventKind, Stage
from app.timeutil import today_local, utcnow

STAGE_LABELS: dict[str, str] = {
    Stage.SAVED: "Saved",
    Stage.APPLIED: "Applied",
    Stage.HR_SCREEN: "HR screen",
    Stage.TECHNICAL: "Technical interview",
    Stage.HOME_ASSIGNMENT: "Home assignment",
    Stage.FINAL: "Final interview",
    Stage.OFFER: "Offer",
    Stage.ACCEPTED: "Accepted",
    Stage.REJECTED: "Rejected",
    Stage.WITHDRAWN: "Withdrawn",
    Stage.GHOSTED: "Ghosted",
}

# The normal forward order. Any stage can still move to any other (people skip
# steps, companies reorder them); this order only drives suggestions in the UI.
PIPELINE_ORDER: list[Stage] = [
    Stage.SAVED, Stage.APPLIED, Stage.HR_SCREEN, Stage.TECHNICAL,
    Stage.HOME_ASSIGNMENT, Stage.FINAL, Stage.OFFER,
]
CLOSED_STAGES: list[Stage] = [Stage.ACCEPTED, Stage.REJECTED, Stage.WITHDRAWN, Stage.GHOSTED]
ACTIVE_STAGES: list[Stage] = list(PIPELINE_ORDER)


class StageChangeError(ValueError):
    pass


def stage_label(stage: str, stage_round: int | None = None) -> str:
    label = STAGE_LABELS.get(stage, stage)
    if stage == Stage.TECHNICAL and stage_round:
        return f"{label} (round {stage_round})"
    return label


def is_closed(stage: str) -> bool:
    return stage in CLOSED_STAGES


def suggested_next_stages(application: Application) -> list[Stage]:
    """Forward stages from the current one, then the closed states.

    From a technical interview the first suggestion is another technical round.
    """
    current = Stage(application.stage)
    if current in CLOSED_STAGES:
        return [stage for stage in PIPELINE_ORDER if stage != Stage.SAVED]
    index = PIPELINE_ORDER.index(current)
    forward = PIPELINE_ORDER[index + 1:]
    if current == Stage.TECHNICAL:
        forward = [Stage.TECHNICAL, *forward]
    closed = [stage for stage in CLOSED_STAGES if not (stage == Stage.ACCEPTED and current != Stage.OFFER)]
    return forward + closed


def _event_order(event: ApplicationEvent) -> tuple[date, float]:
    """Chronological order; events not yet flushed (no id) count as the newest."""
    return (event.occurred_on, event.id if event.id is not None else float("inf"))


def active_stage_events(application: Application) -> list[ApplicationEvent]:
    events = [
        event for event in application.events
        if event.kind == EventKind.STAGE_CHANGE and not event.is_undone
    ]
    return sorted(events, key=_event_order)


def _next_round(application: Application) -> int:
    rounds = [
        event.stage_round or 0
        for event in active_stage_events(application)
        if event.to_stage == Stage.TECHNICAL
    ]
    return max(rounds, default=0) + 1


def record_initial_stage(application: Application, stage: Stage, occurred_on: date) -> ApplicationEvent:
    """Log the stage an application was created in (from_stage is None)."""
    event = ApplicationEvent(
        kind=EventKind.STAGE_CHANGE,
        from_stage=None,
        to_stage=stage,
        stage_round=1 if stage == Stage.TECHNICAL else None,
        occurred_on=occurred_on,
    )
    application.events.append(event)
    recompute_stage(application)
    return event


def change_stage(
    session: Session,
    application: Application,
    to_stage: Stage,
    occurred_on: date | None = None,
    note: str | None = None,
    expected_answer_by: date | None = None,
) -> ApplicationEvent:
    """Move an application to a new stage and log it on the timeline.

    Moving to the technical stage again starts the next interview round.
    """
    occurred_on = occurred_on or today_local()
    current = Stage(application.stage)
    if to_stage == current and to_stage != Stage.TECHNICAL:
        raise StageChangeError(f"The application is already in {stage_label(current)}.")
    if occurred_on > today_local():
        raise StageChangeError("The date can't be in the future.")
    if occurred_on < application.stage_entered_on:
        raise StageChangeError(
            f"The date is before the current stage started ({application.stage_entered_on:%d %b %Y})."
        )
    if expected_answer_by is not None and expected_answer_by < occurred_on:
        raise StageChangeError("\"Expected answer by\" can't be before the stage date.")

    event = ApplicationEvent(
        kind=EventKind.STAGE_CHANGE,
        from_stage=current,
        to_stage=to_stage,
        stage_round=_next_round(application) if to_stage == Stage.TECHNICAL else None,
        occurred_on=occurred_on,
        note=(note or "").strip() or None,
        expected_answer_by=expected_answer_by,
    )
    application.events.append(event)
    session.flush()
    if current == Stage.SAVED and application.applied_on is None and to_stage != Stage.WITHDRAWN:
        application.applied_on = occurred_on
    # A snooze or follow-up belongs to the stage it was set in.
    application.snoozed_until = None
    application.last_followup_on = None
    recompute_stage(application)
    session.flush()
    return event


def undo_last_stage_change(session: Session, application: Application) -> ApplicationEvent:
    """Undo the most recent stage change. The creation event cannot be undone."""
    events = active_stage_events(application)
    if len(events) < 2:
        raise StageChangeError("There is no stage change to undo.")
    last = events[-1]
    last.undone_at = utcnow()
    recompute_stage(application)
    if application.stage == Stage.SAVED:
        application.applied_on = None
    session.flush()
    return last


def recompute_stage(application: Application) -> None:
    """Set the denormalized stage columns from the latest active stage event."""
    events = active_stage_events(application)
    if not events:
        return
    latest = events[-1]
    application.stage = latest.to_stage or Stage.SAVED
    application.stage_round = latest.stage_round
    application.stage_entered_on = latest.occurred_on
    application.expected_answer_by = latest.expected_answer_by


def set_expected_answer_by(application: Application, value: date | None) -> None:
    """Change the "expected answer by" date for the current stage."""
    events = active_stage_events(application)
    if events:
        events[-1].expected_answer_by = value
    application.expected_answer_by = value


def add_note(session: Session, application: Application, note: str, occurred_on: date | None = None) -> ApplicationEvent:
    text = note.strip()
    if not text:
        raise StageChangeError("The note is empty.")
    event = ApplicationEvent(
        kind=EventKind.NOTE,
        occurred_on=occurred_on or today_local(),
        note=text,
    )
    application.events.append(event)
    session.flush()
    return event


def timeline(application: Application) -> list[ApplicationEvent]:
    """All events, newest first."""
    return sorted(application.events, key=_event_order, reverse=True)
