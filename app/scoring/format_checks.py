"""ATS-oriented format checks on an extracted CV.

The result is a plain dict so it can be stored as JSON on the cv_files row.
Each warning has a severity:
  error   - the CV cannot be scored or parsed (e.g. no extractable text)
  warning - likely to hurt parsing or a recruiter's first read
  info    - worth knowing, no score impact
"""

import re
from typing import Any, Literal

from app.services.extraction import Extraction

FORMAT_CHECKS_VERSION = 1

Severity = Literal["error", "warning", "info"]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Phone-like sequences that start the way Israeli numbers are written:
# +972..., 972..., or 0... Year ranges like "2019 - 2023" never start with 0.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w+])(?:\+ ?972|972|0)[\d \t\-().]{7,16}\d(?!\d)")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/", re.IGNORECASE)
_GITHUB_RE = re.compile(r"github\.com/[\w-]+", re.IGNORECASE)

_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)(?:19[89]\d|20[0-4]\d)(?!\d)")
_APOSTROPHE_YEAR_RE = re.compile(r"(?<![\w])['’]\d{2}(?!\d)")
_SEASON_YEAR_RE = re.compile(r"\b(?:spring|summer|fall|autumn|winter)\s+(?:19|20)\d{2}\b", re.IGNORECASE)


def parse_israeli_phone(raw: str) -> tuple[bool, str | None, str | None]:
    """Validate an Israeli phone number.

    Returns (valid, kind, problem). kind is "mobile", "landline" or "voip".
    Accepted: 05X-XXXXXXX, +972-5X-XXX-XXXX, 972 5X..., 0X-XXXXXXX
    (X in 2,3,4,8,9), 07X-XXXXXXX. Separators (space, dash, dot, brackets) are ignored.
    """
    digits = re.sub(r"[\s\-().]", "", raw)
    has_plus = digits.startswith("+")
    digits = digits.lstrip("+")

    if digits.startswith("9720"):
        return False, None, "Drop the 0 after the country code: +972-5X..., not +972-05X..."
    if digits.startswith("972"):
        national = digits[3:]
    elif digits.startswith("0"):
        if has_plus:
            return False, None, "A leading + must be followed by the country code (972)."
        national = digits[1:]
    else:
        return False, None, "Does not start with 0 or +972."

    if not national.isdigit():
        return False, None, "Contains characters other than digits and separators."
    if re.fullmatch(r"5\d{8}", national):
        return True, "mobile", None
    if re.fullmatch(r"7[2-9]\d{7}", national):
        return True, "voip", None
    if re.fullmatch(r"[23489]\d{7}", national):
        return True, "landline", None
    return False, None, "Wrong number of digits or an unknown prefix for an Israeli number."


