"""Text extraction from DOCX and PDF, including layout signals."""

import pytest
from helpers import SAMPLE_CV_LINES, make_docx, make_pdf

from app.services.extraction import (
    UnsupportedFileError,
    detect_kind,
    extract,
    fix_reversed_hebrew,
    looks_like_reversed_hebrew,
)


class TestDetectKind:
    def test_by_content_not_name(self) -> None:
        assert detect_kind("cv.bin", make_pdf(["hello"])) == "pdf"
        assert detect_kind("cv.pdf", make_docx(["hello"])) == "docx"

    def test_rejects_other_files(self) -> None:
        with pytest.raises(UnsupportedFileError):
            detect_kind("cv.txt", b"plain text")
        with pytest.raises(UnsupportedFileError, match=".docx"):
            detect_kind("cv.doc", b"\xd0\xcf\x11\xe0 old word file")


class TestDocx:
    def test_body_tables_header_and_textbox_are_all_extracted(self) -> None:
        data = make_docx(
            ["Body paragraph about Linux"],
            table_rows=[["Skills", "C++, Python"]],
            header_text="niv@example.com",
            textbox_text="Kubernetes in a text box",
        )
        result = extract(data, "docx")
        for snippet in ("Body paragraph", "C++, Python", "niv@example.com", "Kubernetes in a text box"):
            assert snippet in result.text
        assert "C++, Python" in result.table_text
        assert result.textbox_text == "Kubernetes in a text box"
        assert result.header_footer_text == "niv@example.com"

    def test_page_count_from_document_properties(self) -> None:
        result = extract(make_docx(SAMPLE_CV_LINES, pages=2), "docx")
        assert result.page_count == 2
        assert not result.page_count_estimated

    def test_page_count_estimated_when_missing(self) -> None:
        # pages=0 is what a file without a stored page count looks like
        result = extract(make_docx(SAMPLE_CV_LINES, pages=0), "docx")
        assert result.page_count == 1
        assert result.page_count_estimated

    def test_hebrew_text_kept_in_logical_order(self) -> None:
        result = extract(make_docx(["ניסיון בלינוקס ובמערכות הפעלה"]), "docx")
        assert "ניסיון בלינוקס" in result.text


class TestPdf:
    def test_text_and_page_count(self) -> None:
        result = extract(make_pdf(SAMPLE_CV_LINES), "pdf")
        assert "niv@example.com" in result.text
        assert result.page_count == 1
        assert result.text_ok

    def test_image_only_pdf_has_no_usable_text(self) -> None:
        result = extract(make_pdf([], blank=True), "pdf")
        assert not result.text_ok

    def test_two_column_layout_is_flagged(self) -> None:
        result = extract(make_pdf(SAMPLE_CV_LINES * 2, two_columns=True), "pdf")
        assert "multi_column" in result.layout_notes

    def test_single_column_is_not_flagged(self) -> None:
        result = extract(make_pdf(SAMPLE_CV_LINES * 2), "pdf")
        assert "multi_column" not in result.layout_notes


class TestReversedHebrew:
    def test_detects_reversed_words(self) -> None:
        # Visual-order extraction of "ניסיון בלינוקס ובמערכות הפעלה וידע ברשתות"
        reversed_text = "תותשרב עדיו הלעפה תוכרעמבו סקונילב ןויסינ"
        assert looks_like_reversed_hebrew(reversed_text + " ןויסינ ןויסינ")
        assert not looks_like_reversed_hebrew("ניסיון בלינוקס ובמערכות הפעלה וידע ברשתות ניסיון")

    def test_fix_restores_order_and_leaves_latin_alone(self) -> None:
        assert fix_reversed_hebrew("C++ סקונילב ןויסינ") == "C++ ניסיון בלינוקס"
