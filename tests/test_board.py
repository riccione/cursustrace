"""Tests for the Kanban board view: columns, archived stages and moving cards."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import cast

import pytest
from nicegui import events, ui
from nicegui.testing import User

from cursustrace import db
from cursustrace.web import board


def _seed(title: str, status: db.JobStatus | None = None, company: str = "Acme") -> int:
    db.init_db()
    job_id = db.add_job(f"https://example.com/{company}/{title}", title, company, "Remote", "Body")
    assert job_id is not None
    if status is not None:
        db.set_job_status(job_id, status)
    return job_id


async def _open_board(user: User) -> None:
    await user.open("/")
    user.find(marker="view-board").click()
    await asyncio.sleep(0.2)


def _cards_in(user: User, status: db.JobStatus) -> list[object]:
    column = user.find(marker=f"board-column-{status}").elements.pop()
    return [element for element in column.descendants() if "ct-board-card" in element.classes]


@pytest.mark.parametrize(
    ("args", "expected"),
    [("7", 7), (["7"], 7), (None, None), ("", None), ("not-a-number", None)],
)
def test_emitted_job_id_accepts_the_wire_shapes(args: object, expected: int | None) -> None:
    event = cast(events.GenericEventArguments, SimpleNamespace(args=args))
    assert board._emitted_job_id(event) == expected


def test_drop_handlers_keep_their_own_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each column's handler moves to its own stage, not the last one built."""
    seen: list[tuple[int, db.JobStatus]] = []
    monkeypatch.setattr(board, "move_job", lambda job_id, status: seen.append((job_id, status)))
    handlers = {status: board._drop_handler(status, lambda: None) for status in db.PIPELINE_STAGES}

    for handler in handlers.values():
        handler(cast(events.GenericEventArguments, SimpleNamespace(args="7")))

    assert seen == [(7, status) for status in db.PIPELINE_STAGES]


def test_drop_handler_ignores_an_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    moved: list[object] = []
    monkeypatch.setattr(board, "move_job", lambda job_id, status: moved.append(job_id))

    board._drop_handler("applied", lambda: None)(
        cast(events.GenericEventArguments, SimpleNamespace(args=None))
    )

    assert moved == []


async def test_view_switch_toggles_board_and_list(user: User) -> None:
    _seed("Role")

    await user.open("/")
    await user.should_see(marker="status-tab-unapplied")
    await user.should_not_see(marker="board")

    user.find(marker="view-board").click()
    await asyncio.sleep(0.2)
    await user.should_see(marker="board")
    await user.should_not_see(marker="status-tab-unapplied")

    user.find(marker="view-list").click()
    await asyncio.sleep(0.2)
    await user.should_see(marker="status-tab-unapplied")
    await user.should_not_see(marker="board")


async def test_board_shows_a_column_per_active_stage(user: User) -> None:
    _seed("Wishlist role")
    _seed("Applied role", "applied")
    _seed("Interview role", "interview")
    _seed("Offer role", "offer")

    await _open_board(user)

    assert _cards_in(user, "unapplied")
    assert _cards_in(user, "applied")
    assert _cards_in(user, "interview")
    assert _cards_in(user, "offer")
    await user.should_not_see(marker="board-column-rejected")
    await user.should_not_see(marker="board-column-outdated")


async def test_archived_columns_are_rendered_on_demand(user: User) -> None:
    _seed("Rejected role", "rejected")
    _seed("Outdated role", "outdated")

    await _open_board(user)
    await user.should_not_see(marker="board-column-rejected")

    user.find(marker="board-archive").click()
    await asyncio.sleep(0.2)

    assert _cards_in(user, "rejected")
    assert _cards_in(user, "outdated")

    user.find(marker="board-archive").click()
    await asyncio.sleep(0.2)
    await user.should_not_see(marker="board-column-rejected")


async def test_move_menu_changes_the_stage(user: User) -> None:
    job_id = _seed("Move me")

    await _open_board(user)
    user.find(marker=f"move-{job_id}").click()
    await asyncio.sleep(0.1)
    user.find(marker=f"move-{job_id}-to-interview").click()
    await asyncio.sleep(0.3)

    assert [job["id"] for job in db.get_jobs(status="interview")] == [job_id]
    assert _cards_in(user, "interview")


async def test_move_menu_can_go_backwards(user: User) -> None:
    """A deliberate move may return a card to an earlier stage."""
    job_id = _seed("Back again", "interview")

    await _open_board(user)
    user.find(marker=f"move-{job_id}").click()
    await asyncio.sleep(0.1)
    user.find(marker=f"move-{job_id}-to-applied").click()
    await asyncio.sleep(0.3)

    assert [job["id"] for job in db.get_jobs(status="applied")] == [job_id]


async def test_board_counts_flagged_positions_in_the_column(user: User) -> None:
    db.init_db()
    stale = db.add_job("https://example.com/stale", "Stale role", "Acme", "Remote", "Body")
    assert stale is not None
    db.set_job_status(stale, "applied")
    # Stale enough to need attention (21 days) but not old enough to be parked (30).
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 25 * 86400))
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET date_applied = ? WHERE id = ?", (stamp, stale))
        conn.commit()

    await _open_board(user)

    attention = user.find(marker="board-attention-applied").elements
    assert attention
    assert cast(ui.label, attention.pop()).text == "1"
    await user.should_see(marker="attention-chip")


async def test_board_respects_the_tag_filter(user: User) -> None:
    tagged = _seed("Tagged role")
    _seed("Untagged role")
    db.set_job_tags(tagged, ["golang"])

    await _open_board(user)
    user.find(kind=ui.chip, content="golang").click()
    await asyncio.sleep(0.3)

    await user.should_see("Tagged role")
    await user.should_not_see("Untagged role")


async def test_board_search_filters_the_cards(user: User) -> None:
    _seed("QA Engineer", company="Adapty")
    _seed("QA Engineer", company="Paysend")

    await _open_board(user)
    user.find("Search company").clear().type("Adapty")
    await asyncio.sleep(0.4)

    await user.should_see("Adapty")
    await user.should_not_see("Paysend")
    await user.should_see(marker="board")
