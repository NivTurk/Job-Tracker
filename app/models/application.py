from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class Stage(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    HR_SCREEN = "hr_screen"
    TECHNICAL = "technical"
    HOME_ASSIGNMENT = "home_assignment"
    FINAL = "final"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


class EventKind(StrEnum):
    STAGE_CHANGE = "stage_change"
    FOLLOWUP = "followup"
    SNOOZE = "snooze"
    NOTE = "note"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    title_norm: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(32), default="other")

    jd_url: Mapped[str | None] = mapped_column(String(2000))
    # The stored text is the source of truth: postings disappear after a few weeks.
    jd_text: Mapped[str] = mapped_column(Text, default="")
    jd_hash: Mapped[str] = mapped_column(String(64), default="")
    jd_fetch_status: Mapped[str] = mapped_column(String(16), default="manual")

    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_details: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")

    applied_on: Mapped[date | None]

    # Current stage, kept in sync with the latest non-undone stage_change event
    # by services/pipeline.py. Denormalized so lists and filters stay cheap.
    stage: Mapped[str] = mapped_column(String(32), default=Stage.SAVED, index=True)
    stage_round: Mapped[int | None]
    stage_entered_on: Mapped[date]
    last_followup_on: Mapped[date | None]
    expected_answer_by: Mapped[date | None]
    offer_deadline: Mapped[date | None]
    snoozed_until: Mapped[date | None]

    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    company: Mapped["Company"] = relationship(back_populates="applications")  # noqa: F821
    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="(ApplicationEvent.occurred_on, ApplicationEvent.id)",
    )
    cv_versions: Mapped[list["ApplicationCv"]] = relationship(  # noqa: F821
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationCv.version",
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def sent_cv(self) -> "ApplicationCv | None":  # noqa: F821
        for version in self.cv_versions:
            if version.is_sent:
                return version
        return None


class ApplicationEvent(Base):
    """One entry on an application's timeline."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    from_stage: Mapped[str | None] = mapped_column(String(32))
    to_stage: Mapped[str | None] = mapped_column(String(32))
    stage_round: Mapped[int | None]
    occurred_on: Mapped[date]
    note: Mapped[str | None] = mapped_column(Text)
    expected_answer_by: Mapped[date | None]
    # Undo marks the event instead of deleting it, so the timeline stays honest.
    undone_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    application: Mapped[Application] = relationship(back_populates="events")

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None
