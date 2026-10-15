"""AI scoring service: retry path, fallback, caching and call logging. The API is mocked."""

import json
from decimal import Decimal

import pytest
from fake_ai import FakeAiClient, api_down, reply, valid_payload
from helpers import SAMPLE_CV_LINES, read_fixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import RETRY_MAX_TOKENS, AiApiError
from app.ai.prompt import PROMPT_VERSION
from app.models import AiCall, AiScore, Application, CvFile
from app.services.ai_scores import AiConfig, ai_view, cache_key, run_score, usage_since
from app.services.applications import ApplicationInput, create_application
from app.services.cv_store import attach_cv
from app.services.scores import score_all_versions
from app.services.settings import DEFAULTS, set_setting

CONFIG = AiConfig(model="claude-sonnet-5-5", enabled=True)


def make_cv(session: Session, text: str | None = None, sha: str = "a" * 64, text_ok: bool = True) -> CvFile:
    text = "\n".join(SAMPLE_CV_LINES) if text is None else text
    cv = CvFile(
        sha256=sha, original_name="cv.docx", stored_path=f"cvs/{sha}.docx", mime_type="application/docx",
        size_bytes=1000, page_count=1, text=text, text_ok=text_ok,
        format_report={"text_ok": text_ok, "warnings": []},
    )
    session.add(cv)
    session.flush()
    return cv


def make_application(session: Session, jd_text: str | None = None, title: str = "Embedded SW Student") -> Application:
    jd_text = read_fixture("jd_english.txt") if jd_text is None else jd_text
    return create_application(session, ApplicationInput(company="NVIDIA", title=title, jd_text=jd_text))


@pytest.fixture
def setup(session: Session) -> tuple[Application, CvFile]:
    application = make_application(session)
    cv = make_cv(session)
    attach_cv(session, application, cv, None)
    session.flush()
    return application, cv


def calls(session: Session) -> list[AiCall]:
    return list(session.scalars(select(AiCall).order_by(AiCall.id)))


# --- Happy path and caching -----------------------------------------------------


def test_valid_reply_is_stored_and_logged(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply(valid_payload(score=81)))
    view = run_score(session, client, application, cv)

    assert view.status == "ok"
    assert view.score == 81
    assert view.result is not None and len(view.result.edit_suggestions) == 3

    row = session.scalar(select(AiScore))
    assert row is not None and row.status == "ok" and row.score == 81
    assert row.cache_key == cache_key(cv.text, application.jd_text, "claude-sonnet-5-5")
    assert row.prompt_version == PROMPT_VERSION

    [call] = calls(session)
    assert call.outcome == "ok" and call.attempt == 1
    assert call.application_id == application.id
    assert (call.input_tokens, call.output_tokens) == (4000, 800)
    assert call.est_cost_usd == Decimal("0.016000")
    assert call.request_id == "req_test"


def test_prompt_contains_jd_and_cv(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply(valid_payload()))
    run_score(session, client, application, cv)
    sent = client.calls[0]
    content = sent["messages"][0]["content"]
    assert "<job_description>" in content and "Scapy" in content
    assert sent["schema"]["required"]
    assert "Always answer in English" in sent["system"]


