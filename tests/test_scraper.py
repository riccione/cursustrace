"""Tests for the cursustrace web scraper."""

from __future__ import annotations

import curl_cffi
import httpx
import pytest
import trafilatura
from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import HTTPError, Timeout

from cursustrace import scraper
from cursustrace.errors import ScrapeError

OG_HTML = """
<html>
  <head>
    <meta property="og:title" content="Senior Engineer" />
    <meta property="og:site_name" content="Acme Corp" />
    <meta property="og:locality" content="Berlin" />
    <title>Fallback Title</title>
  </head>
  <body><h1>Heading Title</h1><p>Body text here</p></body>
</html>
"""

LONG_BODY = (
    "Reliable engineers ship features and review code across the stack every day. " * 10
).strip()


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _stub_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: "# Clean body")


def _patch_get(
    monkeypatch: pytest.MonkeyPatch,
    html: str,
    status_code: int = 200,
    captured: dict[str, object] | None = None,
) -> None:
    def _get(
        url: str,
        headers: dict[str, str] | None = None,
        impersonate: str | None = None,
        timeout: float | None = None,
        **kwargs: object,
    ) -> _FakeResponse:
        if captured is not None:
            captured["headers"] = headers
            captured["impersonate"] = impersonate
            captured["timeout"] = timeout
        return _FakeResponse(html, status_code)

    monkeypatch.setattr(curl_cffi, "get", _get)


def _job_with(description: str | None, title: str | None = "Senior Engineer") -> scraper.ScrapedJob:
    return scraper.ScrapedJob(
        title=title,
        company="Acme",
        location="Remote",
        description=description,
        deadline=None,
    )


def _zoho_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace('"', "\\x22").replace("-", "\\-").replace("’", "\\u2019")
    )


def _zoho_like_html(value: str) -> str:
    escaped = _zoho_escape(value)
    return (
        "<html><head><title>Zoho Shell</title></head><body>"
        f"<script>var p=\\x22Job_Description\\x22:\\x22{escaped}\\x22;</script>"
        "</body></html>"
    )


def test_extract_job_extracts_open_graph() -> None:
    job = scraper._extract_job(OG_HTML, "https://jobs.example.com/1")
    assert job["title"] == "Senior Engineer"
    assert job["company"] == "Acme Corp"
    assert job["location"] == "Berlin"
    assert job["description"] == "# Clean body"
    assert job["deadline"] is None


def test_scrape_job_impersonates_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: LONG_BODY)
    captured: dict[str, object] = {}
    _patch_get(monkeypatch, OG_HTML, captured=captured)
    scraper.scrape_job("https://jobs.example.com/1")
    assert captured["impersonate"] == "chrome"
    assert captured["headers"] is None
    assert captured["timeout"] == scraper.REQUEST_TIMEOUT


def test_data_is_sufficient_rejects_missing_description() -> None:
    assert not scraper.data_is_sufficient(_job_with(None))


def test_data_is_sufficient_rejects_short_description() -> None:
    assert not scraper.data_is_sufficient(_job_with("Just a few words of role text."))


def test_data_is_sufficient_rejects_title_repeated_as_description() -> None:
    assert not scraper.data_is_sufficient(_job_with(LONG_BODY, title=LONG_BODY))


def test_data_is_sufficient_accepts_long_description() -> None:
    assert scraper.data_is_sufficient(_job_with(LONG_BODY))


def test_extract_job_payload_from_js_escaped_script() -> None:
    plain = f"We’re detail-oriented engineers. {LONG_BODY}"
    html = _zoho_like_html(f"<p>{plain}</p>")
    job = scraper._extract_job(html, "https://example.com")
    assert job["description"] == plain


def test_extract_job_payload_from_json_script() -> None:
    value = f"<p>{LONG_BODY}</p>"
    payload = '{"props": {"post": {"description": "' + value + '"}}}'
    html = '<html><body><script type="application/json">' + payload + "</script></body></html>"
    job = scraper._extract_job(html, "https://example.com")
    assert job["description"] == LONG_BODY


def test_payload_extraction_absent_on_plain_page() -> None:
    soup = BeautifulSoup("<html><body><p>plain</p></body></html>", "html.parser")
    assert scraper._extract_payload_description(soup) is None


def test_extract_job_uses_meta_description_when_thin() -> None:
    html = (
        "<html><head>"
        f'<meta property="og:description" content="{LONG_BODY}" />'
        "<title>Shell</title></head><body></body></html>"
    )
    job = scraper._extract_job(html, "https://example.com")
    assert job["description"] == LONG_BODY


def test_scrape_job_raises_when_content_thin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, "<html><head><title>Tiny</title></head><body></body></html>")
    with pytest.raises(ScrapeError, match="JavaScript-rendered"):
        scraper.scrape_job("https://example.com/job")


