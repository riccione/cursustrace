"""Tests for source resolution and link discovery mechanics."""

from __future__ import annotations

from pathlib import Path

import pytest

from cursustrace import db, discovery, scraper
from cursustrace.errors import ScrapeError


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")


SAMPLE_HTML = """
<html><body>
<a href="/jobs/123">Senior Engineer</a>
<a href="https://remoteok.com/jobs/456?ref=x">Other</a>
<a href="jobs/789">Relative</a>
<a href="https://remoteok.com/jobs/123/">Duplicate</a>
<a href="#section">Fragment</a>
<a href="mailto:x@y.z">Mail</a>
<a href="javascript:void(0)">JS</a>
<a href="https://remoteok.com/">Homepage</a>
<a href="https://other.com/about">Offsite</a>
</body></html>
"""

EXPECTED_LINKS = [
    "https://remoteok.com/jobs/123",
    "https://remoteok.com/jobs/456?ref=x",
    "https://remoteok.com/jobs/789",
    "https://other.com/about",
]


def test_extract_links_resolves_filters_and_dedupes() -> None:
    links = discovery.extract_links(SAMPLE_HTML, "https://remoteok.com/search?q=python")

    assert links == EXPECTED_LINKS


def test_extract_links_honours_limit() -> None:
    links = discovery.extract_links(SAMPLE_HTML, "https://remoteok.com/search", limit=2)

    assert links == EXPECTED_LINKS[:2]


def test_extract_links_drops_empty_pages() -> None:
    assert discovery.extract_links("<a href='/'>home</a>", "https://x.example/jobs") == []


def test_resolve_source_passes_url_through() -> None:
    source = discovery.resolve_source("https://example.com/jobs?limit=50")

    assert source.url == "https://example.com/jobs?limit=50"
    assert source.site is None
    assert source.site_status is None


def test_resolve_source_matches_site_name_case_insensitively() -> None:
    source = discovery.resolve_source("remoteok")

    assert source.site == "RemoteOK"
    assert source.url == "https://remoteok.com"
    assert source.site_status == "live"


def test_resolve_source_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        discovery.resolve_source("NotASite")


def test_resolve_source_without_job_sources_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery, "JOB_SOURCES_DIR", tmp_path / "missing")

    with pytest.raises(ValueError, match="Unknown source"):
        discovery.resolve_source("RemoteOK")
    assert discovery.resolve_source("https://x.example/jobs").url == "https://x.example/jobs"


def test_discover_splits_known_and_new(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    job_id = db.add_job("https://remoteok.com/jobs/123", "Engineer", "Acme", None, "Body")
    assert job_id is not None
    db.set_job_status(job_id, "applied")
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    result = discovery.discover(discovery.resolve_source("https://remoteok.com"), limit=10)

    assert result.new_urls == (
        "https://remoteok.com/jobs/456?ref=x",
        "https://remoteok.com/jobs/789",
        "https://other.com/about",
    )
    assert result.new_count == 3
    assert result.known_count == 1
    assert list(result.candidates) == [
        {
            "url": "https://remoteok.com/jobs/123",
            "known": True,
            "status": "applied",
            "id": job_id,
            "title": "Engineer",
        },
        {"url": "https://remoteok.com/jobs/456?ref=x", "known": False},
        {"url": "https://remoteok.com/jobs/789", "known": False},
        {"url": "https://other.com/about", "known": False},
    ]
    assert result.status_counts == {"applied": 1}


def test_discover_honours_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    result = discovery.discover(discovery.resolve_source("https://remoteok.com"), limit=1)

    assert result.new_urls == ("https://remoteok.com/jobs/123",)


def test_discover_propagates_fetch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def boom(url: str) -> str:
        raise ScrapeError(f"Unable to fetch page {url}: 403 Forbidden")

    monkeypatch.setattr(scraper, "fetch_html", boom)

    with pytest.raises(ScrapeError, match="403 Forbidden"):
        discovery.discover(discovery.resolve_source("https://example.com/jobs"))
