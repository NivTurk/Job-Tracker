"""Split a job description into required / preferred / other parts.

Works line by line. A short line that looks like a heading switches the
current section; inline cues ("an advantage", "יתרון") move single lines
into the preferred bucket. English, Hebrew and mixed JDs are handled with
the same rules.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from app.scoring.normalize import HEBREW_PREFIX_LETTERS, normalize_text

Section = Literal["required", "preferred", "other"]

# Heading phrases, matched at the start of a normalized heading line.
# Order matters only for readability; preferred is checked before required so
# "preferred qualifications" is not taken as "qualifications".
_PREFERRED_HEADINGS = [
    "nice to have", "nice-to-have", "nice to haves", "good to have",
    "preferred qualifications", "preferred skills", "preferred requirements", "preferred",
    "advantages", "advantage", "an advantage", "bonus points", "bonus", "pluses", "plus",
    "it would be great if", "it'd be great if", "extra points", "extra credit", "desirable",
    "even better if", "would be a plus",
    "יתרונות", "יתרון", "יתרון משמעותי", "נחמד שיהיה", "בונוס", "מה שיכול לעזור",
]
_REQUIRED_HEADINGS = [
    "requirements", "requirement", "required qualifications", "required skills", "required",
    "minimum qualifications", "basic qualifications", "qualifications", "must have", "must-haves",
    "must haves", "must", "what you'll need", "what you will need", "what you need",
    "what you bring", "what you'll bring", "what we're looking for", "what we are looking for",
    "what we look for", "who you are", "about you", "you have", "your profile",
    "your background", "the ideal candidate", "ideal candidate", "skills", "technical skills",
    "skills and experience", "experience", "job requirements", "key requirements",
    "דרישות", "דרישות התפקיד", "דרישות המשרה", "דרישות חובה", "דרישות סף", "כישורים",
    "כישורים נדרשים", "מה אנחנו מחפשים", "מה צריך", "מה צריך להביא", "ניסיון נדרש", "השכלה",
    "מי את/ה", "מי אתה", "מי את", "חובה",
]
_OTHER_HEADINGS = [
    "responsibilities", "key responsibilities", "what you'll do", "what you will do",
    "what you'll be doing", "what you will be doing", "about the role", "the role",
    "about the job", "about the position", "the position", "job description", "description",
    "about us", "about the company", "who we are", "company description", "our team",
    "about the team", "benefits", "perks", "what we offer", "we offer", "why join",
    "why us", "your mission", "your role", "day to day", "a day in the life", "overview",
    "how to apply", "equal opportunity", "location", "the opportunity",
    "תיאור התפקיד", "תיאור המשרה", "תחומי אחריות", "על החברה", "אודות", "אודות החברה",
    "מה אנחנו מציעים", "הטבות", "על התפקיד", "על הצוות", "מה תעשה", "מה תעשי", "מה תעשו",
    "המשרה כוללת", "התפקיד כולל",
]

# Inline cues that make a single line "preferred" even inside a requirements section.
_PREFERRED_CUES = re.compile(
    r"\b(advantage|advantageous|a plus|is a plus|nice to have|preferred|preferably|bonus|"
    r"desirable|would be great|is an asset|an asset)\b|יתרון|בונוס|רצוי"
)
_REQUIRED_CUES = re.compile(r"\b(must|required|mandatory|minimum of)\b|חובה|נדרש|נדרשת|נדרשים")

_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)\b"
    r"|(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?שנ(?:ות|ים|ה)\b"
    r"|(?:לפחות|מינימום)\s*(\d{1,2})\s*שנ"
)

_EXPERIENCE_CUES = re.compile(r"experience|background|hands-on|industry|professional|ניסיון|נסיון", re.IGNORECASE)
_STUDY_CUES = re.compile(r"\bleft\b|remaining|degree|studies|לימודים|לתואר|לסיום", re.IGNORECASE)

_BULLET_CHARS = "-*•·●▪◦‣–—>✓✔☑+"

# Words that may follow a heading phrase without turning the line into content.
_HEADING_FILLER = {
    "and", "&", "/", "qualifications", "requirements", "skills", "experience", "education",
    "the", "role", "position", "job", "for", "this", "our", "team", "responsibilities",
    "haves", "have", "to", "preferred", "required", "technical", "key",
    "ו", "התפקיד", "המשרה", "וכישורים", "וניסיון", "והשכלה",
}
_MAX_HEADING_WORDS = 8


@dataclass
class JdSections:
    required_text: str = ""
    preferred_text: str = ""
    other_text: str = ""
    headings_found: list[tuple[str, Section]] = field(default_factory=list)
    # True when no requirements-like heading was found, so the whole JD was
    # treated as requirements (minus lines with "advantage" cues).
    fallback: bool = False
    years_required: int | None = None


def _clean_heading(line: str) -> str:
    """Strip bullets, numbering, emoji and punctuation around a candidate heading."""
    text = line.strip().strip(_BULLET_CHARS).strip()
    text = re.sub(r"^\d+[.)]\s*", "", text)
    text = re.sub(r"[^\w\s'/+\-\"]", " ", text)  # emoji, colons, decorative symbols
    return re.sub(r"\s+", " ", text).strip()


def _matches_heading(candidate: str, phrases: list[str]) -> bool:
    """True if the candidate is one of the phrases, optionally followed only by filler words.

    "requirements and qualifications" matches "requirements"; "experience with
    linux" does not match "experience", because "with linux" is content.
    """
    for phrase in phrases:
        for text in _heading_variants(candidate):
            if text == phrase:
                return True
            if text.startswith(phrase + " "):
                extra = text[len(phrase) + 1:].split()
                if all(word in _HEADING_FILLER for word in extra):
                    return True
    return False


def _heading_variants(candidate: str) -> list[str]:
    """The candidate itself, plus the version without an attached Hebrew prefix letter."""
    variants = [candidate]
    if candidate[:1] in HEBREW_PREFIX_LETTERS and len(candidate) > 2:
        variants.append(candidate[1:])
    return variants


def classify_heading(line: str) -> tuple[Section | None, str]:
    """Return (section, remaining text on the line) if the line is a heading.

    "Requirements: 3+ years of C++" is a heading with inline content.
    Long lines are never headings.
    """
    head, sep, rest = line.partition(":")
    if not sep:
        head, rest = line, ""
    candidate = _clean_heading(head)
    if not candidate or len(candidate.split()) > _MAX_HEADING_WORDS:
        return None, line
    # A bullet line ending with a period is content, not a heading.
    if not sep and line.rstrip().endswith("."):
        return None, line
    if _matches_heading(candidate, _PREFERRED_HEADINGS):
        return "preferred", rest.strip()
    if _matches_heading(candidate, _REQUIRED_HEADINGS):
        return "required", rest.strip()
    if _matches_heading(candidate, _OTHER_HEADINGS):
        return "other", rest.strip()
    return None, line


def _sentences(line: str) -> list[str]:
    """Split a line into sentences so one "is a plus" does not move a whole paragraph."""
    parts = re.split(r"(?<=[.;!?])\s+", line)
    return [part for part in parts if part.strip()]


def _is_preferred_line(line: str) -> bool:
    return bool(_PREFERRED_CUES.search(line)) and not _REQUIRED_CUES.search(line)


def extract_years(text: str) -> int | None:
    """Largest "N years of experience" figure in the text.

    Only lines that talk about experience count, so "at least 2 years left
    in your degree" is not read as an experience requirement.
    """
    years: list[int] = []
    for line in text.split("\n"):
        if not _EXPERIENCE_CUES.search(line) or _STUDY_CUES.search(line):
            continue
        for match in _YEARS_RE.finditer(line):
            value = next(group for group in match.groups() if group)
            number = int(value)
            if 0 < number <= 20:
                years.append(number)
    return max(years) if years else None


def split_sections(jd_text: str) -> JdSections:
    lines = normalize_text(jd_text).split("\n")
    buckets: dict[Section, list[str]] = {"required": [], "preferred": [], "other": []}
    headings: list[tuple[str, Section]] = []
    current: Section | None = None
    preamble: list[str] = []  # lines before the first heading

    for line in lines:
        if not line:
            continue
        section, rest = classify_heading(line)
        if section is not None:
            headings.append((_clean_heading(line.partition(":")[0]), section))
            current = section
            if not rest:
                continue
            line = rest
        if current is None:
            preamble.append(line)
            continue
        if current == "required":
            for sentence in _sentences(line):
                bucket: Section = "preferred" if _is_preferred_line(sentence) else "required"
                buckets[bucket].append(sentence)
        else:
            buckets[current].append(line)

    has_requirement_section = any(section in ("required", "preferred") for _, section in headings)
    result = JdSections(headings_found=headings)

    if not has_requirement_section:
        # No usable headings: treat everything as requirements, except lines
        # that say they are an advantage.
        result.fallback = True
        all_lines = preamble + buckets["required"] + buckets["preferred"] + buckets["other"]
        sentences = [sentence for line in all_lines for sentence in _sentences(line)]
        required = [sentence for sentence in sentences if not _is_preferred_line(sentence)]
        preferred = [sentence for sentence in sentences if _is_preferred_line(sentence)]
        result.required_text = "\n".join(required)
        result.preferred_text = "\n".join(preferred)
    else:
        result.required_text = "\n".join(buckets["required"])
        result.preferred_text = "\n".join(buckets["preferred"])
        result.other_text = "\n".join(preamble + buckets["other"])

    result.years_required = extract_years(result.required_text)
    return result
