"""Keyword normalization and skill matching."""

import pytest

from app.scoring.matching import Lexicon, Skill, load_lexicon, load_lexicon_from_yaml, match_skills
from app.scoring.normalize import collapse_key, normalize_company, normalize_text, normalize_title


@pytest.fixture(scope="module")
def lexicon() -> Lexicon:
    return load_lexicon()


def found(lexicon: Lexicon, text: str) -> set[str]:
    return set(lexicon.find(text))


class TestNormalize:
    def test_lowercases_and_unifies_dashes_and_quotes(self) -> None:
        assert normalize_text("C++ \u2013 \u201cKernel\u201d") == 'c++ - "kernel"'

    def test_keeps_line_breaks(self) -> None:
        assert normalize_text("A\n  B  ") == "a\nb"

    def test_removes_hebrew_niqqud_and_bidi_marks(self) -> None:
        assert normalize_text("\u200fשָׁלוֹם") == "שלום"

    def test_collapse_key_treats_space_hyphen_underscore_alike(self) -> None:
        assert collapse_key("Real-Time") == collapse_key("real  time") == collapse_key("real_time") == "real time"


class TestSynonyms:
    @pytest.mark.parametrize(
        ("text", "skill"),
        [
            ("cpp", "C++"),
            ("Modern C++17", "C++"),
            ("k8s clusters", "Kubernetes"),
            ("Linux kernel modules", "Linux kernel"),
            ("kernel development", "Linux kernel"),
            ("TCP/IP stack", "TCP/IP"),
            ("golang services", "Go"),
            ("x86_64 assembly", "x86"),
            ("real time systems", "Real-time systems"),
            ("Machine-Learning", "Machine learning"),
        ],
    )
    def test_alias_maps_to_canonical_skill(self, lexicon: Lexicon, text: str, skill: str) -> None:
        assert skill in found(lexicon, text)

    def test_slash_separated_languages(self, lexicon: Lexicon) -> None:
        assert {"C", "C++"} <= found(lexicon, "Strong C/C++ skills")

    def test_c_does_not_match_inside_other_words(self, lexicon: Lexicon) -> None:
        assert "C" not in found(lexicon, "Objective-C, C-level executives, c's, abc, C#")

    def test_c_sharp_is_its_own_skill(self, lexicon: Lexicon) -> None:
        skills = found(lexicon, "C# and .NET")
        assert "C#" in skills
        assert "C" not in skills

    def test_java_is_not_found_inside_javascript(self, lexicon: Lexicon) -> None:
        skills = found(lexicon, "JavaScript and Node.js")
        assert "JavaScript" in skills
        assert "Java" not in skills

    def test_bare_go_is_not_a_skill(self, lexicon: Lexicon) -> None:
        assert "Go" not in found(lexicon, "ready to go the extra mile")

    def test_neural_networks_is_not_networking(self, lexicon: Lexicon) -> None:
        skills = found(lexicon, "neural networks")
        assert "Networking" not in skills
        assert "Deep learning" in skills

    def test_hebrew_terms_with_attached_prefixes(self, lexicon: Lexicon) -> None:
        skills = found(lexicon, "ניסיון בלינוקס ובמערכות הפעלה, ידע ברשתות תקשורת")
        assert {"Linux", "Operating systems", "Networking"} <= skills

    def test_latin_term_after_hebrew_prefix_and_hyphen(self, lexicon: Lexicon) -> None:
        assert {"C", "Python"} <= found(lexicon, "ניסיון ב-C וב-Python")

    def test_find_orders_by_first_appearance(self, lexicon: Lexicon) -> None:
        assert list(lexicon.find("Python, then Linux, then C++")) == ["Python", "Linux", "C++"]


class TestLexiconValidation:
    def test_duplicate_alias_across_skills_is_rejected(self) -> None:
        raw = b"skills:\n  A:\n    aliases: [same]\n  B:\n    aliases: [same]\n"
        with pytest.raises(ValueError, match="both"):
            load_lexicon_from_yaml(raw)

    def test_unknown_implied_skill_is_rejected(self) -> None:
        raw = b"skills:\n  A:\n    aliases: [a1]\n    implies: [Nope]\n"
        with pytest.raises(ValueError, match="unknown"):
            load_lexicon_from_yaml(raw)

    def test_shipped_synonyms_file_loads(self, lexicon: Lexicon) -> None:
        assert len(lexicon.skills) > 50
        assert len(lexicon.version) == 12


class TestImplied:
    def test_implication_is_transitive(self) -> None:
        lexicon = Lexicon(
            {
                "Drivers": Skill("Drivers", ("drivers",), implies=("Kernel",)),
                "Kernel": Skill("Kernel", ("kernel",), implies=("Linux",)),
                "Linux": Skill("Linux", ("linux",)),
            },
            version="test",
        )
        assert lexicon.implied_by({"Drivers"}) == {"Kernel": "Drivers", "Linux": "Drivers"}

    def test_present_skills_are_not_reported_as_implied(self, lexicon: Lexicon) -> None:
        implied = lexicon.implied_by({"TCP/IP", "Networking"})
        assert "Networking" not in implied


class TestMatchSkills:
    def test_matched_implied_and_missing(self, lexicon: Lexicon) -> None:
        result = match_skills(
            required_text="C++, networking, Linux kernel",
            preferred_text="Python",
            cv_text="C++ and TCP/IP. Some Python.",
            lexicon=lexicon,
        )
        statuses = {item.skill: item.status for item in result.required}
        assert statuses == {"C++": "matched", "Networking": "implied", "Linux kernel": "missing"}
        assert [item.status for item in result.preferred] == ["matched"]
        implied = next(item for item in result.required if item.skill == "Networking")
        assert implied.cv_evidence == "TCP/IP"

    def test_skill_in_both_parts_counts_as_required_only(self, lexicon: Lexicon) -> None:
        result = match_skills("Python", "Python, Docker", "nothing", lexicon)
        assert [item.skill for item in result.required] == ["Python"]
        assert [item.skill for item in result.preferred] == ["Docker / containers"]


class TestCompanyAndTitleNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Check Point Software Technologies Ltd.", "check point"),
            ("Microsoft Israel", "microsoft"),
            ("NVIDIA", "nvidia"),
            ("VAST Data", "vast data"),
            ("Wiz, Inc.", "wiz"),
            ('אלביט מערכות בע"מ', "אלביט מערכות"),
            ("Systems", "systems"),  # never stripped down to nothing
        ],
    )
    def test_company(self, raw: str, expected: str) -> None:
        assert normalize_company(raw) == expected

    def test_title_expands_abbreviations(self) -> None:
        assert normalize_title("SW Eng. (Student) - Embedded") == "software engineer student embedded"
