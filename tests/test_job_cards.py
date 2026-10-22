"""Tests for the job card layout: header, status badge and stage tracker."""

from __future__ import annotations

import asyncio
import time
from typing import cast

from nicegui import ui
from nicegui.testing import User

from cursustrace import db


def _seed_job(title: str = "Role", company: str = "Acme") -> int:
    db.init_db()
    job_id = db.add_job("https://example.com/card", title, company, "Remote", "Body")
    assert job_id is not None
    return job_id


def _stamp(days_ago: int = 0) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days_ago * 86400))


async def _show_secondary_stage(user: User, status: str) -> None:
    """Open the More menu and show a stage that lives behind it."""
    user.find(marker="more-stages").click()
    await asyncio.sleep(0.1)
    user.find(marker=f"more-stage-{status}").click()
    await asyncio.sleep(0.2)


def _classes(user: User, marker: str) -> list[str]:
    """Return the class list of every element carrying ``marker``."""
    return [str(element.classes) for element in user.find(marker=marker).elements]


def _texts(user: User, marker: str) -> set[str]:
    """Return the text of every element carrying ``marker``."""
    return {cast(ui.label, element).text for element in user.find(marker=marker).elements}


async def test_card_shows_title_company_and_badge(user: User) -> None:
    _seed_job(title="Sr. QA Engineer", company="House RX")

    await user.open("/")

    assert _texts(user, "job-card-title") == {"Sr. QA Engineer"}
    assert _texts(user, "job-card-company") == {"House RX"}
    assert _texts(user, "job-card-badge") == {"Unapplied"}
    assert any("ct-badge--unapplied" in classes for classes in _classes(user, "job-card-badge"))


async def test_tracker_shows_every_pipeline_stage(user: User) -> None:
    _seed_job()

    await user.open("/")

    trackers = user.find(marker="job-card-tracker").elements
    assert trackers
    labels = [
        cast(ui.label, element).text
        for tracker in trackers
        for element in tracker.descendants()
        if "ct-tracker__label" in element.classes
    ]
    assert labels == ["Unapplied", "Applied", "Interview", "Offer"]


async def test_tracker_marks_done_current_and_future_stages(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "interview")

    await user.open("/")

    tracker = user.find(marker="job-card-tracker").elements.pop()
    steps = [
        str(element.classes)
        for element in tracker.descendants()
        if "ct-tracker__step" in element.classes
    ]
    assert sum("is-done" in classes for classes in steps) == 2
    assert sum("is-current" in classes for classes in steps) == 1
    assert sum("is-todo" in classes for classes in steps) == 1


async def test_rejected_card_replaces_the_tracker_with_a_note(user: User) -> None:
    job_id = _seed_job()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET date_applied = ?, date_rejected = ? WHERE id = ?",
            (_stamp(days_ago=14), _stamp(), job_id),
        )
        conn.commit()
    db.set_job_status(job_id, "rejected")

    await user.open("/")
    await _show_secondary_stage(user, "rejected")

    await user.should_not_see(marker="job-card-tracker")
    notes = _texts(user, "job-card-terminal")
    assert notes == {"❌ Application rejected after 14 days"}


async def test_outdated_card_replaces_the_tracker_with_a_note(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "outdated")

    await user.open("/")
    await _show_secondary_stage(user, "outdated")

    await user.should_not_see(marker="job-card-tracker")
    notes = _texts(user, "job-card-terminal")
    assert notes == {"🗄️ Listing is outdated"}


async def test_offer_card_shows_the_offer_badge(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "offer")

    await user.open("/")

    assert _texts(user, "job-card-badge") == {"Offer"}
    assert any("ct-badge--offer" in classes for classes in _classes(user, "job-card-badge"))


async def test_card_body_keeps_the_position_details(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")

    await user.open("/")

    expansion = user.find(kind=ui.expansion).elements.pop()
    assert expansion.value is False
    await user.should_see(marker="days-since-applied")
