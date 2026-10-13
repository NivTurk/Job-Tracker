"""End-to-end through the HTTP layer, with the real Alembic migrations."""

import re

from fastapi.testclient import TestClient
from helpers import SAMPLE_CV_LINES, make_docx, make_pdf, read_fixture


def add_application(client: TestClient, **overrides: str) -> str:
    data = {
        "company": "NVIDIA",
        "title": "Embedded SW Student",
        "location": "Yokneam",
        "source": "company_site",
        "jd_text": read_fixture("jd_english.txt"),
        "initial_stage": "applied",
        "applied_on": "2026-01-05",
        **overrides,
    }
    response = client.post(
        "/applications",
        data=data,
        files={"cv_file": ("cv.docx", make_docx(SAMPLE_CV_LINES), "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_add_application_with_cv_and_see_score(client: TestClient) -> None:
    url = add_application(client)
    page = client.get(url).text
    assert "Embedded SW Student" in page
    assert re.search(r'class="value">\d+<', page), "deterministic score shown"
    assert "Linux kernel" in page  # missing required skill listed
    assert "Added as" in page  # timeline


def test_list_filters_and_partial(client: TestClient) -> None:
    add_application(client)
    add_application(client, company="Wiz", title="Security Research Student", initial_stage="saved")
    full = client.get("/applications").text
    assert "NVIDIA" in full and "Wiz" in full
    saved_only = client.get(
        "/applications?stage=saved", headers={"HX-Request": "true", "HX-Target": "app-table"}
    ).text
    assert saved_only.lstrip().startswith('<div id="app-table">')
    assert "Wiz" in saved_only and "NVIDIA" not in saved_only
    by_company = client.get("/applications?company=nvid").text
    assert "Wiz" not in by_company.split('id="app-table"')[1]


def test_missing_jd_asks_for_text(client: TestClient) -> None:
    response = client.post("/applications", data={"company": "X", "title": "Y", "jd_text": ""})
    assert response.status_code == 422
    assert "Paste the job description" in response.text


def test_duplicate_warning_and_override(client: TestClient) -> None:
    add_application(client)
    data = {"company": "Nvidia Ltd", "title": "Embedded Software Student", "jd_text": "Requirements:\n- C",
            "initial_stage": "applied"}
    response = client.post("/applications", data=data)
    assert response.status_code == 422
    assert "Possible duplicate" in response.text
    response = client.post("/applications", data={**data, "confirm_duplicate": "1"}, follow_redirects=False)
    assert response.status_code == 303


def test_stage_change_round_trip_and_undo(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    htmx = {"HX-Request": "true"}
    response = client.post(f"/applications/{app_id}/stage", data={"to_stage": "hr_screen", "note": "call"}, headers=htmx)
    assert response.status_code == 200
    assert 'id="stage-badge" hx-swap-oob="true"' in response.text
    response = client.post(f"/applications/{app_id}/stage/undo", headers=htmx)
    assert "(undone)" in response.text
    response = client.post(f"/applications/{app_id}/stage", data={"to_stage": ""}, headers=htmx)
    assert response.status_code == 422
    assert "Choose a stage" in response.text


def test_upload_new_version_and_download(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    response = client.post(
        f"/applications/{app_id}/cvs",
        data={"cv_label": "pdf version"},
        files={"cv_file": ("cv.pdf", make_pdf(SAMPLE_CV_LINES), "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(url).text
    assert "v2" in page and "pdf version" in page
    download = re.search(r'href="(/cvs/\d+/download)"', page)
    assert download
    file_response = client.get(download.group(1))
    assert file_response.status_code == 200
    assert file_response.content[:4] in (b"%PDF", b"PK\x03\x04")


def test_scanned_cv_is_flagged_not_scored(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    client.post(
        f"/applications/{app_id}/cvs",
        files={"cv_file": ("scan.pdf", make_pdf([], blank=True), "application/pdf")},
    )
    page = client.get(url).text
    assert "scanned or image-only" in page


def test_unsupported_upload(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    response = client.post(f"/applications/{app_id}/cvs", files={"cv_file": ("cv.txt", b"hello", "text/plain")})
    assert response.status_code == 422
    assert "Only PDF and DOCX" in response.text


def test_edit_and_archive(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    response = client.post(
        f"/applications/{app_id}/edit",
        data={"company": "NVIDIA", "title": "Embedded SW Student", "jd_text": "Requirements:\n- Rust",
              "source": "referral", "notes": "referred by a friend"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "referred by a friend" in client.get(url).text
    client.post(f"/applications/{app_id}/archive")
    assert "Embedded SW Student" not in client.get("/applications").text.split('id="app-table"')[1]
    assert "Embedded SW Student" in client.get("/applications?archived=1").text


def test_settings_save_and_base_cv(client: TestClient) -> None:
    response = client.post("/settings", data={"target_companies": "Wiz\nNVIDIA", "preferred_locations": "Tel Aviv"},
                           follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/settings").text
    assert "Wiz\nNVIDIA" in page
    response = client.post(
        "/settings/base-cv",
        data={"role": "systems"},
        files={"cv_file": ("systems.docx", make_docx(SAMPLE_CV_LINES), "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "systems.docx" in client.get("/settings").text


def test_cross_origin_post_is_refused(client: TestClient) -> None:
    url = add_application(client)
    app_id = url.rsplit("/", 1)[1]
    response = client.post(f"/applications/{app_id}/archive", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_unknown_host_is_refused(client: TestClient) -> None:
    response = client.get("/applications", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_not_found_page(client: TestClient) -> None:
    response = client.get("/applications/9999")
    assert response.status_code == 404
    assert "Application not found" in response.text


def test_hebrew_text_round_trips(client: TestClient) -> None:
    url = add_application(client, company="אלביט מערכות", title="סטודנט/ית Embedded",
                          jd_text=read_fixture("jd_hebrew.txt"))
    page = client.get(url).text
    assert "אלביט מערכות" in page
    assert "דרישות התפקיד" in page
    assert 'dir="auto"' in page


def test_data_survives_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.config import Config
    from app.main import create_app

    config = Config(data_dir=tmp_path / "persist", allowed_hosts="testserver")
    with TestClient(create_app(config)) as first:
        url = add_application(first)
    with TestClient(create_app(config)) as second:
        page = second.get(url).text
        assert "Embedded SW Student" in page
        download = re.search(r'href="(/cvs/\d+/download)"', page)
        assert download and second.get(download.group(1)).status_code == 200
