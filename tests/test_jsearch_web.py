"""Web tests for the experimental JSearch section of the Discover tab."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from nicegui import ui
from nicegui.testing import User

from cursustrace import db, jsearch


def _job(url: str = "https://jobs.example.com/1", **overrides: object) -> jsearch.JSearchJob:
    data: dict[str, object] = {
        "url": url,
        "title": "Senior Python Engineer",
        "company": "Acme",
        "location": "Belgrade, Serbia",
        "description": "Build things.",
        "flags": (),
    }
    data.update(overrides)
    return jsearch.JSearchJob(**data)  # type: ignore[arg-type]


def _page(*jobs: jsearch.JSearchJob, cursor: str | None = None) -> jsearch.JSearchPage:
    return jsearch.JSearchPage(jobs=tuple(jobs), cursor=cursor, requests=1)


async def _open_panel(user: User) -> None:
    user.find("Discover").click()
    await asyncio.sleep(0.1)
    user.find(marker="jsearch-panel").click()
    await asyncio.sleep(0.2)


async def test_jsearch_prompts_for_a_key(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: None)

    await user.open("/")
    await _open_panel(user)

    assert user.find(marker="jsearch-key-input").elements


async def test_jsearch_search_and_add(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", lambda *args, **kwargs: _page(_job()))

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    assert user.find(marker="jsearch-candidate").elements
    assert db.get_jobs() == []

    user.find(marker="jsearch-add-selected").click()
    await asyncio.sleep(0.3)

    assert [job["title"] for job in db.get_jobs()] == ["Senior Python Engineer"]


async def test_jsearch_hides_known_until_toggled(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.add_job("https://jobs.example.com/1", "Senior Python Engineer", "Acme", None, "Body")
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", lambda *args, **kwargs: _page(_job()))

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    summary = cast(ui.label, user.find(marker="jsearch-summary").elements.pop())
    assert summary.text == "Page 1 · 0 new · 1 already tracked"

    user.find(marker="jsearch-show-known").click()
    await asyncio.sleep(0.2)

    assert user.find(marker="jsearch-known").elements


async def test_jsearch_unchecks_flagged_results(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(flags=("location mentions 'Berlin' — not Serbia/EU-remote",))
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", lambda *args, **kwargs: _page(job))

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    box = cast(ui.checkbox, user.find(marker="jsearch-candidate").elements.pop())
    assert box.value is False


def _paged_search(
    pages: list[jsearch.JSearchPage],
) -> tuple[list[tuple[object, dict[str, object]]], object]:
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_search(*args: object, **kwargs: object) -> jsearch.JSearchPage:
        calls.append((args[0], kwargs))
        return pages[len(calls) - 1]

    return calls, fake_search


TWO_PAGES = [
    _page(_job(url="https://jobs.example.com/1"), cursor="next"),
    _page(_job(url="https://jobs.example.com/2", title="Second Role"), cursor=None),
]


async def test_jsearch_pagination_moves_between_cached_pages(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, fake_search = _paged_search(TWO_PAGES)
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    page = cast(ui.label, user.find(marker="jsearch-page").elements.pop())
    assert page.text == "Page 1"
    await user.should_see("Senior Python Engineer")
    user.find(marker="jsearch-next").click()
    await asyncio.sleep(0.3)

    await user.should_see("Second Role")
    assert [call[0] for call in calls] == ["python", "python"]
    assert "cursor" not in calls[0][1]
    assert calls[1][1]["cursor"] == "next"
    assert page.text == "Page 2"

    user.find(marker="jsearch-prev").click()
    await asyncio.sleep(0.2)

    assert len(calls) == 2
    assert page.text == "Page 1"
    await user.should_see("Senior Python Engineer")

    user.find(marker="jsearch-next").click()
    await asyncio.sleep(0.2)

    assert len(calls) == 2
    assert page.text == "Page 2"


async def test_jsearch_pagination_uses_the_search_snapshot(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, fake_search = _paged_search(TWO_PAGES)
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    user.find(marker="jsearch-query").clear().type("rust")
    user.find(marker="jsearch-next").click()
    await asyncio.sleep(0.3)

    assert calls[0][0] == "python"
    assert calls[1][0] == "python"
    assert calls[1][1]["country"] == "us"
    assert calls[1][1]["date_posted"] == "all"
    assert calls[1][1]["remote_only"] is False


async def test_jsearch_pagination_keeps_selections_across_pages(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, fake_search = _paged_search(TWO_PAGES)
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    first = cast(ui.checkbox, user.find(marker="jsearch-candidate").elements.pop())
    assert first.value is True
    user.find(marker="jsearch-candidate").click()
    await asyncio.sleep(0.1)
    first = cast(ui.checkbox, user.find(marker="jsearch-candidate").elements.pop())
    assert first.value is False

    user.find(marker="jsearch-next").click()
    await asyncio.sleep(0.3)
    second = cast(ui.checkbox, user.find(marker="jsearch-candidate").elements.pop())
    assert second.value is True

    user.find(marker="jsearch-prev").click()
    await asyncio.sleep(0.2)
    first = cast(ui.checkbox, user.find(marker="jsearch-candidate").elements.pop())
    assert first.value is False

    add = cast(ui.button, user.find(marker="jsearch-add-selected").elements.pop())
    assert add.text == "Add selected (1)"


async def test_jsearch_pagination_adds_across_pages(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    _, fake_search = _paged_search(TWO_PAGES)
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)
    user.find(marker="jsearch-next").click()
    await asyncio.sleep(0.3)

    add = cast(ui.button, user.find(marker="jsearch-add-selected").elements.pop())
    assert add.text == "Add selected (2)"
    user.find(marker="jsearch-add-selected").click()
    for _ in range(20):
        if db.get_jobs():
            break
        await asyncio.sleep(0.1)

    assert sorted(job["title"] for job in db.get_jobs()) == [
        "Second Role",
        "Senior Python Engineer",
    ]
    add = cast(ui.button, user.find(marker="jsearch-add-selected").elements.pop())
    assert add.text == "Add selected"


async def test_jsearch_pagination_disables_next_at_the_end(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", lambda *args, **kwargs: _page(_job()))

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    next_button = cast(ui.button, user.find(marker="jsearch-next").elements.pop())
    assert next_button.enabled is False


async def test_jsearch_shows_monthly_usage(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    db.set_setting(jsearch.month_key(), "12")
    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))

    await user.open("/")
    await _open_panel(user)

    label = cast(ui.label, user.find(marker="jsearch-usage").elements.pop())
    assert "12 / 200" in label.text


async def test_jsearch_country_select_passes_the_code(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_search(*args: object, **kwargs: object) -> jsearch.JSearchPage:
        captured.update(kwargs)
        return _page(_job())

    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    country = cast(ui.select, user.find(marker="jsearch-country").elements.pop())
    assert country.value == jsearch.DEFAULT_COUNTRY

    country.set_value("de")
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)

    assert captured["country"] == "de"