def test_scrape_job_returns_sufficient_description(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: LONG_BODY)
    _patch_get(monkeypatch, "<html><head><title>Real Job</title></head><body></body></html>")
    job = scraper.scrape_job("https://example.com/job")
    assert job["description"] == LONG_BODY


def test_scrape_job_falls_back_to_payload_description(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = f"We’re detail-oriented engineers. {LONG_BODY}"
    _patch_get(monkeypatch, _zoho_like_html(f"<p>{plain}</p>"))
    job = scraper.scrape_job("https://example.com/job")
    assert job["description"] == plain


SHELL_HTML = "<html><head><title>Job Shell</title></head><body></body></html>"


def _patch_get_json(
    monkeypatch: pytest.MonkeyPatch,
    payload: object | None,
    seen: list[str] | None = None,
) -> None:
    def _get_json(url: str) -> object | None:
        if seen is not None:
            seen.append(url)
        return payload

    monkeypatch.setattr(scraper, "_get_json", _get_json)


def test_scrape_job_greenhouse_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, SHELL_HTML)
    seen: list[str] = []
    _patch_get_json(
        monkeypatch,
        {
            "title": "QA Engineer",
            "company_name": "JetBrains",
            "location": {"name": "Belgrade, Serbia"},
            "content": f"&lt;p&gt;{LONG_BODY}&lt;/p&gt;",
            "application_deadline": "2026-11-30",
        },
        seen,
    )
    job = scraper.scrape_job("https://job-boards.eu.greenhouse.io/acme/jobs/123456")
    assert seen == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/123456"]
    assert job["title"] == "QA Engineer"
    assert job["company"] == "JetBrains"
    assert job["location"] == "Belgrade, Serbia"
    assert job["description"] == LONG_BODY
    assert job["deadline"] == "2026-11-30"


def test_scrape_job_lever_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, SHELL_HTML)
    seen: list[str] = []
    posting = {
        "id": "97951083-5465-4382-bb4d-ac9d89458a21",
        "text": "Software Engineer 2026",
        "categories": {"location": "Remote - US", "commitment": "Full Time"},
        "descriptionPlain": LONG_BODY,
        "deadline": None,
        "lists": [{"title": None, "content": "<p>Extra responsibilities paragraph.</p>"}],
    }
    _patch_get_json(monkeypatch, [posting], seen)
    job = scraper.scrape_job(
        "https://jobs.lever.co/acme/97951083-5465-4382-bb4d-ac9d89458a21/apply"
    )
    assert seen == ["https://api.lever.co/v0/postings/acme?mode=json"]
    assert job["title"] == "Software Engineer 2026"
    assert job["company"] == "Acme"
    assert job["location"] == "Remote - US"
    assert job["description"] is not None
    assert job["description"].startswith(LONG_BODY)
    assert "Extra responsibilities paragraph." in job["description"]


def test_scrape_job_ashbyhq_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, SHELL_HTML)
    seen: list[str] = []
    _patch_get_json(
        monkeypatch,
        {
            "jobs": [
                {
                    "id": "d0b278b9-2548-4fe3-8878-bcd7989944b9",
                    "title": "Senior Embedded Engineer",
                    "location": "London, United Kingdom",
                    "descriptionPlain": LONG_BODY,
                    "descriptionHtml": f"<p>{LONG_BODY}</p>",
                }
            ]
        },
        seen,
    )
    job = scraper.scrape_job("https://jobs.ashbyhq.com/wayve/d0b278b9-2548-4fe3-8878-bcd7989944b9")
    assert seen == ["https://api.ashbyhq.com/posting-api/job-board/wayve"]
    assert job["title"] == "Senior Embedded Engineer"
    assert job["company"] == "Wayve"
    assert job["location"] == "London, United Kingdom"
    assert job["description"] == LONG_BODY


def test_scrape_job_board_fallback_uses_canonical_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (
        "<html><head><title>Job Shell</title>"
        '<link rel="canonical" href="https://job-boards.greenhouse.io/acme/jobs/789" />'
        "</head><body></body></html>"
    )
    _patch_get(monkeypatch, html)
    seen: list[str] = []
    _patch_get_json(
        monkeypatch,
        {"title": "Platform Engineer", "content": f"<p>{LONG_BODY}</p>"},
        seen,
    )
    job = scraper.scrape_job("https://careers.example.com/job/1")
    assert seen == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/789"]
    assert job["title"] == "Platform Engineer"


def test_scrape_job_skips_board_api_without_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, SHELL_HTML)

    def _unexpected(url: str) -> object | None:
        raise AssertionError(f"board API must not be called, got {url}")

    monkeypatch.setattr(scraper, "_get_json", _unexpected)
    with pytest.raises(ScrapeError, match="JavaScript-rendered"):
        scraper.scrape_job("https://example.com/job")


def test_scrape_job_raises_when_board_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, SHELL_HTML)
    _patch_get_json(monkeypatch, None)
    with pytest.raises(ScrapeError, match="JavaScript-rendered"):
        scraper.scrape_job("https://job-boards.eu.greenhouse.io/acme/jobs/123456")