def test_cached_result_is_read_without_calling(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    run_score(session, FakeAiClient(reply(valid_payload(score=70))), application, cv)
    # Reading never calls the API: FakeAiClient with no replies would raise.
    view = ai_view(session, application, cv, CONFIG)
    assert (view.status, view.score) == ("ok", 70)
    assert len(calls(session)) == 1


def test_rescore_overwrites_cache(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply(valid_payload(score=70)), reply(valid_payload(score=85)))
    run_score(session, client, application, cv)
    view = run_score(session, client, application, cv)
    assert view.score == 85
    assert len(list(session.scalars(select(AiScore)))) == 1
    assert len(calls(session)) == 2


def test_cache_key_changes_with_each_input() -> None:
    base = cache_key("cv", "jd", "claude-sonnet-5-5", "score-v1")
    assert cache_key("cv", "jd", "claude-sonnet-5-5", "score-v1") == base
    assert cache_key("cv2", "jd", "claude-sonnet-5-5", "score-v1") != base
    assert cache_key("cv", "jd2", "claude-sonnet-5-5", "score-v1") != base
    assert cache_key("cv", "jd", "claude-haiku-5-5", "score-v1") != base
    assert cache_key("cv", "jd", "claude-sonnet-5-5", "score-v2") != base
    # The separator stops "ab" + "c" from colliding with "a" + "bc".
    assert cache_key("ab", "c", "m", "v") != cache_key("a", "bc", "m", "v")


def test_other_model_or_edited_jd_is_a_cache_miss(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    run_score(session, FakeAiClient(reply(valid_payload())), application, cv)
    assert ai_view(session, application, cv, AiConfig(model="claude-haiku-5-5", enabled=True)).status == "pending"
    application.jd_text += "\nAlso: Rust is an advantage."
    assert ai_view(session, application, cv, CONFIG).status == "pending"


def test_same_cv_and_jd_share_one_result_across_applications(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    other = make_application(session, title="Embedded SW Student (Haifa)")
    attach_cv(session, other, cv, None)
    run_score(session, FakeAiClient(reply(valid_payload(score=66))), application, cv)
    assert ai_view(session, other, cv, CONFIG).score == 66


# --- Retry path --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad", "outcome"),
    [
        (reply("not json at all"), "invalid_json"),
        (reply(valid_payload(score=140)), "out_of_range"),
        (reply(valid_payload(edit_suggestions=[])), "out_of_range"),
        (reply({"score": 70}), "invalid_schema"),
    ],
)
def test_bad_then_good_retries_once_with_feedback(
    session: Session, setup: tuple[Application, CvFile], bad, outcome: str  # type: ignore[no-untyped-def]
) -> None:
    application, cv = setup
    client = FakeAiClient(bad, reply(valid_payload(score=72)))
    view = run_score(session, client, application, cv)

    assert (view.status, view.score) == ("ok", 72)
    first, second = calls(session)
    assert (first.outcome, first.attempt) == (outcome, 1)
    assert (second.outcome, second.attempt) == ("ok", 2)
    assert first.error

    retry_messages = client.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert retry_messages[1]["content"] == bad.text
    assert "failed validation" in retry_messages[2]["content"]


def test_cut_off_reply_is_resent_with_more_tokens(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply('{"score": 7', stop_reason="max_tokens"), reply(valid_payload()))
    assert run_score(session, client, application, cv).status == "ok"
    assert client.calls[1]["max_tokens"] == RETRY_MAX_TOKENS
    assert len(client.calls[1]["messages"]) == 1  # the original request, not a correction
    assert calls(session)[0].outcome == "max_tokens"


def test_refusal_is_resent_unchanged(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply("", stop_reason="refusal"), reply(valid_payload()))
    assert run_score(session, client, application, cv).status == "ok"
    assert client.calls[1]["messages"] == client.calls[0]["messages"]


def test_two_bad_replies_fall_back_with_error_note(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply("garbage"), reply(valid_payload(score=-5)))
    view = run_score(session, client, application, cv)

    assert view.status == "invalid"
    assert view.score is None
    assert view.message and "failed validation twice" in view.message and "score must be between" in view.message
    assert len(client.calls) == 2  # retried once, not more
    row = session.scalar(select(AiScore))
    assert row is not None and row.status == "invalid" and row.score is None

    scores = score_all_versions(session, application, CONFIG)
    [score] = scores.values()
    assert score.combined.basis == "deterministic"
    assert score.value == score.deterministic.score


def test_failed_rescore_keeps_earlier_good_result(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply(valid_payload(score=74)), reply("bad"), reply("still bad"))
    run_score(session, client, application, cv)
    view = run_score(session, client, application, cv)
    assert (view.status, view.score) == ("ok", 74)
    assert view.last_failure and "failed validation" in view.last_failure


# --- API unavailable -----------------------------------------------------------------


def test_api_down_leaves_score_pending(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(api_down())
    view = run_score(session, client, application, cv)

    assert view.status == "pending"
    assert view.last_failure == "Could not connect to the Claude API."
    assert session.scalar(select(AiScore)) is None
    [call] = calls(session)
    assert call.outcome == "api_error" and call.input_tokens is None and call.est_cost_usd is None
    assert len(client.calls) == 1  # transport retries are the SDK's job, not ours

    [score] = score_all_versions(session, application, CONFIG).values()
    assert score.ai.status == "pending"
    assert score.value == score.deterministic.score


def test_timeout_is_logged_as_timeout(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    run_score(session, FakeAiClient(AiApiError("timeout", "timed out")), application, cv)
    assert calls(session)[0].outcome == "timeout"


def test_api_down_on_retry_stores_nothing(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    view = run_score(session, FakeAiClient(reply("bad"), api_down()), application, cv)
    assert view.status == "pending"
    assert session.scalar(select(AiScore)) is None


def test_api_down_after_success_keeps_cached_result(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    client = FakeAiClient(reply(valid_payload(score=77)), api_down())
    run_score(session, client, application, cv)
    view = run_score(session, client, application, cv)
    assert (view.status, view.score) == ("ok", 77)
    assert view.last_failure == "Could not connect to the Claude API."


def test_disabled_ai_shows_pending_with_setup_hint(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    view = ai_view(session, application, cv, AiConfig(enabled=False))
    assert view.status == "pending"
    assert view.message and "ANTHROPIC_API_KEY" in view.message
    assert not view.can_run


# --- Inputs that can't be scored ---------------------------------------------------------


def test_cv_without_text_is_not_sent(session: Session) -> None:
    application = make_application(session)
    cv = make_cv(session, text="", text_ok=False)
    client = FakeAiClient()
    view = run_score(session, client, application, cv)
    assert view.status == "unavailable"
    assert client.calls == []
    assert calls(session) == []


def test_empty_jd_is_not_sent(session: Session) -> None:
    application = make_application(session)
    application.jd_text = "   "
    view = run_score(session, FakeAiClient(), application, make_cv(session))
    assert view.status == "unavailable"


# --- Combined score from stored sub-scores ---------------------------------------------


def test_combined_uses_the_ai_share_setting(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    run_score(session, FakeAiClient(reply(valid_payload(score=90))), application, cv)
    [score] = score_all_versions(session, application, CONFIG).values()
    det = score.deterministic.score
    assert det is not None
    assert score.combined.basis == "both"
    assert score.value == int(0.5 * det + 0.5 * 90 + 0.5)

    set_setting(session, "scoring", {**DEFAULTS["scoring"], "ai_share": 0.25})
    [score] = score_all_versions(session, application, CONFIG).values()
    assert score.value == int(0.75 * det + 0.25 * 90 + 0.5)


def test_result_json_round_trips(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    payload = valid_payload()
    run_score(session, FakeAiClient(reply(payload)), application, cv)
    row = session.scalar(select(AiScore))
    assert row is not None
    assert json.loads(json.dumps(row.result_json)) == payload


def test_usage_summary(session: Session, setup: tuple[Application, CvFile]) -> None:
    application, cv = setup
    run_score(session, FakeAiClient(reply("bad"), reply(valid_payload())), application, cv)
    run_score(session, FakeAiClient(api_down()), application, cv)
    usage = usage_since(session)
    assert usage.calls == 3
    assert usage.failed == 2
    assert usage.input_tokens == 8000
    assert usage.cost_usd == pytest.approx(0.032)
