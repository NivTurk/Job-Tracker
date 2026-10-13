"""The deterministic ATS score: reproducibility, weighting, edge cases."""

from helpers import read_fixture

from app.scoring.deterministic import ScoringWeights, compute_deterministic, keyword_score
from app.scoring.matching import MatchResult, SkillMatch

OK_REPORT = {"text_ok": True, "warnings": []}
CV_TEXT = (
    "Niv Example. Skills: C, C++, Python, Linux, Wireshark, TCP/IP, Git, Docker. "
    "B.Sc. Computer Science, Open University. English, Hebrew."
)


def test_same_input_gives_same_score() -> None:
    jd = read_fixture("jd_english.txt")
    first = compute_deterministic(jd, CV_TEXT, OK_REPORT)
    for _ in range(5):
        again = compute_deterministic(jd, CV_TEXT, OK_REPORT)
        assert again.score == first.score
        assert [m.skill for m in again.match.required] == [m.skill for m in first.match.required]


def test_english_jd_breakdown() -> None:
    result = compute_deterministic(read_fixture("jd_english.txt"), CV_TEXT, OK_REPORT)
    assert result.scorable
    assert [m.skill for m in result.missing_required] == ["Linux kernel"]
    assert "Networking" in [m.skill for m in result.implied]  # via TCP/IP
    assert [m.skill for m in result.missing_preferred] == ["Kubernetes"]
    assert result.format_score == 100
    # Required: CS degree, C, C++ matched; Networking implied (0.75); kernel missing.
    # Preferred: Python, Packet analysis matched; Kubernetes missing.
    # (3*3.75 + 1*2) / (3*5 + 1*3) = 13.25 / 18 = 73.6 -> 74
    assert result.keyword_score == 74
    assert result.score == round(0.8 * 74 + 0.2 * 100)


def test_required_skills_weigh_more_than_preferred() -> None:
    def match(required_status: str, preferred_status: str) -> MatchResult:
        return MatchResult(
            required=[SkillMatch("A", required_status, ["a"])],  # type: ignore[arg-type]
            preferred=[SkillMatch("B", preferred_status, ["b"])],  # type: ignore[arg-type]
        )

    weights = ScoringWeights()
    has_required = keyword_score(match("matched", "missing"), weights)
    has_preferred = keyword_score(match("missing", "matched"), weights)
    assert has_required == 75
    assert has_preferred == 25


def test_implied_gets_partial_credit() -> None:
    result = MatchResult(required=[SkillMatch("A", "implied", ["a"])])
    assert keyword_score(result, ScoringWeights(implied_credit=0.5)) == 50


def test_format_warnings_lower_the_score() -> None:
    jd = read_fixture("jd_english.txt")
    clean = compute_deterministic(jd, CV_TEXT, OK_REPORT)
    report = {"text_ok": True, "warnings": [{"code": "x", "severity": "warning", "message": ""}] * 2}
    warned = compute_deterministic(jd, CV_TEXT, report)
    assert warned.format_score == 80
    assert warned.keyword_score == clean.keyword_score
    assert warned.score < clean.score


def test_scanned_cv_is_not_scored_as_zero() -> None:
    report = {"text_ok": False, "warnings": [{"code": "no_text", "severity": "error", "message": ""}]}
    result = compute_deterministic(read_fixture("jd_english.txt"), "", report)
    assert not result.scorable
    assert result.score is None
    assert "scanned" in (result.reason or "")


def test_empty_jd_is_not_scored() -> None:
    result = compute_deterministic("   ", CV_TEXT, OK_REPORT)
    assert not result.scorable
    assert result.score is None


def test_jd_without_known_skills() -> None:
    result = compute_deterministic("Requirements:\n- a positive attitude\n- team player", CV_TEXT, OK_REPORT)
    assert not result.scorable
    assert result.format_score == 100
    assert "synonyms.yaml" in (result.reason or "")


def test_hebrew_jd_scores() -> None:
    result = compute_deterministic(read_fixture("jd_hebrew.txt"), CV_TEXT, OK_REPORT)
    assert result.scorable
    assert result.missing_required == []
    assert "The requirements mention 3+ years of experience." in result.notes


def test_rounding_is_half_up() -> None:
    result = MatchResult(
        required=[SkillMatch("A", "matched", ["a"])],
        preferred=[SkillMatch("B", "missing", ["b"]), SkillMatch("C", "missing", ["c"]),
                   SkillMatch("D", "missing", ["d"]), SkillMatch("E", "missing", ["e"]),
                   SkillMatch("F", "missing", ["f"]), SkillMatch("G", "missing", ["g"]),
                   SkillMatch("H", "matched", ["h"])],
    )
    # (3 + 1) / (3 + 7) = 40.0
    assert keyword_score(result, ScoringWeights()) == 40
    half = MatchResult(required=[SkillMatch("A", "implied", ["a"])] * 2 + [SkillMatch("B", "missing", ["b"])] * 2)
    # 2 * 0.75 / 4 = 37.5 -> 38
    assert keyword_score(half, ScoringWeights()) == 38
