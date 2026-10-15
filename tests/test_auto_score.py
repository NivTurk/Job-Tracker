"""Background AI scoring after a CV is attached or the JD changes. The API is mocked."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_ai import FakeAiClient, api_down, reply, valid_payload
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from helpers import SAMPLE_CV_LINES, make_docx, read_fixture
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ai_scores import make_application, make_cv
from test_web import add_application

from app.config import Config
from app.main import create_app
from app.models import AiCall
from app.services.ai_scores import AiConfig, ai_view
from app.services.auto_score import AutoScorer
from app.services.cv_store import attach_cv
from app.services.settings import set_setting

HX = {"HX-Request": "true"}


@pytest.fixture
def fake() -> FakeAiClient:
    return FakeAiClient()


@pytest.fixture
def web(tmp_path: Path, fake: FakeAiClient) -> Iterator[TestClient]:
    config = Config(data_dir=tmp_path / "appdata", allowed_hosts="testserver")
    with TestClient(create_app(config, ai_client=fake)) as test_client:
        yield test_client


def call_count(web: TestClient) -> int:
    factory = web.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        return session.scalar(select(func.count(AiCall.id))) or 0


def edit_form(web: TestClient, url: str, **changes: str) -> dict[str, str]:
    data = {
        "company": "NVIDIA", "title": "Embedded SW Student", "location": "Yokneam", "source": "company_site",
        "jd_text": read_fixture("jd_english.txt"), "applied_on": "2026-01-05", "jd_fetch_status": "manual",
    }
    data.update(changes)
    return data


# --- Through the HTTP layer -------------------------------------------------------------
# Starlette's TestClient runs background tasks before the response returns, so the
# score is already stored when the next request is made.


def test_new_application_with_cv_is_scored(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(reply(valid_payload(score=88)))
    url = add_application(web)
    assert len(fake.calls) == 1
    page = web.get(url).text
    assert "Re-score" in page
    assert re.search(r"AI</dt>\s*<dd>88<", page)
    assert "AI 88" in web.get("/applications").text


def test_auto_score_off(web: TestClient, fake: FakeAiClient) -> None:
    assert web.post("/settings/ai", data={}, follow_redirects=False).status_code == 303
    assert 'name="auto_score" value="1" >' in web.get("/settings").text.replace("  ", " ")
    add_application(web)
    assert fake.calls == []


def test_setting_round_trip(web: TestClient) -> None:
    web.post("/settings/ai", data={})
    assert "checked" not in web.get("/settings").text.split('name="auto_score"')[1][:40]
    web.post("/settings/ai", data={"auto_score": "1"})
    assert "checked" in web.get("/settings").text.split('name="auto_score"')[1][:40]


def test_new_cv_version_is_scored(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(reply(valid_payload(score=60)), reply(valid_payload(score=75)))
    url = add_application(web)
    upload = web.post(
        f"{url}/cvs",
        files={"cv_file": ("v2.docx", make_docx([*SAMPLE_CV_LINES, "Linux kernel module for packet capture, 2025"]),
                           "application/octet-stream")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert len(fake.calls) == 2
    assert "AI 75" in web.get("/applications").text  # the new version became the sent one


def test_jd_edit_rescores_sent_version_only(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(reply(valid_payload(score=60)))
    url = add_application(web)
    fake.queue(reply(valid_payload(score=55)))
    new_jd = read_fixture("jd_english.txt") + "\nRust is an advantage."
    response = web.post(f"{url}/edit", data=edit_form(web, url, jd_text=new_jd), follow_redirects=False)
    assert response.status_code == 303, response.text
    assert len(fake.calls) == 2
    assert "Rust is an advantage." in fake.calls[1]["messages"][0]["content"]


def test_edit_without_jd_change_costs_nothing(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(reply(valid_payload()))
    url = add_application(web)
    response = web.post(f"{url}/edit", data=edit_form(web, url, location="Tel Aviv"), follow_redirects=False)
    assert response.status_code == 303
    assert len(fake.calls) == 1


def test_cached_pair_is_not_scored_again(web: TestClient, fake: FakeAiClient) -> None:
    """Same CV file and same JD in a second application: the cached result is reused."""
    fake.queue(reply(valid_payload(score=70)))
    add_application(web)
    add_application(web, title="Embedded SW Student (Haifa)", confirm_duplicate="1")
    assert len(fake.calls) == 1
    assert web.get("/applications").text.count("AI 70") == 2


def test_api_down_in_background_leaves_app_working(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(api_down())
    url = add_application(web)
    page = web.get(url)
    assert page.status_code == 200
    assert "Score with AI" in page.text
    assert "Could not connect to the Claude API." in page.text
    assert call_count(web) == 1


def test_unexpected_error_in_background_is_contained(web: TestClient, fake: FakeAiClient) -> None:
    fake.queue(RuntimeError("boom"))
    url = add_application(web)
    assert web.get(url).status_code == 200
    scorer = web.app.state.auto_scorer  # type: ignore[attr-defined]
    assert scorer.running_keys() == frozenset()


def test_no_api_key_means_no_auto_scorer(client: TestClient) -> None:
    assert client.app.state.auto_scorer is None  # type: ignore[attr-defined]
    add_application(client)  # works, nothing to call


# --- Running state and polling ------------------------------------------------------


def test_running_state_polls_then_refreshes(web: TestClient, fake: FakeAiClient) -> None:
    web.post("/settings/ai", data={})  # add without scoring, then schedule by hand
    url = add_application(web)
    web.post("/settings/ai", data={"auto_score": "1"})
    app_id = int(url.rsplit("/", 1)[1])
    scorer: AutoScorer = web.app.state.auto_scorer  # type: ignore[attr-defined]

    factory = web.app.state.session_factory  # type: ignore[attr-defined]
    background = BackgroundTasks()
    with factory() as session:
        from app.models import Application

        application = session.get(Application, app_id)
        assert application is not None
        version = application.sent_cv
        assert scorer.schedule(session, background, application, version.cv_file)
        assert not scorer.schedule(session, BackgroundTasks(), application, version.cv_file)  # already running
        vid = version.id

    page = web.get(url).text
    assert "Scoring in the background" in page
    assert 'hx-trigger="every 3s"' in page
    assert "Score with AI" not in page  # no second call while one runs
    assert "scoring..." in web.get("/applications").text

    still_running = web.get(f"{url}/score?cv={vid}&poll=1", headers=HX)
    assert "HX-Refresh" not in still_running.headers
    assert 'hx-trigger="every 3s"' in still_running.text

    fake.queue(reply(valid_payload(score=91)))
    import asyncio

    asyncio.run(background())
    assert scorer.running_keys() == frozenset()

    done = web.get(f"{url}/score?cv={vid}&poll=1", headers=HX)
    assert done.headers.get("HX-Refresh") == "true"
    assert re.search(r"AI</dt>\s*<dd>91<", web.get(url).text)


# --- AutoScorer directly ------------------------------------------------------------


def _scorer(session: Session, fake: FakeAiClient) -> AutoScorer:
    bind = session.get_bind()
    from app.db import make_session_factory

    return AutoScorer(make_session_factory(bind), fake)  # type: ignore[arg-type]


def test_skips_unscorable_cv(session: Session, fake: FakeAiClient) -> None:
    application = make_application(session)
    cv = make_cv(session, text="", text_ok=False)
    attach_cv(session, application, cv, None)
    session.commit()
    assert not _scorer(session, fake).schedule(session, BackgroundTasks(), application, cv)


def test_respects_setting(session: Session, fake: FakeAiClient) -> None:
    application = make_application(session)
    cv = make_cv(session)
    session.commit()
    set_setting(session, "ai", {"auto_score": False})
    assert not _scorer(session, fake).schedule(session, BackgroundTasks(), application, cv)


def test_jd_changed_before_job_ran_is_skipped(session: Session, fake: FakeAiClient) -> None:
    application = make_application(session)
    cv = make_cv(session)
    attach_cv(session, application, cv, None)
    session.commit()
    scorer = _scorer(session, fake)
    background = BackgroundTasks()
    assert scorer.schedule(session, background, application, cv)
    application.jd_text += "\nChanged."
    session.commit()

    import asyncio

    asyncio.run(background())
    assert fake.calls == []
    assert scorer.running_keys() == frozenset()


def test_job_stores_result(session: Session, fake: FakeAiClient) -> None:
    application = make_application(session)
    cv = make_cv(session)
    attach_cv(session, application, cv, None)
    session.commit()
    scorer = _scorer(session, fake)
    background = BackgroundTasks()
    scorer.schedule(session, background, application, cv)
    fake.queue(reply(valid_payload(score=67)))

    import asyncio

    asyncio.run(background())
    session.expire_all()
    view = ai_view(session, application, cv, AiConfig(model=fake.model, enabled=True))
    assert (view.status, view.score) == ("ok", 67)
