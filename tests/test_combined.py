"""Combined score: deterministic and AI halves mixed by the ai_share setting."""

import pytest

from app.scoring.combined import combine


def test_even_split() -> None:
    result = combine(60, 80, 0.5)
    assert result.value == 70
    assert result.basis == "both"
    assert result.label == "combined"


def test_rounds_half_up() -> None:
    # 0.5 * 71 + 0.5 * 80 = 75.5
    assert combine(71, 80, 0.5).value == 76


def test_uneven_weight() -> None:
    # 0.7 * 50 + 0.3 * 90 = 62
    assert combine(50, 90, 0.3).value == 62


@pytest.mark.parametrize("share", [0.0, 0.5, 1.0])
def test_missing_ai_falls_back_to_deterministic(share: float) -> None:
    result = combine(64, None, share)
    assert result.value == 64
    assert result.basis == "deterministic"
    assert result.label == "deterministic only"


def test_missing_deterministic_falls_back_to_ai() -> None:
    result = combine(None, 81, 0.5)
    assert (result.value, result.basis) == (81, "ai")


def test_neither() -> None:
    result = combine(None, None, 0.5)
    assert result.value is None
    assert result.basis == "none"


def test_share_zero_ignores_ai() -> None:
    assert combine(40, 100, 0.0).value == 40
    assert combine(40, 100, 0.0).basis == "deterministic"


def test_share_one_ignores_deterministic() -> None:
    assert combine(40, 100, 1.0).value == 100
    assert combine(40, 100, 1.0).basis == "ai"


def test_share_is_clamped() -> None:
    assert combine(40, 100, 1.7).value == 100
    assert combine(40, 100, -0.2).value == 40
