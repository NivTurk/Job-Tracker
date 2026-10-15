"""AI scoring through the HTTP layer, with a fake client and the real migrations."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_ai import FakeAiClient, api_down, reply, valid_payload
from fastapi.testclient import TestClient
from test_web import add_application

from app.config import Config
from app.main import create_app

HX = {"HX-Request": "true", "HX-Target": "score-panel"}


@pytest.fixture
def fake() -> FakeAiClient:
    return FakeAiClient()


@pytest.fixture
def ai_client(tmp_path: Path, fake: FakeAiClient) -> Iterator[TestClient]:
    config = Config(data_dir=tmp_path / "appdata", allowed_hosts="testserver")
    with TestClient(create_app(config, ai_client=fake)) as test_client:
        # These tests drive the button by hand; auto-scoring has its own tests.
        test_client.post("/settings/ai", data={})
        yield test_client


def version_id(page: str) -> str:
    match = re.search(r"/cvs/(\d+)/ai-score", page)
    assert match, "AI score button present"
    return match.group(1)


def big_value(page: str) -> str:
    match = re.search(r'class="value">([^<]+)<', page)
    assert match
    return match.group(1)


def test_no_api_key_means_no_button_and_tracker_works(client: TestClient) -> None:
    url = add_application(client)
    page = client.get(url).text
    assert "ai-score" not in page
    assert "ANTHROPIC_API_KEY" in page
    assert "deterministic only" in page
    assert re.search(r'class="value">\d+<', page)
    assert "pending" in client.get("/applications").text


def test_score_with_ai_shows_both_subscores_and_combined(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    page = ai_client.get(url).text
    assert "Score with AI" in page
    deterministic = int(big_value(page))  # combined falls back to deterministic while AI is pending

    fake.queue(reply(valid_payload(score=90)))
    partial = ai_client.post(f"{url}/cvs/{version_id(page)}/ai-score", headers=HX)
    assert partial.status_code == 200
    html = partial.text
    assert html.lstrip().startswith("<section")  # just the panel, for the htmx swap
    assert int(big_value(html)) == int(0.5 * deterministic + 0.5 * 90 + 0.5)
    assert "combined" in html
    assert "Re-score" in html
    assert "Linux kernel development" in html  # missing must-have
    assert "List C before Python." in html  # edit suggestion
    assert "Network traffic analyzer in Python" in html  # evidence

    listing = ai_client.get("/applications").text
    assert "AI 90" in listing
    assert f"D {deterministic}" in listing


def test_non_htmx_post_redirects(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    vid = version_id(ai_client.get(url).text)
    fake.queue(reply(valid_payload()))
    response = ai_client.post(f"{url}/cvs/{vid}/ai-score", follow_redirects=False)
    assert response.status_code == 303
    assert "AI review" in ai_client.get(url).text


def test_api_down_shows_pending_and_page_still_works(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    page = ai_client.get(url).text
    fake.queue(api_down())
    html = ai_client.post(f"{url}/cvs/{version_id(page)}/ai-score", headers=HX).text
    assert "AI scoring failed: Could not connect to the Claude API." in html
    assert "deterministic only" in html
    assert re.search(r'class="value">\d+<', html)

    again = ai_client.get(url)
    assert again.status_code == 200
    assert "pending" in again.text
    assert "Could not connect" in again.text  # last failure is shown
    assert ai_client.get("/applications").status_code == 200


def test_invalid_twice_shows_error_note(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    page = ai_client.get(url).text
    fake.queue(reply("nope"), reply("still nope"))
    html = ai_client.post(f"{url}/cvs/{version_id(page)}/ai-score", headers=HX).text
    assert "failed validation twice" in html
    assert "deterministic only" in html


def test_unknown_version_is_404(ai_client: TestClient) -> None:
    url = add_application(ai_client)
    assert ai_client.post(f"{url}/cvs/9999/ai-score").status_code == 404


def test_cross_origin_post_refused(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    vid = version_id(ai_client.get(url).text)
    response = ai_client.post(f"{url}/cvs/{vid}/ai-score", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert fake.calls == []


def test_ai_share_setting_changes_combined_score(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    page = ai_client.get(url).text
    deterministic = int(big_value(page))
    fake.queue(reply(valid_payload(score=100)))
    ai_client.post(f"{url}/cvs/{version_id(page)}/ai-score", headers=HX)

    response = ai_client.post("/settings/scoring", data={"ai_share_percent": "20"}, follow_redirects=False)
    assert response.status_code == 303
    assert int(big_value(ai_client.get(url).text)) == int(0.8 * deterministic + 0.2 * 100 + 0.5)

    settings = ai_client.get("/settings").text
    assert 'value="20"' in settings
    assert "80% deterministic, 20% AI" in settings


@pytest.mark.parametrize("value", ["", "abc", "-1", "101", "50.5"])
def test_ai_share_validation(ai_client: TestClient, value: str) -> None:
    response = ai_client.post("/settings/scoring", data={"ai_share_percent": value})
    assert response.status_code == 422
    assert "0 to 100" in response.text


def test_settings_shows_usage_and_recent_calls(ai_client: TestClient, fake: FakeAiClient) -> None:
    url = add_application(ai_client)
    vid = version_id(ai_client.get(url).text)
    fake.queue(reply("bad"), reply(valid_payload()))
    ai_client.post(f"{url}/cvs/{vid}/ai-score", headers=HX)
    page = ai_client.get("/settings").text
    assert "claude-sonnet-5-5" in page
    assert "$0.0320" in page  # two calls at 4000 in / 800 out on Sonnet
    assert "invalid_json" in page and "(retry)" in page


def test_existing_scores_survive_new_cv_version(ai_client: TestClient, fake: FakeAiClient) -> None:
    """Uploading another version doesn't touch the first version's cached AI score."""
    url = add_application(ai_client)
    page = ai_client.get(url).text
    first = version_id(page)
    fake.queue(reply(valid_payload(score=61)))
    ai_client.post(f"{url}/cvs/{first}/ai-score", headers=HX)

    from helpers import make_docx

    upload = ai_client.post(
        f"{url}/cvs",
        files={"cv_file": ("cv2.docx", make_docx(["Different CV", "a@b.com", "054-1234567", "2024", "C++ Linux"]),
                           "application/octet-stream")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    first_panel = ai_client.get(f"{url}/score?cv={first}").text
    assert "Re-score" in first_panel
    assert re.search(r"AI</dt>\s*<dd>61<", first_panel)
