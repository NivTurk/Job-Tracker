"""Dedupe: CV files by content hash, applications by company + similar title."""

from pathlib import Path

import pytest
from helpers import SAMPLE_CV_LINES, make_docx, make_pdf
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ApplicationCv, CvFile, Stage
from app.services.applications import ApplicationInput, create_application, find_duplicates, titles_similar
from app.services.cv_store import attach_cv, mark_sent, store_cv

MAX = 10 * 1024 * 1024


def new_application(session: Session, company: str = "NVIDIA", title: str = "Embedded SW Student"):
    return create_application(
        session,
        ApplicationInput(company=company, title=title, jd_text="Requirements:\n- C++", initial_stage=Stage.APPLIED),
    )


class TestCvDedupe:
    def test_same_file_is_stored_once(self, session: Session, data_dir: Path) -> None:
        data = make_docx(SAMPLE_CV_LINES)
        first, created_first = store_cv(session, data_dir, "cv.docx", data, MAX)
        second, created_second = store_cv(session, data_dir, "renamed.docx", data, MAX)
        assert created_first and not created_second
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(CvFile)) == 1
        assert len(list((data_dir / "cvs").rglob("*.docx"))) == 1

    def test_one_file_linked_to_many_jobs(self, session: Session, data_dir: Path) -> None:
        cv_file, _ = store_cv(session, data_dir, "cv.pdf", make_pdf(SAMPLE_CV_LINES), MAX)
        app_a = new_application(session, "NVIDIA", "Embedded SW Student")
        app_b = new_application(session, "Wiz", "Security Research Student")
        attach_cv(session, app_a, cv_file)
        attach_cv(session, app_b, cv_file)
        session.commit()
        assert {attachment.application_id for attachment in cv_file.attachments} == {app_a.id, app_b.id}

    def test_file_stored_under_hash_path(self, session: Session, data_dir: Path) -> None:
        cv_file, _ = store_cv(session, data_dir, "cv.pdf", make_pdf(SAMPLE_CV_LINES), MAX)
        assert cv_file.stored_path == f"cvs/{cv_file.sha256[:2]}/{cv_file.sha256}.pdf"
        assert (data_dir / cv_file.stored_path).exists()

    def test_too_large_file_is_rejected(self, session: Session, data_dir: Path) -> None:
        with pytest.raises(ValueError, match="larger than"):
            store_cv(session, data_dir, "cv.pdf", make_pdf(SAMPLE_CV_LINES), max_bytes=100)


class TestCvVersions:
    def test_new_version_becomes_sent(self, session: Session, data_dir: Path) -> None:
        application = new_application(session)
        v1_file, _ = store_cv(session, data_dir, "v1.docx", make_docx(SAMPLE_CV_LINES), MAX)
        v2_file, _ = store_cv(session, data_dir, "v2.docx", make_docx(SAMPLE_CV_LINES + ["Kubernetes"]), MAX)
        v1 = attach_cv(session, application, v1_file)
        v2 = attach_cv(session, application, v2_file, label="added k8s")
        session.commit()
        assert (v1.version, v2.version) == (1, 2)
        assert not v1.is_sent and v2.is_sent
        assert application.sent_cv is v2

    def test_mark_older_version_as_sent(self, session: Session, data_dir: Path) -> None:
        application = new_application(session)
        v1 = attach_cv(session, application, store_cv(session, data_dir, "a.docx", make_docx(["a" * 200]), MAX)[0])
        v2 = attach_cv(session, application, store_cv(session, data_dir, "b.docx", make_docx(["b" * 200]), MAX)[0])
        mark_sent(session, v1)
        session.commit()
        assert v1.is_sent and not v2.is_sent

    def test_reattaching_same_file_does_not_duplicate(self, session: Session, data_dir: Path) -> None:
        application = new_application(session)
        cv_file, _ = store_cv(session, data_dir, "a.docx", make_docx(SAMPLE_CV_LINES), MAX)
        first = attach_cv(session, application, cv_file)
        again = attach_cv(session, application, cv_file)
        assert first.id == again.id
        assert len(application.cv_versions) == 1

    def test_database_allows_only_one_sent_version(self, session: Session, data_dir: Path) -> None:
        application = new_application(session)
        attach_cv(session, application, store_cv(session, data_dir, "a.docx", make_docx(["a" * 200]), MAX)[0])
        other, _ = store_cv(session, data_dir, "b.docx", make_docx(["b" * 200]), MAX)
        session.add(ApplicationCv(application_id=application.id, cv_file_id=other.id, version=9, is_sent=True))
        with pytest.raises(IntegrityError):
            session.flush()


class TestApplicationDuplicates:
    @pytest.mark.parametrize(
        ("a", "b", "similar"),
        [
            ("Embedded SW Student", "Embedded Software Student", True),
            ("Embedded Software Engineer", "Embedded Software Eng.", True),
            ("Student Firmware Engineer", "Firmware Engineer", True),
            ("Embedded Software Student", "Security Researcher", False),
            ("Backend Developer", "Firmware Engineer", False),
        ],
    )
    def test_titles_similar(self, a: str, b: str, similar: bool) -> None:
        assert titles_similar(a, b) is similar

    def test_same_company_different_spelling(self, session: Session) -> None:
        existing = new_application(session, "Check Point Software Technologies Ltd.", "Security Research Student")
        duplicates = find_duplicates(session, "Check Point", "Security Research - Student")
        assert [d.id for d in duplicates] == [existing.id]

    def test_other_company_is_not_a_duplicate(self, session: Session) -> None:
        new_application(session, "NVIDIA", "Embedded SW Student")
        assert find_duplicates(session, "Mobileye", "Embedded SW Student") == []

    def test_exclude_self(self, session: Session) -> None:
        application = new_application(session)
        assert find_duplicates(session, "NVIDIA", "Embedded SW Student", exclude_id=application.id) == []

    def test_company_rows_are_shared(self, session: Session) -> None:
        a = new_application(session, "Microsoft Israel", "Kernel Student")
        b = new_application(session, "Microsoft", "Azure Networking Student")
        assert a.company_id == b.company_id
