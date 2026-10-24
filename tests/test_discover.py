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
<a href="https://facebook.com/acme">Social</a>
<a href="https://linkedin.com/company/acme">Company page</a>
<a href="https://linkedin.com/jobs/view/999">LinkedIn job</a>
<a href="/blog/post">Blog</a>
</body></html>
"""

EXPECTED_LINKS = [
    "https://linkedin.com/jobs/view/999",
    "https://remoteok.com/jobs/123",
    "https://remoteok.com/jobs/456?ref=x",
    "https://remoteok.com/jobs/789",
]


def test_extract_links_resolves_filters_and_dedupes() -> None:
    links = discovery.extract_links(SAMPLE_HTML, "https://remoteok.com/search?q=python")

    assert links == EXPECTED_LINKS


def test_extract_links_drops_chrome_and_social() -> None:
    html = """
    <a href="/about">About</a>
    <a href="/jobs/1">Job</a>
    <a href="https://twitter.com/x">Twitter</a>
    <a href="/feed.rss">RSS</a>
    <a href="/login">Login</a>
    """

    assert discovery.extract_links(html, "https://board.example") == [
        "https://board.example/jobs/1"
    ]


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
        "https://linkedin.com/jobs/view/999",
        "https://remoteok.com/jobs/456?ref=x",
        "https://remoteok.com/jobs/789",
    )
    assert result.new_count == 3
    assert result.known_count == 1
    assert list(result.candidates) == [
        {
            "url": "https://linkedin.com/jobs/view/999",
            "known": False,
            "title": "LinkedIn job",
        },
        {
            "url": "https://remoteok.com/jobs/123",
            "known": True,
            "status": "applied",
            "id": job_id,
            "title": "Engineer",
        },
        {"url": "https://remoteok.com/jobs/456?ref=x", "known": False, "title": "Other"},
        {"url": "https://remoteok.com/jobs/789", "known": False, "title": "Relative"},
    ]
    assert result.status_counts == {"applied": 1}


def test_discover_honours_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    result = discovery.discover(discovery.resolve_source("https://remoteok.com"), limit=1)

    assert result.new_urls == ("https://linkedin.com/jobs/view/999",)


def test_discover_propagates_fetch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def boom(url: str) -> str:
        raise ScrapeError(f"Unable to fetch page {url}: 403 Forbidden")

    monkeypatch.setattr(scraper, "fetch_html", boom)

    with pytest.raises(ScrapeError, match="403 Forbidden"):
        discovery.discover(discovery.resolve_source("https://example.com/jobs"))


def test_resolve_source_carries_search_template() -> None:
    source = discovery.resolve_source("remoteok")

    assert source.search_url == "https://remoteok.com/remote-{query}-jobs"


def test_discover_substitutes_query_into_search_template(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    seen: dict[str, str] = {}

    def fake_fetch(url: str) -> str:
        seen["url"] = url
        return SAMPLE_HTML

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    source = discovery.SourceRef(
        input="RemoteOK",
        url="https://remoteok.com",
        site="RemoteOK",
        site_status="live",
        search_url="https://remoteok.com/remote-{query}-jobs",
    )

    result = discovery.discover(source, query="qa automation")

    assert seen["url"] == "https://remoteok.com/remote-qa+automation-jobs"
    assert result.query == "qa automation"
    assert result.fetched_url == "https://remoteok.com/remote-qa+automation-jobs"


def test_discover_filters_links_when_no_search_template(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)
    source = discovery.SourceRef(
        input="https://other.com", url="https://other.com", site=None, site_status=None
    )

    result = discovery.discover(source, query="linkedin")

    assert result.new_urls == ("https://linkedin.com/jobs/view/999",)


def test_discover_only_live_skips_non_live_source() -> None:
    db.init_db()
    source = discovery.SourceRef(
        input="LinkedIn Jobs (RS)",
        url="https://rs.linkedin.com/jobs",
        site="LinkedIn Jobs (RS)",
        site_status="blocked",
    )

    with pytest.raises(ScrapeError, match="only_live"):
        discovery.discover(source, only_live=True)


def test_source_warning_only_for_non_live_named_sources() -> None:
    assert discovery.source_warning(discovery.SourceRef("x", "https://x", "X", "live")) is None
    blocked = discovery.source_warning(discovery.SourceRef("x", "https://x", "X", "blocked"))
    assert blocked is not None and "blocked" in blocked
    assert discovery.source_warning(discovery.SourceRef("u", "https://x", None, None)) is None


def test_list_sources_is_deduplicated_and_sorted() -> None:
    sources = discovery.list_sources()

    assert sources
    names = [str(source.site) for source in sources]
    assert names == sorted(names, key=str.casefold)
    assert len(names) == len({name.casefold() for name in names})
    remoteok = next(source for source in sources if source.site == "RemoteOK")
    assert remoteok.site_status == "live"
    assert remoteok.search_url == "https://remoteok.com/remote-{query}-jobs"


def test_discover_scopes_known_set_to_user(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    db.add_job("https://remoteok.com/jobs/123", "Engineer", "Acme", None, "Body", user_id=1)
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)
    source = discovery.resolve_source("https://remoteok.com")

    first = discovery.discover(source, limit=10, user_id=1)
    second = discovery.discover(source, limit=10, user_id=2)

    assert first.known_count == 1
    assert second.known_count == 0
    assert second.new_count == first.new_count + 1


def _live(site: str, url: str) -> discovery.SourceRef:
    return discovery.SourceRef(input=site, url=url, site=site, site_status="live")


def test_discover_all_merges_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def fake_fetch(url: str) -> str:
        host = url.split("//", 1)[1]
        return (
            '<a href="https://shared.example/jobs/1">Shared</a>'
            f'<a href="https://{host}/jobs/2">Local</a>'
        )

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)

    batch = discovery.discover_all(
        [_live("A", "https://a.example"), _live("B", "https://b.example")]
    )

    assert batch.checked == 2
    assert set(batch.new_urls) == {
        "https://shared.example/jobs/1",
        "https://a.example/jobs/2",
        "https://b.example/jobs/2",
    }
    assert batch.new_count == 3
    assert all(candidate["site"] in {"A", "B"} for candidate in batch.candidates)


def test_discover_all_drops_known_links(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    db.add_job("https://a.example/jobs/1", "Known", "Acme", None, "Body")

    def fake_fetch(url: str) -> str:
        host = url.split("//", 1)[1]
        return f'<a href="https://{host}/jobs/1">Known</a><a href="https://{host}/jobs/2">New</a>'

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)

    batch = discovery.discover_all([_live("A", "https://a.example")])

    assert batch.known_count == 1
    assert batch.new_urls == ("https://a.example/jobs/2",)
    assert all(not candidate["known"] for candidate in batch.candidates)


def test_discover_all_only_live_skips_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return SAMPLE_HTML

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    blocked = discovery.SourceRef(
        input="B", url="https://b.example", site="B", site_status="blocked"
    )

    batch = discovery.discover_all([_live("A", "https://a.example"), blocked])

    assert batch.checked == 1
    assert batch.skipped_not_live == 1
    assert fetched == ["https://a.example"]


def test_discover_all_collects_board_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def fake_fetch(url: str) -> str:
        if "b.example" in url:
            raise ScrapeError("Unable to fetch page: 403")
        return SAMPLE_HTML

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)

    batch = discovery.discover_all(
        [_live("A", "https://a.example"), _live("B", "https://b.example")]
    )

    assert batch.checked == 2
    assert len(batch.errors) == 1
    assert batch.errors[0].source.site == "B"
    assert "403" in batch.errors[0].error


def test_discover_all_honours_per_board_and_total(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def fake_fetch(url: str) -> str:
        host = url.split("//", 1)[1]
        return "".join(
            f'<a href="https://{host}/jobs/{index}">Job {index}</a>' for index in range(5)
        )

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    sources = [_live("A", "https://a.example"), _live("B", "https://b.example")]

    per_board = discovery.discover_all(sources, per_board=2, total=100)
    assert per_board.new_count == 4

    capped = discovery.discover_all(sources, per_board=2, total=3)
    assert capped.new_count == 3
    assert capped.truncated is True


def test_discover_all_shares_one_known_index(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    calls = {"count": 0}
    original = db.get_jobs

    def counting_get_jobs(status: str | None = None, *, user_id: int | None = None) -> list:
        calls["count"] += 1
        return original(status, user_id=user_id)

    monkeypatch.setattr(db, "get_jobs", counting_get_jobs)
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    discovery.discover_all([_live("A", "https://a.example"), _live("B", "https://b.example")])

    assert calls["count"] == 1


def test_discover_all_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    seen: list[tuple[int, int, str]] = []
    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    discovery.discover_all(
        [_live("A", "https://a.example"), _live("B", "https://b.example")],
        progress=lambda done, total, site: seen.append((done, total, site)),
    )

    assert [done for done, _, _ in seen] == [1, 2]
    assert all(total == 2 for _, total, _ in seen)


def test_discover_all_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    state = {"cancel": False}

    def progress(done: int, total: int, site: str) -> None:
        state["cancel"] = True

    monkeypatch.setattr(scraper, "fetch_html", lambda url: SAMPLE_HTML)

    batch = discovery.discover_all(
        [_live("A", "https://a.example"), _live("B", "https://b.example")],
        progress=progress,
        cancelled=lambda: state["cancel"],
    )

    assert batch.cancelled is True
    assert batch.checked == 1


def test_discover_all_filters_by_query(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()

    def fake_fetch(url: str) -> str:
        host = url.split("//", 1)[1]
        return (
            '<a href="https://shared.example/jobs/1">Shared</a>'
            f'<a href="https://{host}/jobs/2">Local</a>'
        )

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)

    batch = discovery.discover_all([_live("A", "https://a.example")], query="shared")

    assert batch.new_urls == ("https://shared.example/jobs/1",)


RANKING_HTML = """
<a href="/about">About</a>
<a href="/jobs-with-salary">Jobs with salary</a>
<a href="/jobs/locations/berlin">Berlin</a>
<a href="/jobs/companies/acme">Acme</a>
<a href="/jobs/qa">QA</a>
<a href="/hire-developers/qa">QA engineers</a>
<a href="/remote-jobs">Remote jobs</a>
<a href="/jobs/companies/acme/senior-qa-engineer-berlin-433385">Senior QA Engineer</a>
"""

FOLLOW_HOME_HTML = """
<a href="/about">About</a>
<a href="/remote-jobs">Remote jobs</a>
"""

FOLLOW_LISTING_HTML = """
<a href="/remote-jobs/j/acme-senior-qa-engineer-pszakv5wm7">Senior QA Engineer</a>
<a href="/remote-jobs/j/acme-staff-engineer-ptp8lc4kuu">Staff Engineer</a>
"""


def test_extract_links_ranks_job_details_above_navigation() -> None:
    entries = discovery.extract_link_entries(RANKING_HTML, "https://board.example")

    assert [entry.url for entry in entries] == [
        "https://board.example/jobs/companies/acme/senior-qa-engineer-berlin-433385"
    ]
    assert entries[0].title == "Senior QA Engineer"
    assert entries[0].score == 9


def test_extract_links_matches_query_in_anchor_text() -> None:
    html = '<a href="https://board.example/careers/123456">Senior QA Engineer</a>'

    assert discovery.extract_links(html, "https://board.example", query="qa") == [
        "https://board.example/careers/123456"
    ]
    assert discovery.extract_links(html, "https://board.example", query="rust") == []


def test_discover_follows_a_listings_page_for_named_boards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.init_db()
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return FOLLOW_HOME_HTML if url == "https://arc.example" else FOLLOW_LISTING_HTML

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    source = discovery.SourceRef(
        input="Arc", url="https://arc.example", site="Arc", site_status="live"
    )

    result = discovery.discover(source)

    assert fetched == ["https://arc.example", "https://arc.example/remote-jobs"]
    assert result.fetched_url == "https://arc.example/remote-jobs"
    assert [candidate["url"] for candidate in result.candidates] == [
        "https://arc.example/remote-jobs/j/acme-senior-qa-engineer-pszakv5wm7",
        "https://arc.example/remote-jobs/j/acme-staff-engineer-ptp8lc4kuu",
    ]
    assert result.candidates[0]["title"] == "Senior QA Engineer"


def test_discover_does_not_follow_listings_for_raw_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.init_db()
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return FOLLOW_HOME_HTML

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    source = discovery.SourceRef(
        input="https://arc.example", url="https://arc.example", site=None, site_status=None
    )

    result = discovery.discover(source)

    assert fetched == ["https://arc.example"]
    assert result.candidates == ()


def test_discover_skips_follow_when_details_are_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.init_db()
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return FOLLOW_LISTING_HTML + '<a href="/jobs">Jobs</a>'

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    source = discovery.SourceRef(
        input="Arc", url="https://arc.example", site="Arc", site_status="live"
    )

    discovery.discover(source)

    assert fetched == ["https://arc.example"]


def test_discover_keeps_first_pass_when_follow_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.init_db()

    def fake_fetch(url: str) -> str:
        if url.endswith("/jobs"):
            raise ScrapeError("403 Forbidden")
        return '<a href="/jobs">Jobs</a>'

    monkeypatch.setattr(scraper, "fetch_html", fake_fetch)
    source = discovery.SourceRef(
        input="Arc", url="https://arc.example", site="Arc", site_status="live"
    )

    result = discovery.discover(source)

    assert result.candidates == ()
    assert result.fetched_url == "https://arc.example"


def test_discover_all_counts_empty_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    monkeypatch.setattr(scraper, "fetch_html", lambda url: '<a href="/about">About</a>')

    batch = discovery.discover_all([_live("A", "https://a.example")])

    assert batch.empty == 1
    assert batch.checked == 1
