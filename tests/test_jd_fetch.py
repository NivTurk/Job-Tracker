"""Extracting JD text from posting pages (no network: HTML strings and a mock transport)."""

import json

import httpx

from app.services.jd_fetch import extract_jd_from_html, fetch_jd

LONG_DESCRIPTION = (
    "<p>We build network security products.</p>"
    "<h3>Requirements</h3><ul><li>Strong C/C++</li><li>Linux internals</li><li>Networking</li></ul>"
    "<h3>Advantage</h3><ul><li>Python</li></ul>"
    + "<p>" + "More details about the team and the work. " * 10 + "</p>"
)


def page_with_json_ld() -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Security Research Student",
        "description": LONG_DESCRIPTION,
        "hiringOrganization": {"@type": "Organization", "name": "Check Point"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}},
    }
    return (
        "<html><head><title>Jobs</title>"
        f'<script type="application/ld+json">{json.dumps(posting)}</script></head>'
        "<body><nav>Home Careers</nav><div>cookie banner</div></body></html>"
    )


def test_json_ld_job_posting() -> None:
    result = extract_jd_from_html(page_with_json_ld())
    assert result.status == "fetched"
    assert result.title == "Security Research Student"
    assert result.company == "Check Point"
    assert result.location == "Tel Aviv, IL"
    assert "- Strong C/C++" in result.text
    assert "cookie banner" not in result.text


def test_plain_html_uses_main_content() -> None:
    html = (
        "<html><head><title>Firmware Student</title><script>var x=1;</script></head><body>"
        "<header>Logo Menu</header>"
        f"<main><h1>Firmware Student</h1>{LONG_DESCRIPTION}</main>"
        "<footer>Copyright</footer></body></html>"
    )
    result = extract_jd_from_html(html)
    assert result.status == "fetched"
    assert "Requirements" in result.text
    assert "Logo Menu" not in result.text
    assert "var x" not in result.text


def test_login_wall_fails() -> None:
    html = "<html><body><main><p>Please sign in to view this job.</p>" + "<p>x</p>" * 3 + "</main></body></html>"
    result = extract_jd_from_html(html)
    assert result.status == "failed"


def test_page_without_jd_fails() -> None:
    html = "<html><body><main>" + "<p>Our product is great and our customers love it.</p>" * 20 + "</main></body></html>"
    result = extract_jd_from_html(html)
    assert result.status == "failed"
    assert "doesn't look like a job description" in (result.reason or "")


def test_linkedin_is_never_fetched() -> None:
    result = fetch_jd("https://www.linkedin.com/jobs/view/123")
    assert result.status == "failed"
    assert "LinkedIn" in (result.reason or "")


def test_http_errors_are_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_jd("https://jobs.example.com/1", client=client)
    assert result.status == "failed"
    assert "404" in (result.reason or "")


def test_successful_fetch_through_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page_with_json_ld(), headers={"content-type": "text/html; charset=utf-8"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_jd("https://jobs.example.com/1", client=client)
    assert result.status == "fetched"
    assert result.company == "Check Point"


def test_bad_url() -> None:
    assert fetch_jd("not a url").status == "failed"
