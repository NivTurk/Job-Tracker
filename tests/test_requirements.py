"""JD section detection in English, Hebrew and mixed text."""

from helpers import read_fixture

from app.scoring.matching import load_lexicon
from app.scoring.requirements import classify_heading, extract_years, split_sections


def skills_in(text: str) -> set[str]:
    return set(load_lexicon().find(text))


class TestHeadings:
    def test_plain_and_colon_headings(self) -> None:
        assert classify_heading("requirements:")[0] == "required"
        assert classify_heading("nice to have")[0] == "preferred"
        assert classify_heading("responsibilities")[0] == "other"

    def test_heading_with_filler_words(self) -> None:
        assert classify_heading("requirements & qualifications")[0] == "required"

    def test_content_line_is_not_a_heading(self) -> None:
        assert classify_heading("experience with linux")[0] is None
        assert classify_heading("- must have strong communication skills.")[0] is None

    def test_inline_content_after_colon_is_kept(self) -> None:
        section, rest = classify_heading("requirements: 3+ years of c++")
        assert section == "required"
        assert rest == "3+ years of c++"

    def test_hebrew_headings(self) -> None:
        assert classify_heading("דרישות התפקיד:")[0] == "required"
        assert classify_heading("יתרונות:")[0] == "preferred"
        assert classify_heading("ויתרון:")[0] == "preferred"  # attached prefix letter
        assert classify_heading("תיאור התפקיד")[0] == "other"

    def test_preferred_wins_over_required(self) -> None:
        assert classify_heading("preferred qualifications")[0] == "preferred"


class TestSplitSections:
    def test_english_jd(self) -> None:
        sections = split_sections(read_fixture("jd_english.txt"))
        assert not sections.fallback
        required = skills_in(sections.required_text)
        preferred = skills_in(sections.preferred_text)
        assert {"C", "C++", "Linux kernel", "Networking", "CS / EE degree"} <= required
        # "Python - an advantage" sits under Requirements but is preferred.
        assert "Python" not in required
        assert {"Python", "Kubernetes", "Packet analysis"} <= preferred
        # Skills mentioned only in the role description don't count.
        assert "Rust" not in required | preferred

    def test_hebrew_jd(self) -> None:
        sections = split_sections(read_fixture("jd_hebrew.txt"))
        assert not sections.fallback
        required = skills_in(sections.required_text)
        preferred = skills_in(sections.preferred_text)
        assert {"C", "Linux", "Networking", "CS / EE degree"} <= required
        assert {"Python", "Docker / containers", "Packet analysis"} <= preferred
        assert "Go" not in required | preferred
        assert sections.years_required == 3

    def test_mixed_jd_with_emoji_and_hebrew_lines(self) -> None:
        sections = split_sections(read_fixture("jd_mixed.txt"))
        required = skills_in(sections.required_text)
        preferred = skills_in(sections.preferred_text)
        assert {"Reverse engineering", "Operating systems", "CS / EE degree", "English"} <= required
        assert {"CTF", "Kubernetes", "AWS"} <= preferred

    def test_fallback_without_headings_uses_sentences(self) -> None:
        sections = split_sections("We need someone with Golang and Docker. Kafka is a plus.")
        assert sections.fallback
        assert skills_in(sections.required_text) == {"Go", "Docker / containers"}
        assert skills_in(sections.preferred_text) == {"Distributed systems"}

    def test_empty_jd(self) -> None:
        sections = split_sections("")
        assert sections.required_text == ""
        assert sections.preferred_text == ""


class TestYears:
    def test_english(self) -> None:
        assert extract_years("3+ years of experience, 5 years preferred") == 5

    def test_hebrew(self) -> None:
        assert extract_years("לפחות 2 שנות ניסיון") == 2

    def test_none(self) -> None:
        assert extract_years("no experience needed") is None

    def test_years_left_in_degree_is_not_experience(self) -> None:
        assert extract_years("B.Sc. student with at least 2 years left, experience with C") is None
