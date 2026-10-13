"""Text normalization shared by matching, section detection and dedupe.

Rules are deliberately simple and deterministic: the same input must always
produce the same output, or the deterministic score stops being reproducible.
"""

import re
import unicodedata

HEBREW_CHARS = "֐-׿"
HEBREW_RE = re.compile(f"[{HEBREW_CHARS}]")
# Niqqud and cantillation marks: vowel points that some JDs include.
_NIQQUD_RE = re.compile("[֑-ׇ]")
# Single-letter prefixes that attach to a Hebrew word: and, the, in, to, from, that, as.
HEBREW_PREFIX_LETTERS = "ובלמשהכ"

_DASHES_RE = re.compile("[‐-―−־]")  # includes Hebrew maqaf
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "׳": "'",
    "“": '"', "”": '"', "„": '"', "״": '"',
}
_BIDI_CONTROLS_RE = re.compile("[‎‏‪-‮⁦-⁩]")
_SPACES_RE = re.compile(r"[ \t  -​　]+")


def normalize_text(text: str) -> str:
    """Lowercase, unify dashes/quotes/spaces, drop niqqud and bidi marks.

    Line breaks are kept because section detection works line by line.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _BIDI_CONTROLS_RE.sub("", text)
    text = _NIQQUD_RE.sub("", text)
    text = _DASHES_RE.sub("-", text)
    for fancy, plain in _QUOTES.items():
        text = text.replace(fancy, plain)
    text = text.lower()
    text = _SPACES_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(lines)


def has_hebrew(text: str) -> bool:
    return HEBREW_RE.search(text) is not None


def collapse_key(text: str) -> str:
    """Key used to look a matched phrase up in the synonym table.

    "Machine-Learning", "machine  learning" and "machine learning" all map to
    "machine learning".
    """
    return re.sub(r"[\s\-_]+", " ", text.strip().lower())


_COMPANY_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co",
    "llc", "plc", "gmbh", "sa", "ag", "bv", "israel", "il",
    "technologies", "technology", "software", "systems", "group",
    'בע"מ', "בעמ", "ישראל",
}


def normalize_company(name: str) -> str:
    """Normalize a company name for matching.

    "Check Point Software Technologies Ltd." -> "check point"
    "Microsoft Israel" -> "microsoft"
    Suffixes are only stripped from the end, and never down to nothing.
    """
    text = normalize_text(name).replace("\n", " ")
    text = re.sub(r"[^\w\s\"']", " ", text)
    words = [word.strip("'\"") for word in text.split()]
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


_TITLE_ABBREVIATIONS = {
    "sw": "software",
    "s/w": "software",
    "hw": "hardware",
    "h/w": "hardware",
    "eng": "engineer",
    "engr": "engineer",
    "dev": "developer",
    "sr": "senior",
    "jr": "junior",
    "fw": "firmware",
    "qa": "qa",
}


def normalize_title(title: str) -> str:
    """Normalize a job title for duplicate detection.

    "SW Eng. (Student) - Embedded" -> "software engineer student embedded"
    """
    text = normalize_text(title).replace("\n", " ")
    text = text.replace("s/w", "sw").replace("h/w", "hw")
    text = re.sub(r"[^\w\s+#]", " ", text)
    words = [_TITLE_ABBREVIATIONS.get(word, word) for word in text.split()]
    return " ".join(words)
