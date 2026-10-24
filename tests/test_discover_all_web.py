"""Web tests for all-board discovery in the Discover tab."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from nicegui import ui
from nicegui.testing import User

from cursustrace import db, discovery, scraper
from cursustrace.errors import ScrapeError


def _batch(
    *urls: str,
    known_count: int = 0,
    errors: tuple[discovery.BoardError, ...] = (),
    title: str | None = None,
    empty: int = 0,
) -> discovery.DiscoveryBatch:
    candidates = tuple(
        {"url": url, "known": False, "site": "Example Board", "title": title} for url in urls
    )
    return discovery.DiscoveryBatch(
        source=discovery.SourceRef(
            input="All known boards",
            url="",
            site="All known boards",
            site_status=None,
        ),
        candidates=candidates,
        new_urls=tuple(urls),
        checked=3,
        extracted=8,
        skipped_not_live=1,
        known_count=known_count,
        empty=empty,
        errors=errors,
        truncated=False,
    )


async def _run_all(user: User) -> None:
    board = cast(ui.select, user.find(marker="discover-board").elements.pop())
    board.set_value(discovery.ALL_SOURCES)
    await asyncio.sleep(0.1)
    user.find(marker="discover-run").click()
    await user.should_see(marker="discover-summary")


async def test_all_boards_lists_new_links_with_site(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", "https://b.example/jobs/2"),
    )
    await user.open("/")

    await _run_all(user)

    await user.should_see("2 new · 0 already tracked")
    assert len(user.find(marker="discover-candidate").elements) == 2
    assert len(user.find(marker="discover-site").elements) == 2


async def test_all_boards_hides_known_links(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", known_count=4),
    )
    await user.open("/")

    await _run_all(user)

    await user.should_see("1 new · 4 already tracked")
    assert len(user.find(marker="discover-candidate").elements) == 1


async def test_all_boards_shows_failed_boards(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    errors = (
        discovery.BoardError(
            discovery.SourceRef(
                input="Broken", url="https://broken.example", site="Broken", site_status="live"
            ),
            "403 Forbidden",
        ),
    )
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", errors=errors),
    )
    await user.open("/")

    await _run_all(user)

    assert user.find(marker="discover-errors").elements


async def test_all_boards_add_selected_keeps_failed_links(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", "https://b.example/jobs/2"),
    )

    def scrape(url: str) -> scraper.ScrapedJob:
        if url.endswith("/1"):
            raise ScrapeError("403 Forbidden")
        return {
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body",
            "deadline": None,
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    await _run_all(user)

    user.find(marker="discover-add-selected").click()

    await user.should_see("Saved 1")
    assert user.find(marker="discover-candidate").elements
    remaining = cast(ui.checkbox, user.find(marker="discover-candidate").elements.pop())
    assert remaining.text == "https://a.example/jobs/1"


async def test_all_boards_shows_candidate_titles(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", title="Senior QA Engineer"),
    )
    await user.open("/")

    await _run_all(user)

    box = cast(ui.checkbox, user.find(marker="discover-candidate").elements.pop())
    assert box.text == "Senior QA Engineer"


async def test_all_boards_reports_boards_without_listings(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        discovery,
        "discover_all",
        lambda *args, **kwargs: _batch("https://a.example/jobs/1", empty=2),
    )
    await user.open("/")

    await _run_all(user)

    await user.should_see("2 without listings")