class _FakeHttpResponse:
    def __init__(self, data: object | None = None, error: Exception | None = None) -> None:
        self._data = data
        self._error = error

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        if self._error is not None:
            raise self._error
        return self._data


def test_get_json_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _FakeHttpResponse({"ok": True}))
    assert scraper._get_json("https://example.com/api") == {"ok": True}


def test_get_json_returns_none_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> _FakeHttpResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _boom)
    assert scraper._get_json("https://example.com/api") is None


def test_get_json_returns_none_on_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: _FakeHttpResponse(error=ValueError("not json"))
    )
    assert scraper._get_json("https://example.com/api") is None


def test_title_falls_back_to_h1() -> None:
    html = "<html><body><h1>Only Heading</h1></body></html>"
    assert scraper._extract_job(html, "https://example.com")["title"] == "Only Heading"


def test_title_falls_back_to_title_tag() -> None:
    html = "<html><head><title>Only Title</title></head><body></body></html>"
    assert scraper._extract_job(html, "https://example.com")["title"] == "Only Title"


def test_title_none_when_absent() -> None:
    assert (
        scraper._extract_job("<html><body><p>no title</p></body></html>", "https://example.com")[
            "title"
        ]
        is None
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.lever.co/acme/123", "Lever"),
        ("https://www.example.com/job", "Example"),
        ("https://example.co.uk/job", "Example"),
        ("https://localhost/job", "Localhost"),
    ],
)
def test_company_inferred_from_domain(url: str, expected: str) -> None:
    job = scraper._extract_job("<html><body></body></html>", url)
    assert job["company"] == expected


def test_location_defaults_when_missing() -> None:
    job = scraper._extract_job("<html><body></body></html>", "https://example.com")
    assert job["location"] == "Not Specified"


def test_location_from_jsonld_jobposting() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "JobPosting", "title": "QA Engineer",
     "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
     "addressLocality": "Arvada", "addressRegion": "CO", "addressCountry": "US"}}}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Arvada, CO, US"


def test_location_jsonld_list_dedupes_parts() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    [{"@type": "JobPosting",
      "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Hamburg",
      "addressRegion": "HAMBURG", "addressCountry": "DE"}}]}]
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Hamburg, DE"


def test_location_jsonld_address_as_string() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "jobLocation": {"address": "Berlin, Germany"}}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Berlin, Germany"


def test_og_meta_wins_over_jsonld() -> None:
    html = """
    <html><head>
    <meta property="og:locality" content="Oslo" />
    <script type="application/ld+json">
    {"@type": "JobPosting", "jobLocation": {"address": {"addressLocality": "Berlin"}}}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Oslo"


def test_malformed_jsonld_falls_back_to_default() -> None:
    html = (
        '<html><head><script type="application/ld+json">{not json</script>'
        "</head><body></body></html>"
    )
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Not Specified"


def test_non_jobposting_jsonld_ignored() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Organization", "address": {"addressLocality": "Paris"}}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["location"] == "Not Specified"


def test_location_from_description_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trafilatura, "extract", lambda *args, **kwargs: "Role text. 📍Location: Croatia"
    )
    job = scraper._extract_job("<html><body><p>Role text</p></body></html>", "https://example.com")
    assert job["location"] == "Croatia"


def test_deadline_from_jsonld_valid_through() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "QA Engineer", "validThrough": "2026-12-31"}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] == "2026-12-31"


def test_deadline_datetime_normalized_to_date() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "validThrough": "2026-12-31T23:59:59+01:00"}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] == "2026-12-31"


def test_deadline_none_when_valid_through_missing() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "QA Engineer"}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] is None


def test_deadline_none_when_unparseable() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "validThrough": "soon"}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] is None


def test_deadline_ignores_non_jobposting_jsonld() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Organization", "validThrough": "2026-12-31"}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] is None


def test_deadline_from_jsonld_graph_form() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@graph": [{"@type": "JobPosting", "validThrough": "2026-11-30"}]}
    </script>
    </head><body></body></html>
    """
    job = scraper._extract_job(html, "https://example.com")
    assert job["deadline"] == "2026-11-30"


def test_description_falls_back_to_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: None)
    job = scraper._extract_job(
        "<html><body><p>Plain body text</p></body></html>", "https://example.com"
    )
    assert job["description"] == "Plain body text"


def test_description_none_when_page_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: "")
    job = scraper._extract_job("<html><body></body></html>", "https://example.com")
    assert job["description"] is None


def test_http_error_raises_scrape_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, "", status_code=404)
    with pytest.raises(ScrapeError):
        scraper.scrape_job("https://example.com/missing")


def test_timeout_raises_scrape_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get(url: str, **kwargs: object) -> _FakeResponse:
        raise Timeout("timed out")

    monkeypatch.setattr(curl_cffi, "get", _get)
    with pytest.raises(ScrapeError):
        scraper.scrape_job("https://example.com/slow")
