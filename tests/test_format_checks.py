"""CV format checks and Israeli phone validation."""

import pytest

from app.scoring.format_checks import format_score, parse_israeli_phone, run_format_checks
from app.services.extraction import Extraction

GOOD_TEXT = (
    "Niv Example\nniv@example.com | 054-123-4567 | github.com/niv\n"
    "Network traffic analyzer, Jan 2025 - Present. Built tools in Python and C for packet parsing. "
    "B.Sc. Computer Science, 2021 - 2026. Wrote unit tests, set up CI, and documented everything clearly."
)


def codes(report: dict) -> set[str]:
    return {warning["code"] for warning in report["warnings"]}


class TestIsraeliPhone:
    @pytest.mark.parametrize(
        ("raw", "kind"),
        [
            ("054-1234567", "mobile"),
            ("054-123-4567", "mobile"),
            ("0541234567", "mobile"),
            ("+972-54-123-4567", "mobile"),
            ("+972 54 123 4567", "mobile"),
            ("972541234567", "mobile"),
            ("(054) 123-4567", "mobile"),
            ("03-1234567", "landline"),
            ("09.765.4321", "landline"),
            ("077-1234567", "voip"),
        ],
    )
    def test_valid(self, raw: str, kind: str) -> None:
        valid, detected_kind, problem = parse_israeli_phone(raw)
        assert valid, problem
        assert detected_kind == kind

    def test_zero_after_country_code(self) -> None:
        valid, _, problem = parse_israeli_phone("+972-054-1234567")
        assert not valid
        assert "Drop the 0" in (problem or "")

    @pytest.mark.parametrize("raw", ["054-12345", "05412345678", "01-1234567", "+1 415 555 0100"])
    def test_invalid(self, raw: str) -> None:
        assert not parse_israeli_phone(raw)[0]


class TestRunFormatChecks:
    def test_clean_docx_has_only_info_items(self) -> None:
        report = run_format_checks(Extraction(file_kind="docx", text=GOOD_TEXT, page_count=1))
        assert all(w["severity"] == "info" for w in report["warnings"])
        assert report["emails"] == ["niv@example.com"]
        assert report["phones"][0]["valid"]
        assert format_score(report) == 100

    def test_no_text_is_an_error_and_stops(self) -> None:
        report = run_format_checks(Extraction(file_kind="pdf", text="  ", page_count=1))
        assert codes(report) == {"no_text"}
        assert report["text_ok"] is False
        assert format_score(report) == 0

    def test_missing_contact_details(self) -> None:
        text = GOOD_TEXT.replace("niv@example.com", "").replace("054-123-4567", "")
        report = run_format_checks(Extraction(file_kind="pdf", text=text, page_count=1))
        assert {"no_email", "no_phone"} <= codes(report)

    def test_year_ranges_are_not_phone_numbers(self) -> None:
        text = GOOD_TEXT.replace("054-123-4567", "")
        report = run_format_checks(Extraction(file_kind="pdf", text=text, page_count=1))
        assert report["phones"] == []

    def test_invalid_phone(self) -> None:
        text = GOOD_TEXT.replace("054-123-4567", "054-123-456")
        report = run_format_checks(Extraction(file_kind="pdf", text=text, page_count=1))
        assert "invalid_phone" in codes(report)

    def test_dates(self) -> None:
        text = GOOD_TEXT.replace("2021 - 2026", "'21 - '26").replace("Jan 2025", "Summer '25")
        text = text.replace("2025", "").replace("2026", "")
        report = run_format_checks(Extraction(file_kind="pdf", text=text, page_count=1))
        assert {"no_dates", "short_years"} <= codes(report)

    def test_length(self) -> None:
        report = run_format_checks(Extraction(file_kind="pdf", text=GOOD_TEXT, page_count=2))
        assert "too_long" in codes(report)
        estimated = run_format_checks(
            Extraction(file_kind="docx", text=GOOD_TEXT, page_count=2, page_count_estimated=True)
        )
        assert "maybe_long" in codes(estimated)
        assert "too_long" not in codes(estimated)

    def test_docx_textboxes_tables_and_header(self) -> None:
        extraction = Extraction(
            file_kind="docx",
            text=GOOD_TEXT,
            page_count=1,
            textbox_text="Skills: C, C++",
            table_text=GOOD_TEXT,
            header_footer_text="niv@example.com 054-123-4567",
        )
        report = run_format_checks(extraction)
        assert {"docx_textboxes", "docx_tables", "docx_contact_in_header"} <= codes(report)

    def test_pdf_heuristics_are_labelled(self) -> None:
        extraction = Extraction(file_kind="pdf", text=GOOD_TEXT, page_count=1, layout_notes=["multi_column"])
        report = run_format_checks(extraction)
        warning = next(w for w in report["warnings"] if w["code"] == "pdf_multi_column")
        assert "heuristic" in warning["message"]

    def test_each_warning_costs_ten_points(self) -> None:
        report = {"warnings": [{"severity": "warning"}, {"severity": "warning"}, {"severity": "info"}]}
        assert format_score(report) == 80
