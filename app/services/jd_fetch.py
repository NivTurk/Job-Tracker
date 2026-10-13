"""Fetch a job posting URL and pull out the job description text.

Strategy:
1. Structured data: most ATS-hosted postings (Greenhouse, Lever, Comeet,
   company career sites) embed a schema.org JobPosting in JSON-LD.
2. Otherwise, strip navigation/scripts and take the largest content block.
3. If the page needs a login, is too short, or doesn't read like a JD, report
   failure so the UI asks the user to paste the text instead.

LinkedIn is never fetched: its terms forbid scraping. The user pastes the text.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

FetchStatus = Literal["fetched", "failed"]

MIN_JD_CHARS = 300
TIMEOUT_SECONDS = 15
MAX_BYTES = 3 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

_BLOCKED_HOSTS = ("linkedin.com",)
_JD_HINTS = re.compile(
    r"requirements|qualifications|responsibilities|experience|what you|about the role|"
    r"job description|skills|דרישות|ניסיון|תיאור התפקיד|תיאור המשרה|כישורים|אחריות",
    re.IGNORECASE,
)
_LOGIN_HINTS = re.compile(
    r"sign in to (view|continue|see)|log in to (view|continue|see)|please (sign|log) in|"
    r"join now to see|create an account to|התחבר(ו)? כדי",
    re.IGNORECASE,
)
_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul", "ol"}


@dataclass
class FetchResult:
    status: FetchStatus
    text: str = ""
    title: str | None = None
    company: str | None = None
    location: str | None = None
    reason: str | None = None


def fetch_jd(url: str, client: httpx.Client | None = None) -> FetchResult:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return FetchResult("failed", reason="That doesn't look like a web address.")
    host = parsed.netloc.lower()
    if any(host == blocked or host.endswith("." + blocked) for blocked in _BLOCKED_HOSTS):
        return FetchResult(
            "failed",
            reason="LinkedIn pages are not fetched (their terms forbid scraping). Paste the job text instead.",
        )

    owns_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,he;q=0.8"},
    )
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return FetchResult("failed", reason=f"The page could not be loaded ({type(exc).__name__}).")
    finally:
        if owns_client:
            client.close()

    if response.status_code in (401, 403):
        return FetchResult("failed", reason="The site refused the request or needs a login. Paste the text instead.")
    if response.status_code >= 400:
        return FetchResult("failed", reason=f"The page returned HTTP {response.status_code}.")
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return FetchResult("failed", reason=f"The link is not a web page ({content_type or 'unknown type'}).")
    if len(response.content) > MAX_BYTES:
        return FetchResult("failed", reason="The page is unusually large; paste the text instead.")

    return extract_jd_from_html(response.text)


def extract_jd_from_html(html: str) -> FetchResult:
    soup = BeautifulSoup(html, "html.parser")

    posting = _find_job_posting(soup)
    if posting is not None:
        description_html = str(posting.get("description") or "")
        text = html_to_text(BeautifulSoup(description_html, "html.parser"))
        if len(text) >= MIN_JD_CHARS:
            return FetchResult(
                "fetched",
                text=text,
                title=_clean_str(posting.get("title")),
                company=_posting_company(posting),
                location=_posting_location(posting),
            )

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe", "button"]):
        tag.decompose()
    page_title = soup.title.get_text(strip=True) if soup.title else None
    root = _main_content(soup)
    text = html_to_text(root) if root is not None else ""

    if _LOGIN_HINTS.search(text) and len(text) < 2000:
        return FetchResult("failed", reason="The page asks to sign in. Paste the job text instead.")
    if len(text) < MIN_JD_CHARS:
        return FetchResult("failed", reason="Couldn't find a job description on that page. Paste the text instead.")
    if not _JD_HINTS.search(text):
        return FetchResult("failed", reason="The page doesn't look like a job description. Paste the text instead.")
    return FetchResult("fetched", text=text, title=page_title)


def html_to_text(root: Tag | BeautifulSoup) -> str:
    """Convert HTML to plain text, keeping line breaks and list bullets."""
    for item in root.find_all("li"):
        item.insert(0, "- ")
    for tag in root.find_all(_BLOCK_TAGS):
        tag.insert_after("\n")
    text = root.get_text()
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if line in ("", "-"):
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _find_job_posting(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for candidate in _iter_ld_objects(data):
            kind = candidate.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                return candidate
    return None


def _iter_ld_objects(data: object):  # type: ignore[no-untyped-def]
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_objects(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_ld_objects(data["@graph"])


def _clean_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return BeautifulSoup(value, "html.parser").get_text(strip=True)
    return None


def _posting_company(posting: dict) -> str | None:
    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean_str(org.get("name"))
    return _clean_str(org)


def _posting_location(posting: dict) -> str | None:
    locations = posting.get("jobLocation")
    if isinstance(locations, dict):
        locations = [locations]
    if not isinstance(locations, list):
        return None
    for location in locations:
        address = location.get("address") if isinstance(location, dict) else None
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressCountry")]
            names = [part.get("name") if isinstance(part, dict) else part for part in parts]
            joined = ", ".join(str(name) for name in names if name)
            if joined:
                return joined
    return None


def _main_content(soup: BeautifulSoup) -> Tag | None:
    """<main> or <article> when the page has one, else the whole body.

    Taking the whole body can pull in some page chrome, but it never drops a
    section of the JD; the fetched text is shown for review before saving.
    """
    for selector in ("main", "article", "[role=main]"):
        found = soup.select_one(selector)
        if found is not None and len(found.get_text(strip=True)) >= MIN_JD_CHARS:
            return found
    return soup.body
