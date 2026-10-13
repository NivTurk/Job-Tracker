"""Stage pipeline: transitions, rounds, undo, validation."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Stage
from app.services import pipeline
from app.services.applications import ApplicationInput, create_application
from app.timeutil import today_local


def make_app(session: Session, stage: Stage = Stage.APPLIED, applied_on: date | None = None):
    return create_application(
        session,
        ApplicationInput(
            company="NVIDIA",
            title="Embedded SW Student",
            jd_text="Requirements:\n- C",
            initial_stage=stage,
            applied_on=applied_on,
        ),
    )


def days_ago(n: int) -> date:
    return today_local() - timedelta(days=n)


def test_created_as_applied_logs_initial_event(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    assert application.stage == Stage.APPLIED
    assert application.stage_entered_on == days_ago(10)
    assert len(application.events) == 1
    assert application.events[0].from_stage is None


def test_saved_has_no_applied_date_until_applied(session: Session) -> None:
    application = make_app(session, stage=Stage.SAVED)
    assert application.applied_on is None
    pipeline.change_stage(session, application, Stage.APPLIED, days_ago(0))
    assert application.applied_on == days_ago(0)


def test_change_stage_logs_note_and_expected_date(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    event = pipeline.change_stage(
        session, application, Stage.HR_SCREEN, days_ago(3), note="call with Dana", expected_answer_by=days_ago(-2)
    )
    assert application.stage == Stage.HR_SCREEN
    assert application.stage_entered_on == days_ago(3)
    assert application.expected_answer_by == days_ago(-2)
    assert event.note == "call with Dana"


def test_technical_rounds_count_up(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(20))
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(10))
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(5))
    assert application.stage_round == 2
    pipeline.change_stage(session, application, Stage.HOME_ASSIGNMENT, days_ago(3))
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(1))
    assert application.stage_round == 3
    assert pipeline.stage_label(application.stage, application.stage_round) == "Technical interview (round 3)"


def test_same_stage_again_is_rejected_except_technical(session: Session) -> None:
    application = make_app(session)
    with pytest.raises(pipeline.StageChangeError, match="already"):
        pipeline.change_stage(session, application, Stage.APPLIED)


def test_future_and_out_of_order_dates_are_rejected(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(5))
    with pytest.raises(pipeline.StageChangeError, match="future"):
        pipeline.change_stage(session, application, Stage.HR_SCREEN, days_ago(-1))
    with pytest.raises(pipeline.StageChangeError, match="before the current stage"):
        pipeline.change_stage(session, application, Stage.HR_SCREEN, days_ago(6))


def test_undo_restores_previous_stage_and_keeps_history(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    pipeline.change_stage(session, application, Stage.HR_SCREEN, days_ago(5), expected_answer_by=days_ago(-1))
    pipeline.change_stage(session, application, Stage.REJECTED, days_ago(1))

    undone = pipeline.undo_last_stage_change(session, application)
    assert undone.to_stage == Stage.REJECTED
    assert undone.is_undone
    assert application.stage == Stage.HR_SCREEN
    assert application.stage_entered_on == days_ago(5)
    assert application.expected_answer_by == days_ago(-1)
    assert len(application.events) == 3  # nothing deleted


def test_undo_twice_then_nothing_left(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    pipeline.change_stage(session, application, Stage.HR_SCREEN, days_ago(5))
    pipeline.undo_last_stage_change(session, application)
    assert application.stage == Stage.APPLIED
    with pytest.raises(pipeline.StageChangeError, match="no stage change"):
        pipeline.undo_last_stage_change(session, application)


def test_undo_back_to_saved_clears_applied_date(session: Session) -> None:
    application = make_app(session, stage=Stage.SAVED)
    pipeline.change_stage(session, application, Stage.APPLIED)
    pipeline.undo_last_stage_change(session, application)
    assert application.stage == Stage.SAVED
    assert application.applied_on is None


def test_undone_technical_round_is_not_counted(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(5))
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(4))
    pipeline.undo_last_stage_change(session, application)
    pipeline.change_stage(session, application, Stage.TECHNICAL, days_ago(3))
    assert application.stage_round == 2


def test_suggested_next_stages(session: Session) -> None:
    application = make_app(session)
    suggestions = pipeline.suggested_next_stages(application)
    assert suggestions[0] == Stage.HR_SCREEN
    assert Stage.ACCEPTED not in suggestions  # only offered once there is an offer
    pipeline.change_stage(session, application, Stage.TECHNICAL)
    assert pipeline.suggested_next_stages(application)[0] == Stage.TECHNICAL


def test_timeline_is_newest_first(session: Session) -> None:
    application = make_app(session, applied_on=days_ago(10))
    pipeline.change_stage(session, application, Stage.HR_SCREEN, days_ago(5))
    pipeline.add_note(session, application, "sent thank-you email", days_ago(4))
    kinds = [event.kind for event in pipeline.timeline(application)]
    assert kinds == ["note", "stage_change", "stage_change"]
