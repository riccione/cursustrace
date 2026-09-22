"""Tests for the cursustrace web scraper."""

from __future__ import annotations

import pytest
import requests
import trafilatura

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


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


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
        timeout: float | None = None,
    ) -> _FakeResponse:
        if captured is not None:
            captured["headers"] = headers
            captured["timeout"] = timeout
        return _FakeResponse(html, status_code)

    monkeypatch.setattr(requests, "get", _get)


def test_scrape_job_extracts_open_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, OG_HTML)
    job = scraper.scrape_job("https://jobs.example.com/1")
    assert job["title"] == "Senior Engineer"
    assert job["company"] == "Acme Corp"
    assert job["location"] == "Berlin"
    assert job["description"] == "# Clean body"


def test_scrape_job_sends_chrome_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    _patch_get(monkeypatch, OG_HTML, captured=captured)
    scraper.scrape_job("https://jobs.example.com/1")
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "Chrome" in str(headers.get("User-Agent"))
    assert captured["timeout"] == scraper.REQUEST_TIMEOUT


def test_title_falls_back_to_h1(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><h1>Only Heading</h1></body></html>"
    _patch_get(monkeypatch, html)
    assert scraper.scrape_job("https://example.com")["title"] == "Only Heading"


def test_title_falls_back_to_title_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><head><title>Only Title</title></head><body></body></html>"
    _patch_get(monkeypatch, html)
    assert scraper.scrape_job("https://example.com")["title"] == "Only Title"


def test_title_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, "<html><body><p>no title</p></body></html>")
    assert scraper.scrape_job("https://example.com")["title"] is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.lever.co/acme/123", "Lever"),
        ("https://www.example.com/job", "Example"),
        ("https://example.co.uk/job", "Example"),
        ("https://localhost/job", "Localhost"),
    ],
)
def test_company_inferred_from_domain(
    monkeypatch: pytest.MonkeyPatch, url: str, expected: str
) -> None:
    _patch_get(monkeypatch, "<html><body></body></html>")
    assert scraper.scrape_job(url)["company"] == expected


def test_location_defaults_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, "<html><body></body></html>")
    assert scraper.scrape_job("https://example.com")["location"] == "Not Specified"


def test_description_falls_back_to_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: None)
    _patch_get(monkeypatch, "<html><body><p>Plain body text</p></body></html>")
    assert scraper.scrape_job("https://example.com")["description"] == "Plain body text"


def test_description_none_when_page_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: "")
    _patch_get(monkeypatch, "<html><body></body></html>")
    assert scraper.scrape_job("https://example.com")["description"] is None


def test_http_error_raises_scrape_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, "", status_code=404)
    with pytest.raises(ScrapeError):
        scraper.scrape_job("https://example.com/missing")


def test_timeout_raises_scrape_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get(
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "get", _get)
    with pytest.raises(ScrapeError):
        scraper.scrape_job("https://example.com/slow")