def _warning(code: str, severity: Severity, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _has_contact_info(text: str) -> bool:
    return bool(_EMAIL_RE.search(text) or _PHONE_CANDIDATE_RE.search(text))


def run_format_checks(extraction: Extraction) -> dict[str, Any]:
    text = extraction.text
    warnings: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "version": FORMAT_CHECKS_VERSION,
        "text_ok": extraction.text_ok,
        "page_count": extraction.page_count,
        "page_count_estimated": extraction.page_count_estimated,
        "emails": [],
        "phones": [],
        "warnings": warnings,
    }

    # 1. Extractable text
    if not extraction.text_ok:
        warnings.append(_warning(
            "no_text", "error",
            "Almost no text could be extracted. This is probably a scanned or image-only file, "
            "which ATS parsers cannot read. Scoring is skipped.",
        ))
        return report

    if extraction.hebrew_order_fixed:
        warnings.append(_warning(
            "hebrew_reversed", "warning",
            "Hebrew text in this PDF extracts in reversed order. It was corrected for scoring, "
            "but ATS parsers may read it backwards. Exporting the PDF from Word usually fixes this.",
        ))

    # 2. Contact details
    emails = sorted(set(_EMAIL_RE.findall(text)))
    report["emails"] = emails
    if not emails:
        warnings.append(_warning("no_email", "warning", "No email address found."))

    phones: list[dict[str, Any]] = []
    for match in _PHONE_CANDIDATE_RE.finditer(text):
        raw = match.group(0).strip()
        valid, kind, problem = parse_israeli_phone(raw)
        phones.append({"raw": raw, "valid": valid, "kind": kind, "problem": problem})
    report["phones"] = phones
    if not phones:
        warnings.append(_warning("no_phone", "warning", "No phone number found."))
    elif not any(phone["valid"] for phone in phones):
        problem = next(phone["problem"] for phone in phones if phone["problem"])
        warnings.append(_warning(
            "invalid_phone", "warning",
            f"Phone number \"{phones[0]['raw']}\" does not look like a valid Israeli number. {problem}",
        ))

    if not _LINKEDIN_RE.search(text) and not _GITHUB_RE.search(text):
        warnings.append(_warning("no_profile_links", "info", "No LinkedIn or GitHub link found."))

    # 3. Dates
    if not _FOUR_DIGIT_YEAR_RE.search(text):
        warnings.append(_warning(
            "no_dates", "warning",
            "No 4-digit years found. ATS parsers use dates to work out experience; "
            "use formats like \"Jan 2023 - Present\" or \"2021 - 2023\".",
        ))
    if _APOSTROPHE_YEAR_RE.search(text):
        warnings.append(_warning(
            "short_years", "warning",
            "Two-digit years like '21 are often not parsed. Use 4-digit years.",
        ))
    if _SEASON_YEAR_RE.search(text):
        warnings.append(_warning(
            "season_dates", "info",
            "Season-based dates (e.g. \"Summer 2023\") are parsed inconsistently. A month is safer.",
        ))

    # 4. Length
    if extraction.page_count and extraction.page_count > 1:
        if extraction.page_count_estimated:
            warnings.append(_warning(
                "maybe_long", "info",
                f"The text is long enough that it may run to {extraction.page_count} pages "
                "(estimated, the file has no stored page count).",
            ))
        else:
            warnings.append(_warning(
                "too_long", "warning",
                f"The CV is {extraction.page_count} pages. Junior CVs usually fit on one.",
            ))

    # 5. Content ATS parsers may skip
    if extraction.file_kind == "docx":
        _docx_layout_checks(extraction, warnings)
    else:
        _pdf_layout_checks(extraction, warnings)

    return report


def _docx_layout_checks(extraction: Extraction, warnings: list[dict[str, str]]) -> None:
    total_words = max(1, len(extraction.text.split()))
    if extraction.textbox_text.strip():
        words = len(extraction.textbox_text.split())
        warnings.append(_warning(
            "docx_textboxes", "warning",
            f"{words} words are inside text boxes. Many ATS parsers skip text boxes entirely; "
            "move that content into the main body.",
        ))
    if extraction.header_footer_text.strip() and _has_contact_info(extraction.header_footer_text):
        warnings.append(_warning(
            "docx_contact_in_header", "warning",
            "Contact details are in the page header/footer. Some parsers ignore headers; "
            "put your name, email and phone in the body.",
        ))
    if extraction.table_text.strip():
        table_words = len(extraction.table_text.split())
        share = table_words / total_words
        if share >= 0.3 or _has_contact_info(extraction.table_text):
            warnings.append(_warning(
                "docx_tables", "warning",
                f"{round(share * 100)}% of the text is inside tables. Parsers often read tables "
                "out of order or merge cells; a single-column layout is safer.",
            ))
        else:
            warnings.append(_warning(
                "docx_tables_minor", "info",
                "The CV uses a table for a small part of the content.",
            ))


def _pdf_layout_checks(extraction: Extraction, warnings: list[dict[str, str]]) -> None:
    if "multi_column" in extraction.layout_notes:
        warnings.append(_warning(
            "pdf_multi_column", "warning",
            "The layout looks like it has more than one column (heuristic). Parsers may read "
            "across columns and mix sections together.",
        ))
    if "pdf_tables" in extraction.layout_notes:
        warnings.append(_warning(
            "pdf_tables", "info",
            "Table-like structures were detected (heuristic). Check that the text order still "
            "makes sense when read top to bottom.",
        ))
    if extraction.header_footer_text.strip() and _has_contact_info(extraction.header_footer_text):
        warnings.append(_warning(
            "pdf_contact_at_edge", "info",
            "Contact details sit at the very top or bottom edge of the page (heuristic). If this is "
            "a header/footer in the source document, some parsers will skip it.",
        ))


SEVERITY_PENALTY = {"error": 100, "warning": 10, "info": 0}


def format_score(report: dict[str, Any]) -> int:
    """100 minus a fixed penalty per warning, floored at 0."""
    penalty = sum(SEVERITY_PENALTY.get(w["severity"], 0) for w in report.get("warnings", []))
    return max(0, 100 - penalty)
