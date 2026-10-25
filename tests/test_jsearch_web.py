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
    assert summary.text == "0 new · 1 already tracked"

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


async def test_jsearch_load_more_appends(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page(_job(url="https://jobs.example.com/1"), cursor="next"),
        _page(_job(url="https://jobs.example.com/2", title="Second Role"), cursor=None),
    ]
    calls: list[str | None] = []

    def fake_search(*args: object, **kwargs: object) -> jsearch.JSearchPage:
        calls.append(cast("str | None", kwargs.get("cursor")))
        return pages[len(calls) - 1]

    monkeypatch.setattr(jsearch, "api_key", lambda settings=None: ("key", "environment"))
    monkeypatch.setattr(jsearch, "search", fake_search)

    await user.open("/")
    await _open_panel(user)
    user.find(marker="jsearch-query").type("python")
    user.find(marker="jsearch-run").click()
    await asyncio.sleep(0.3)
    assert len(user.find(marker="jsearch-candidate").elements) == 1

    user.find(marker="jsearch-load-more").click()
    await asyncio.sleep(0.3)

    assert calls == [None, "next"]
    assert len(user.find(marker="jsearch-candidate").elements) == 2


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
