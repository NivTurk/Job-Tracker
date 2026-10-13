"""Storing CV files on disk, deduplicated by content hash, and attaching them to jobs.

Layout: <data_dir>/cvs/<first 2 hex chars>/<sha256>.<ext>. The same file
attached to five applications is stored once and has one cv_files row.
"""

import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application, ApplicationCv, CvFile
from app.scoring.format_checks import FORMAT_CHECKS_VERSION, run_format_checks
from app.services.extraction import detect_kind, extract

MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class CvTooLargeError(ValueError):
    pass


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_cv(
    session: Session,
    data_dir: Path,
    filename: str,
    data: bytes,
    max_bytes: int,
) -> tuple[CvFile, bool]:
    """Store a CV file and extract its text. Returns (cv_file, created).

    If a file with identical content exists, the existing row is returned and
    nothing is written. Raises UnsupportedFileError / ExtractionError /
    CvTooLargeError for files that cannot be used.
    """
    if len(data) > max_bytes:
        raise CvTooLargeError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")

    digest = sha256_of(data)
    existing = session.scalar(select(CvFile).where(CvFile.sha256 == digest))
    if existing is not None:
        return existing, False

    kind = detect_kind(filename, data)
    extraction = extract(data, kind)
    report = run_format_checks(extraction)

    relative = Path("cvs") / digest[:2] / f"{digest}.{kind}"
    absolute = data_dir / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(data)

    cv_file = CvFile(
        sha256=digest,
        original_name=Path(filename).name[:300] or f"cv.{kind}",
        stored_path=relative.as_posix(),
        mime_type=MIME_TYPES[kind],
        size_bytes=len(data),
        page_count=extraction.page_count,
        text=extraction.text,
        text_ok=extraction.text_ok,
        format_report=report,
    )
    session.add(cv_file)
    session.flush()
    return cv_file, True


def refresh_format_report(cv_file: CvFile, data_dir: Path) -> bool:
    """Re-run extraction and format checks if the stored report is from older code.

    Returns True if the row changed.
    """
    if cv_file.format_report.get("version") == FORMAT_CHECKS_VERSION:
        return False
    path = data_dir / cv_file.stored_path
    if not path.exists():
        return False
    data = path.read_bytes()
    extraction = extract(data, detect_kind(cv_file.original_name, data))
    cv_file.text = extraction.text
    cv_file.text_ok = extraction.text_ok
    cv_file.page_count = extraction.page_count
    cv_file.format_report = run_format_checks(extraction)
    return True


def attach_cv(
    session: Session,
    application: Application,
    cv_file: CvFile,
    label: str | None = None,
) -> ApplicationCv:
    """Attach a CV to an application as its newest version and mark it as sent.

    Attaching a file that is already attached to this application makes that
    version the sent one instead of creating a duplicate.
    """
    existing = session.scalar(
        select(ApplicationCv).where(
            ApplicationCv.application_id == application.id,
            ApplicationCv.cv_file_id == cv_file.id,
        )
    )
    if existing is not None:
        mark_sent(session, existing)
        if label:
            existing.label = label
        return existing

    last_version = session.scalar(
        select(func.max(ApplicationCv.version)).where(ApplicationCv.application_id == application.id)
    )
    _clear_sent(session, application.id)
    version = ApplicationCv(
        application_id=application.id,
        cv_file_id=cv_file.id,
        version=(last_version or 0) + 1,
        label=label or None,
        is_sent=True,
    )
    session.add(version)
    session.flush()
    session.refresh(application, ["cv_versions"])
    return version


def mark_sent(session: Session, version: ApplicationCv) -> None:
    if version.is_sent:
        return
    _clear_sent(session, version.application_id)
    version.is_sent = True
    session.flush()


def _clear_sent(session: Session, application_id: int) -> None:
    # Flushed separately: the partial unique index allows one sent CV per
    # application, so the old one must be cleared before the new one is set.
    for version in session.scalars(
        select(ApplicationCv).where(
            ApplicationCv.application_id == application_id,
            ApplicationCv.is_sent.is_(True),
        )
    ):
        version.is_sent = False
    session.flush()


def base_cvs(session: Session) -> dict[str, CvFile]:
    rows = session.scalars(select(CvFile).where(CvFile.base_role.is_not(None)))
    return {row.base_role: row for row in rows if row.base_role}


def set_base_cv(session: Session, role: str, cv_file: CvFile) -> None:
    """Make cv_file the base CV for a role ("systems" / "netsec"), replacing any previous one."""
    for row in session.scalars(select(CvFile).where(CvFile.base_role == role)):
        row.base_role = None
    session.flush()
    cv_file.base_role = role
    session.flush()
