from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class CvFile(Base):
    """One row per unique file (by content hash), however many jobs use it."""

    __tablename__ = "cv_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_name: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(500))  # relative to the data dir
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    page_count: Mapped[int | None]
    text: Mapped[str] = mapped_column(Text, default="")
    # False when almost nothing could be extracted (scanned / image-only PDF).
    text_ok: Mapped[bool] = mapped_column(default=True)
    # Format checks only depend on the file, so they are computed once at upload.
    format_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # "systems" / "netsec" for the two base CVs used by job search, else None.
    base_role: Mapped[str | None] = mapped_column(String(32))
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)

    attachments: Mapped[list["ApplicationCv"]] = relationship(back_populates="cv_file")


class ApplicationCv(Base):
    """A CV version attached to an application. Exactly one can be the sent one."""

    __tablename__ = "application_cvs"
    __table_args__ = (
        UniqueConstraint("application_id", "cv_file_id", name="uq_application_cv_file"),
        Index(
            "uq_one_sent_cv_per_application",
            "application_id",
            unique=True,
            sqlite_where=text("is_sent = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    cv_file_id: Mapped[int] = mapped_column(ForeignKey("cv_files.id"))
    version: Mapped[int]
    label: Mapped[str | None] = mapped_column(String(200))
    is_sent: Mapped[bool] = mapped_column(default=False)
    attached_at: Mapped[datetime] = mapped_column(default=utcnow)

    application: Mapped["Application"] = relationship(back_populates="cv_versions")  # noqa: F821
    cv_file: Mapped[CvFile] = relationship(back_populates="attachments")
