"""NiceGUI UI tests for cursustrace using the user simulation."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from nicegui import ui
from nicegui.testing import User
from pypdf import PdfReader

from cursustrace import app, attention, config, db, logsetup, scraper
from cursustrace.errors import ScrapeError

JOB_URL = "https://example.com/jobs/1"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def _scan(user: User, url: str = JOB_URL) -> None:
    user.find(marker="scan-urls").clear().type(url)
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()
    await asyncio.sleep(0.05)


NOTICE_RETRIES = 50


async def _see_notice(user: User, text: str) -> None:
    """Wait for a notice, allowing slow CI ample time for scan + dashboard rebuild."""
    await user.should_see(text, retries=NOTICE_RETRIES)


def _seed_job(title: str = "Senior Engineer", description: str = "Body text") -> int:
    db.init_db()
    db.add_job(JOB_URL, title, "Acme", "Remote", description)
    return db.get_jobs()[0]["id"]


def _days_ago_stamp(days: int) -> str:
    now = time.localtime()
    today = date(now.tm_year, now.tm_mon, now.tm_mday)
    return (today - timedelta(days=days)).strftime("%Y-%m-%d 09:00:00")


def _seed_stale_job(days: int = 15) -> int:
    db.init_db()
    job_id = db.add_job(JOB_URL, "Stale Lead", "Acme", "Remote", "Body")
    assert job_id is not None
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET date_added = ? WHERE id = ?", (_days_ago_stamp(days), job_id))
    return job_id


def _profile_payload(
    full_name: str = "Jane Doe",
    summary: str = "# Jane",
    profile_id: int = 1,
    name: str = "Default",
) -> db.Profile:
    return {
        "id": profile_id,
        "name": name,
        "full_name": full_name,
        "location": "Remote",
        "phone": "555-0100",
        "email": "jane@example.com",
        "linkedin_url": "https://linkedin.com/in/jane",
        "github_url": "https://github.com/jane",
        "summary": summary,
        "work_history": "## Work History",
        "education": "## Education",
        "skills": "## Skills",
        "date_updated": None,
    }


async def test_title_renders(user: User) -> None:
    await user.open("/")
    await user.should_see(app.APP_TITLE)


async def test_scan_saves_position(user: User) -> None:
    await user.open("/")
    await _scan(user)

    await _see_notice(user, "Saved 1")
    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Engineer"
    assert jobs[0]["company"] == "Acme"


async def test_scan_saves_deadline(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body text",
            "deadline": "2026-12-31",
        },
    )
    await user.open("/")
    await _scan(user)

    await _see_notice(user, "Saved 1")
    assert db.get_jobs()[0]["deadline"] == "2026-12-31"


async def test_duplicate_url_shows_warning(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    await _scan(user)

    await _see_notice(user, "Duplicates 1")
    assert len(db.get_jobs()) == 1


async def test_similar_position_added_with_notice(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body text",
            "deadline": None,
        },
    )
    await _scan(user, "https://other.com/jobs/999")

    await _see_notice(user, "Saved 1")
    await _see_notice(user, "look similar")
    assert len(db.get_jobs()) == 2


async def test_similar_notice_can_be_disabled(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    db.set_setting("similar_notice", "off")
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body text",
            "deadline": None,
        },
    )
    await _scan(user, "https://other.com/jobs/999")

    await _see_notice(user, "Saved 1")
    await _wait_for(lambda: len(db.get_jobs()) == 2, timeout=5.0)
    await _see_notice(user, "1-2 of 2")
    await user.should_not_see("look similar")
    assert len(db.get_jobs()) == 2


async def test_empty_url_shows_warning(user: User) -> None:
    await user.open("/")
    user.find("Scan & Save Positions").click()

    await _see_notice(user, "Please enter at least one job URL.")


async def test_scrape_error_shows_error(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_scrape(url: str) -> scraper.ScrapedJob:
        raise ScrapeError(f"boom: {url}")

    monkeypatch.setattr(scraper, "scrape_job", raise_scrape)
    await user.open("/")
    await _scan(user)

    await _see_notice(user, "boom")
    assert db.get_jobs() == []


async def test_scan_multiple_urls_saves_all(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    def scrape(url: str) -> scraper.ScrapedJob:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "title": f"Engineer {slug}",
            "company": "Acme",
            "location": "Remote",
            "description": f"Body {slug}",
            "deadline": None,
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    user.find(marker="scan-urls").clear().type(
        "https://example.com/jobs/1\nhttps://example.com/jobs/2\nhttps://example.com/jobs/3"
    )
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()

    await _wait_for(lambda: len(db.get_jobs()) == 3)
    await _see_notice(user, "Saved 3")


async def test_scan_multiple_reports_mixed_outcomes(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def scrape(url: str) -> scraper.ScrapedJob:
        if url.endswith("/bad"):
            raise ScrapeError("nope")
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "title": f"Engineer {slug}",
            "company": "Acme",
            "location": "Remote",
            "description": f"Body {slug}",
            "deadline": None,
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    await _scan(user, "https://example.com/jobs/1")
    await _see_notice(user, "Saved 1")

    user.find(marker="scan-urls").clear().type(
        "https://example.com/jobs/2\nhttps://example.com/jobs/1\nhttps://example.com/jobs/bad"
    )
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()

    await _wait_for(lambda: len(db.get_jobs()) == 2)
    await _see_notice(user, "Saved 1 · Duplicates 1 · Failed 1")
    assert db.get_jobs()[0]["job_url"] == "https://example.com/jobs/2"


def _scan_input(user: User) -> ui.textarea:
    return cast(ui.textarea, user.find(marker="scan-urls").elements.pop())


async def test_scan_clears_input_on_success(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    await _wait_for(lambda: _scan_input(user).value == "")


async def test_scan_keeps_failed_urls(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    def scrape(url: str) -> scraper.ScrapedJob:
        if url.endswith("/bad"):
            raise ScrapeError("nope")
        return {
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body",
            "deadline": None,
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    user.find(marker="scan-urls").clear().type(
        "https://example.com/jobs/1\nhttps://example.com/jobs/bad"
    )
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()

    await _wait_for(lambda: len(db.get_jobs()) == 1)
    await _wait_for(lambda: _scan_input(user).value == "https://example.com/jobs/bad")


def test_parse_urls_splits_and_dedupes() -> None:
    text = "https://a.com/1\n\n  https://a.com/2  \nhttps://a.com/1\n"

    assert app._parse_urls(text) == ["https://a.com/1", "https://a.com/2"]


def test_parse_urls_handles_empty_input() -> None:
    assert app._parse_urls(None) == []
    assert app._parse_urls("   \n  ") == []


def _checkbox(user: User, label: str) -> ui.checkbox:
    return user.find(kind=ui.checkbox, content=label).elements.pop()


async def test_checkbox_marks_job_applied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    user.find(kind=ui.checkbox, content="Applied").click()

    await _wait_for(lambda: len(db.get_jobs(status="applied")) == 1)
    applied = db.get_jobs(status="applied")[0]
    assert TIMESTAMP_RE.match(applied["date_applied"] or "")
    assert db.get_jobs(status="unapplied") == []


async def test_checkbox_marks_job_interview(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    user.find(kind=ui.checkbox, content="Interview").click()

    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)
    assert job["date_interview"] is not None


async def test_checkbox_marks_job_rejected(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    user.find(kind=ui.checkbox, content="Rejected").click()

    await _wait_for(lambda: db.get_jobs()[0]["rejected"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 0, 1)
    assert job["date_rejected"] is not None


async def test_interview_replaces_applied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    user.find(kind=ui.checkbox, content="Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    user.find(kind=ui.checkbox, content="Interview").click()
    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)

    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)


async def test_unchecking_rejected_returns_to_unapplied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    user.find(kind=ui.checkbox, content="Rejected").click()
    await _wait_for(lambda: db.get_jobs()[0]["rejected"] == 1)

    user.find(kind=ui.checkbox, content="Rejected").click()
    await _wait_for(
        lambda: (
            (
                db.get_jobs()[0]["applied"],
                db.get_jobs()[0]["interview"],
                db.get_jobs()[0]["rejected"],
            )
            == (0, 0, 0)
        )
    )

    job = db.get_jobs()[0]
    assert job["date_applied"] is None
    assert job["date_interview"] is None
    assert job["date_rejected"] is None


async def test_checkbox_states_unapplied(user: User) -> None:
    _seed_job()
    await user.open("/")

    assert _checkbox(user, "Applied").enabled is True
    assert _checkbox(user, "Interview").enabled is True
    assert _checkbox(user, "Rejected").enabled is True
    assert _checkbox(user, "Applied").value is False
    assert _checkbox(user, "Interview").value is False
    assert _checkbox(user, "Rejected").value is False


async def test_checkbox_states_applied(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    await user.open("/")

    assert _checkbox(user, "Applied").enabled is False
    assert _checkbox(user, "Applied").value is True
    assert _checkbox(user, "Interview").enabled is True
    assert _checkbox(user, "Rejected").enabled is True


async def test_checkbox_states_interview(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "interview")
    await user.open("/")

    assert _checkbox(user, "Applied").enabled is False
    assert _checkbox(user, "Applied").value is True
    assert _checkbox(user, "Interview").enabled is False
    assert _checkbox(user, "Interview").value is True
    assert _checkbox(user, "Rejected").enabled is True
    assert _checkbox(user, "Rejected").value is False


async def test_checkbox_states_rejected_from_applied(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "rejected")
    await user.open("/")

    assert _checkbox(user, "Applied").enabled is False
    assert _checkbox(user, "Applied").value is True
    assert _checkbox(user, "Interview").enabled is False
    assert _checkbox(user, "Interview").value is False
    assert _checkbox(user, "Rejected").enabled is True
    assert _checkbox(user, "Rejected").value is True


async def test_checkbox_states_rejected_from_interview(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "interview")
    db.set_job_status(job_id, "rejected")
    await user.open("/")

    assert _checkbox(user, "Applied").enabled is False
    assert _checkbox(user, "Applied").value is True
    assert _checkbox(user, "Interview").enabled is False
    assert _checkbox(user, "Interview").value is True
    assert _checkbox(user, "Rejected").enabled is True
    assert _checkbox(user, "Rejected").value is True


async def test_checkbox_states_outdated(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "outdated")
    await user.open("/")

    assert _checkbox(user, "Outdated").enabled is False
    assert _checkbox(user, "Outdated").value is True
    assert _checkbox(user, "Applied").enabled is True
    assert _checkbox(user, "Applied").value is False
    assert _checkbox(user, "Interview").enabled is True
    assert _checkbox(user, "Rejected").enabled is True


async def test_outdated_checkbox_parks_position(user: User) -> None:
    _seed_job()
    await user.open("/")

    user.find(kind=ui.checkbox, content="Outdated").click()
    await _wait_for(lambda: db.get_jobs()[0]["outdated"] == 1)

    assert db.job_status(db.get_jobs()[0]) == "outdated"
    await user.should_see("No unapplied positions yet.")
    assert _checkbox(user, "Outdated").value is True


async def test_applied_checkbox_restores_parked_position(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "outdated")
    await user.open("/")

    user.find(kind=ui.checkbox, content="Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    job = db.get_jobs()[0]
    assert db.job_status(job) == "applied"
    assert job["date_applied"] is not None
    assert job["date_outdated"] is None


async def test_delete_requires_confirmation(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    job_id = db.get_jobs()[0]["id"]
    user.find("🗑️ Delete").click()
    with user.scope(marker=f"delete-dialog-{job_id}") as scoped:
        dialog = cast(ui.dialog, scoped)
        await _wait_for(lambda: dialog.value is True)
        user.find("Cancel").click()

    await _wait_for(lambda: dialog.value is False)
    assert len(db.get_jobs()) == 1


async def test_delete_confirmed_removes_position(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    job_id = db.get_jobs()[0]["id"]
    user.find("🗑️ Delete").click()
    user.find(marker=f"delete-confirm-{job_id}").click()

    await _wait_for(lambda: db.get_jobs() == [])
    await _see_notice(user, "Position deleted.")


async def test_delete_from_detail_returns_to_dashboard(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    user.find("🗑️ Delete").click()
    user.find(marker=f"delete-confirm-{job_id}").click()

    await user.should_see("Job URL", retries=20)
    assert db.get_jobs() == []


async def test_add_job_manually(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/1")
        user.find("Title").clear().type("Manual Engineer")
        user.find("Company").clear().type("ManualCo")
        user.find("Location").clear().type("Remote")
        user.find("Description").clear().type("Manual body")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: len(db.get_jobs()) == 1)
    await _see_notice(user, "Position added.")
    job = db.get_jobs()[0]
    assert job["job_url"] == "https://manual.example/1"
    assert job["title"] == "Manual Engineer"
    assert job["company"] == "ManualCo"
    assert job["description"] == "Manual body"


async def test_add_job_manually_shows_similar_notice(user: User) -> None:
    _seed_job(title="Manual Engineer")
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/2")
        user.find("Title").clear().type("Manual Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Location").clear().type("Remote")
        user.find("Description").clear().type("Manual body")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: len(db.get_jobs()) == 2)
    await _see_notice(user, "Position added.")
    await user.should_see("Looks similar to")


async def test_add_job_manually_with_salary(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/3")
        user.find("Title").clear().type("Paid Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Description").clear().type("Body")
        cast(ui.number, user.find("Salary min").elements.pop()).set_value(60000)
        cast(ui.number, user.find("Salary max").elements.pop()).set_value(80000)
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: len(db.get_jobs()) == 1)
    job = db.get_jobs()[0]
    assert job["salary_min"] == 60000
    assert job["salary_max"] == 80000
    assert job["salary_currency"] == "EUR"
    assert job["salary_period"] == "year"


async def test_add_job_manually_rejects_inverted_salary(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/4")
        user.find("Title").clear().type("Paid Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Description").clear().type("Body")
        cast(ui.number, user.find("Salary min").elements.pop()).set_value(90000)
        cast(ui.number, user.find("Salary max").elements.pop()).set_value(50000)
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await asyncio.sleep(0.1)
    assert db.get_jobs() == []


async def test_add_job_manually_with_deadline(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/5")
        user.find("Title").clear().type("Dated Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Description").clear().type("Body")
        user.find("Deadline").clear().type("2026-12-31")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: len(db.get_jobs()) == 1)
    assert db.get_jobs()[0]["deadline"] == "2026-12-31"


async def test_add_job_manually_rejects_invalid_deadline(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/6")
        user.find("Title").clear().type("Dated Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Description").clear().type("Body")
        user.find("Deadline").clear().type("soon")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await asyncio.sleep(0.1)
    assert db.get_jobs() == []


async def test_card_and_detail_show_salary(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/paid",
        "Paid Engineer",
        "Acme",
        "Remote",
        "Body",
        salary_min=60000,
        salary_max=80000,
        salary_currency="EUR",
        salary_period="year",
    )
    job_id = db.get_jobs()[0]["id"]

    await user.open("/")
    await user.should_see("€60,000 – €80,000 / year")

    await user.open(f"/job/{job_id}")
    await user.should_see("€60,000 – €80,000 / year")


async def test_card_and_detail_show_deadline(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/dated",
        "Dated Engineer",
        "Acme",
        "Remote",
        "Body",
        deadline="2099-12-31",
    )
    job_id = db.get_jobs()[0]["id"]

    await user.open("/")
    await user.should_see("2099-12-31")
    assert user.find(marker="deadline").elements

    await user.open(f"/job/{job_id}")
    await user.should_see("2099-12-31")
    assert user.find(marker="deadline").elements


async def test_card_hides_deadline_when_unset(user: User) -> None:
    _seed_job()
    await user.open("/")

    await user.should_not_see(marker="deadline")


async def test_banner_shows_stale_unapplied_position(user: User) -> None:
    _seed_stale_job(15)

    await user.open("/")

    await user.should_see("1 position needs attention")
    await user.should_see("added 15 days ago, not applied")
    assert user.find(marker="attention-item").elements


async def test_banner_shows_imminent_deadline(user: User) -> None:
    db.init_db()
    now = time.localtime()
    in_three_days = date(now.tm_year, now.tm_mon, now.tm_mday) + timedelta(days=3)
    db.add_job(
        "https://example.com/dated",
        "Dated Lead",
        "Acme",
        "Remote",
        "Body",
        deadline=in_three_days.isoformat(),
    )

    await user.open("/")

    await user.should_see("deadline in 3 days")
    assert user.find(marker="attention-item").elements


async def test_banner_hidden_when_nothing_needs_attention(user: User) -> None:
    _seed_job()

    await user.open("/")

    await user.should_not_see(marker="attention-banner")


async def test_banner_clears_after_marking_applied(user: User) -> None:
    _seed_stale_job(15)
    await user.open("/")
    await user.should_see("added 15 days ago, not applied")

    user.find(kind=ui.checkbox, content="Applied").click()

    await user.should_not_see(marker="attention-banner")


async def test_banner_toggle_hides_and_persists(user: User) -> None:
    _seed_stale_job(15)
    await user.open("/")
    await user.should_see(marker="attention-banner")

    user.find(marker="settings-button").click()
    user.find(marker="attention-banner-toggle").click()

    await user.should_not_see(marker="attention-banner")
    assert db.get_setting("attention_notice") == "off"

    await user.open("/")
    await user.should_not_see(marker="attention-banner")


def _seed_stale_applied_job(days: int) -> int:
    db.init_db()
    job_id = db.add_job(JOB_URL, "Silent Lead", "Acme", "Remote", "Body")
    assert job_id is not None
    db.set_job_status(job_id, "applied")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET date_applied = ? WHERE id = ?", (_days_ago_stamp(days), job_id)
        )
    return job_id


async def test_banner_warns_before_parking(user: User) -> None:
    _seed_stale_applied_job(25)

    await user.open("/")

    await user.should_see("1 position needs attention")
    assert db.get_jobs()[0]["outdated"] == 0
    assert db.job_status(db.get_jobs()[0]) == "applied"


async def test_auto_parks_stale_applied_position(user: User) -> None:
    _seed_stale_applied_job(35)

    await user.open("/")

    await _wait_for(lambda: db.get_jobs()[0]["outdated"] == 1)
    assert db.job_status(db.get_jobs()[0]) == "outdated"
    await user.should_not_see(marker="attention-banner")


async def test_manual_restore_wins_over_parking(user: User) -> None:
    _seed_stale_applied_job(35)

    await user.open("/")
    await _wait_for(lambda: db.get_jobs()[0]["outdated"] == 1)

    user.find(kind=ui.checkbox, content="Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    await user.open("/")
    await user.should_see("Silent Lead")
    assert db.job_status(db.get_jobs()[0]) == "applied"
    assert db.get_jobs()[0]["outdated"] == 0


async def test_salary_filter_narrows_status_lists(user: User) -> None:
    db.init_db()
    db.add_job("https://example.com/unknown", "Unknown Salary", "Acme", "Remote", "Body")
    db.add_job(
        "https://example.com/paid",
        "Paid Role",
        "Acme",
        "Remote",
        "Body",
        salary_min=100000,
        salary_currency="EUR",
        salary_period="year",
    )
    await user.open("/")
    await user.should_see("Unknown Salary")
    await user.should_see("Paid Role")

    cast(ui.expansion, user.find(marker="advanced-filters").elements.pop()).open()
    cast(ui.number, user.find("Min salary").elements.pop()).set_value(50000)

    await user.should_not_see("Unknown Salary")
    await user.should_see("Paid Role")


async def test_sort_and_page_size_control_the_list(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Zulu role", "Zeta", "Remote", "Body")
    db.add_job("https://a.com/2", "Alpha role", "Alpha", "Remote", "Body")
    db.add_job("https://a.com/3", "Mike role", "Mid", "Remote", "Body")
    await user.open("/")

    cast(ui.select, user.find(marker="page-size").elements.pop()).set_value(1)

    await user.should_see("Showing 1-1 of 3")
    await user.should_see("Mike role")
    await user.should_not_see("Alpha role")
    await user.should_not_see("Zulu role")

    cast(ui.select, user.find(marker="sort-select").elements.pop()).set_value("company")

    await user.should_see("Alpha role")
    await user.should_not_see("Mike role")
    await user.should_not_see("Zulu role")


async def test_pagination_pages_through_large_list(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/0", "Oldest role", "Acme", "Remote", "Body")
    for i in range(1, 11):
        db.add_job(f"https://a.com/{i}", f"Filler {i}", "Acme", "Remote", "Body")
    db.add_job("https://a.com/99", "Newest role", "Acme", "Remote", "Body")
    await user.open("/")

    cast(ui.select, user.find(marker="page-size").elements.pop()).set_value(10)

    await user.should_see("Showing 1-10 of 12")
    await user.should_see("Newest role")
    await user.should_not_see("Oldest role")

    cast(ui.pagination, user.find(marker="pagination").elements.pop()).set_value(2)

    await user.should_see("Showing 11-12 of 12")
    await user.should_see("Oldest role")
    await user.should_not_see("Newest role")


async def test_search_results_are_paginated_without_sorting(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/0", "Oldest role", "Acme", "Remote", "Body")
    for i in range(1, 11):
        db.add_job(f"https://a.com/{i}", f"Filler {i}", "Acme", "Remote", "Body")
    db.add_job("https://a.com/99", "Newest role", "Acme", "Remote", "Body")
    await user.open("/")
    sort_element = cast(ui.select, user.find(marker="sort-select").elements.pop())

    user.find("Search company").clear().type("Acme")
    await user.should_see("[Unapplied]")

    assert not sort_element.visible

    cast(ui.select, user.find(marker="page-size").elements.pop()).set_value(10)
    await user.should_see("Showing 1-10 of 12")

    cast(ui.pagination, user.find(marker="pagination").elements.pop()).set_value(2)
    await user.should_see("Showing 11-12 of 12")


async def test_add_job_manually_requires_fields(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Save").click()
    await asyncio.sleep(0.1)

    assert db.get_jobs() == []
    with user.scope(marker="job-form"):
        assert cast(ui.input, user.find("Job URL").elements.pop()).error == "Job URL is required."
        assert cast(ui.input, user.find("Title").elements.pop()).error == "Title is required."
        assert cast(ui.input, user.find("Company").elements.pop()).error == "Company is required."
        assert (
            cast(ui.textarea, user.find("Description").elements.pop()).error
            == "Description is required."
        )


async def test_add_job_manually_rejects_invalid_url(user: User) -> None:
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("not-a-url")
        user.find("Title").clear().type("Title")
        user.find("Company").clear().type("Company")
        user.find("Description").clear().type("Description")
        await asyncio.sleep(0.1)
        user.find("Save").click()
    await asyncio.sleep(0.1)

    assert db.get_jobs() == []
    with user.scope(marker="job-form"):
        assert cast(ui.input, user.find("Job URL").elements.pop()).error is not None


async def test_edit_job_from_detail(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        user.find("Title").clear().type("Updated Title")
        user.find("Company").clear().type("UpdatedCo")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: db.get_jobs()[0]["title"] == "Updated Title")
    await _see_notice(user, "Position updated.")
    assert db.get_jobs()[0]["company"] == "UpdatedCo"


async def test_edit_form_updates_deadline(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/edit-deadline",
        "Dated Engineer",
        "Acme",
        "Remote",
        "Body",
        deadline="2026-12-31",
    )
    job_id = db.get_jobs()[0]["id"]
    await user.open(f"/job/{job_id}")

    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        user.find("Deadline").clear().type("2027-01-15")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: db.get_jobs()[0]["deadline"] == "2027-01-15")


async def test_edit_form_clears_deadline(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/clear-deadline",
        "Dated Engineer",
        "Acme",
        "Remote",
        "Body",
        deadline="2026-12-31",
    )
    job_id = db.get_jobs()[0]["id"]
    await user.open(f"/job/{job_id}")

    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        user.find("Deadline").clear()
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: db.get_jobs()[0]["deadline"] is None)


async def test_edit_job_rejects_duplicate_url(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "body one")
    db.add_job("https://b.com/2", "Other", "OtherCo", "Berlin", "body two")
    target = db.get_jobs()[0]

    await user.open(f"/job/{target['id']}")
    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://a.com/1")
        await asyncio.sleep(0.1)
        user.find("Save").click()
    await asyncio.sleep(0.1)

    await user.should_see("already exists")
    assert db.get_jobs()[0]["job_url"] == "https://b.com/2"


async def test_dashboard_lists_status_tabs(user: User) -> None:
    await user.open("/")

    await user.should_see("No unapplied positions yet.")
    await user.should_see("No applied positions yet.")
    await user.should_see("No interview positions yet.")
    await user.should_see("No rejected positions yet.")
    await user.should_see("No outdated positions yet.")


async def test_dashboard_shows_parked_position(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "outdated")

    await user.open("/")

    await user.should_see("Senior Engineer")
    await user.should_see("No unapplied positions yet.")


async def test_company_search_filters_cards(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA Engineer", "Adapty", "Remote", "Body")
    db.add_job("https://a.com/2", "QA Engineer", "Paysend", "Remote", "Body")
    await user.open("/")
    await user.should_see("Adapty")
    await user.should_see("Paysend")

    user.find("Search company").clear().type("Adapty")
    await asyncio.sleep(0.4)

    await user.should_see("Adapty")
    await user.should_not_see("Paysend")

    user.find("Search company").clear()
    await asyncio.sleep(0.4)
    await user.should_see("Paysend")


async def test_search_is_across_statuses(user: User) -> None:
    db.init_db()
    adapty_id = db.add_job("https://a.com/1", "QA Engineer", "Adapty", "Remote", "Body")
    paysend_id = db.add_job("https://a.com/2", "QA Engineer", "Paysend", "Remote", "Body")
    assert adapty_id is not None
    assert paysend_id is not None
    db.set_job_status(adapty_id, "applied")
    db.set_job_status(paysend_id, "rejected")
    await user.open("/")
    await user.should_see("Adapty")

    user.find("Search company").clear().type("Adapty")
    await asyncio.sleep(0.4)

    await user.should_see("Adapty")
    await user.should_see("[Applied]")
    await user.should_not_see("Paysend")

    user.find("Search company").clear()
    await asyncio.sleep(0.4)
    await user.should_see("Paysend")


async def test_card_comment_box_for_current_stage(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    await user.open("/")

    user.find("Applied comment").clear().type("Referred by a friend")
    user.find("💾 Save comment").click()

    await _wait_for(lambda: db.get_jobs()[0]["applied_comment"] == "Referred by a friend")
    await user.should_see("Comment saved.")


async def test_card_hides_comment_box_when_unapplied(user: User) -> None:
    _seed_job()
    await user.open("/")

    await user.should_not_see("Applied comment")


async def test_detail_shows_comments(user: User) -> None:
    job_id = _seed_job()
    db.update_job_comments(job_id, "applied note", "interview note", "rejected note")
    await user.open(f"/job/{job_id}")

    await user.should_see("**Applied comment:** applied note")
    await user.should_see("**Interview comment:** interview note")
    await user.should_see("**Rejected comment:** rejected note")


async def test_edit_dialog_updates_comments(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        user.find("Applied comment").clear().type("via referral")
        user.find("Interview comment").clear().type("great team")
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: db.get_jobs()[0]["applied_comment"] == "via referral")
    assert db.get_jobs()[0]["interview_comment"] == "great team"


async def test_statistics_tab_shows_counts(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "desc")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    await user.open("/")

    def stat(key: str) -> str:
        label = cast(ui.label, user.find(marker=f"stat-{key}").elements.pop())
        return str(label.text)

    assert stat("total") == "3"
    assert stat("unapplied") == "1"
    assert stat("applied") == "1"
    assert stat("interview") == "0"
    assert stat("rejected") == "1"
    assert stat("outdated") == "0"


async def test_statistics_tab_shows_salary_chart(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://a.com/1",
        "Paid",
        "Acme",
        "Remote",
        "desc",
        salary_min=70000,
        salary_max=90000,
        salary_currency="EUR",
        salary_period="year",
    )
    await user.open("/")
    user.find("📊 Statistics").click()

    await user.should_see("1 of 1 positions have salary data")
    chart = cast(ui.echart, user.find(marker="salary-chart").elements.pop())
    assert chart.options["series"][0]["data"] == [1]


async def test_statistics_without_salary_shows_hint(user: User) -> None:
    _seed_job()
    await user.open("/")
    user.find("📊 Statistics").click()

    await user.should_see("Add salary to positions to see the distribution.")


def test_fill_months() -> None:
    assert app._fill_months([("2026-01", 2), ("2026-04", 1)]) == (
        ["2026-01", "2026-02", "2026-03", "2026-04"],
        [2, 0, 0, 1],
    )
    assert app._fill_months([("2025-11", 3)]) == (["2025-11"], [3])
    assert app._fill_months([("2025-12", 1), ("2026-01", 2)]) == (
        ["2025-12", "2026-01"],
        [1, 2],
    )


async def test_statistics_status_pie(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "desc")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    await user.open("/")

    chart = cast(ui.echart, user.find(marker="status-chart").elements.pop())
    assert chart.options["series"][0]["data"] == [
        {"name": "Unapplied", "value": 1},
        {"name": "Applied", "value": 1},
        {"name": "Interview", "value": 0},
        {"name": "Rejected", "value": 1},
        {"name": "Outdated", "value": 0},
    ]


async def test_statistics_funnel(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "desc")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    await user.open("/")

    chart = cast(ui.echart, user.find(marker="funnel-chart").elements.pop())
    assert chart.options["series"][0]["data"] == [
        {"name": "Added", "value": 3},
        {"name": "Applied", "value": 2},
        {"name": "Response", "value": 1},
        {"name": "Interview", "value": 0},
    ]


async def test_statistics_timeline_chart(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "applied")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET date_applied = '2020-01-15 09:00:00' WHERE id = ?",
            (jobs[0]["id"],),
        )
        conn.commit()
    await user.open("/")

    chart = cast(ui.echart, user.find(marker="timeline-chart").elements.pop())
    labels = chart.options["xAxis"]["data"]
    values = chart.options["series"][0]["data"]
    assert labels[0] == "2020-01"
    assert labels[-1] == time.strftime("%Y-%m")
    assert values[0] == 1
    assert values[-1] == 1
    assert sum(values) == 2


async def test_statistics_charts_hidden_without_positions(user: User) -> None:
    db.init_db()
    await user.open("/")

    await user.should_see("No positions yet to display charts.")
    await user.should_not_see(marker="status-chart")
    await user.should_not_see(marker="funnel-chart")
    await user.should_not_see(marker="timeline-chart")


async def test_statistics_timeline_hint_without_applications(user: User) -> None:
    _seed_job()
    await user.open("/")

    await user.should_see("No applications recorded yet.")
    await user.should_see(marker="status-chart")
    await user.should_see(marker="funnel-chart")
    await user.should_not_see(marker="timeline-chart")


async def test_statistics_charts_use_dark_theme(user: User) -> None:
    _seed_job()
    await user.open("/")

    chart = cast(ui.echart, user.find(marker="status-chart").elements.pop())
    assert chart.props["theme"] is None

    user.find(ui.toggle).elements.pop().set_value("dark")

    def theme_is_dark() -> bool:
        try:
            current = cast(ui.echart, user.find(marker="status-chart").elements.pop())
        except AssertionError:
            return False
        return bool(current.props["theme"] == "dark")

    await _wait_for(theme_is_dark)


async def test_card_has_full_details_link(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _see_notice(user, "Saved 1")

    await user.should_see("View full details")


async def test_details_view_shows_full_description(user: User) -> None:
    job_id = _seed_job(description="## Responsibilities\n- Build things")
    await user.open(f"/job/{job_id}")

    await user.should_see("Responsibilities")
    await user.should_see("**Company:** Acme")
    await user.should_see("**Location:** Remote")


async def test_detail_description_is_constrained(user: User) -> None:
    job_id = _seed_job(description="A very long description that should not overflow.")
    await user.open(f"/job/{job_id}")

    element = user.find(marker="job-description").elements.pop()
    assert "job-description" in element.classes


async def test_details_view_shows_tracking_fields(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "interview")
    await user.open(f"/job/{job_id}")

    await user.should_see("**Status:** Interview")
    await user.should_see("**Applied:**")
    await user.should_see("**Interview:**")
    await user.should_see("**Added:**")


async def test_detail_shows_outdated_stage(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "outdated")
    await user.open(f"/job/{job_id}")

    await user.should_see("**Status:** Outdated")
    await user.should_see("**Outdated:**")
    checkbox = _checkbox(user, "Outdated")
    assert checkbox.value is True
    assert checkbox.enabled is False


async def test_details_view_unknown_id_warns(user: User) -> None:
    await user.open("/job/999")

    await _see_notice(user, "Position not found.")


async def test_detail_shows_history_timeline(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "interview")
    await user.open(f"/job/{job_id}")

    await user.should_see("History")
    titles = {entry.props["title"] for entry in user.find(ui.timeline_entry).elements}
    assert titles == {"Unapplied", "Applied", "Interview"}


async def test_detail_checkbox_changes_status(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    user.find(kind=ui.checkbox, content="Applied").click()
    await user.should_see("**Status:** Applied")

    job = db.get_job(job_id)
    assert job is not None
    assert db.job_status(job) == "applied"
    assert _checkbox(user, "Applied").value is True
    assert any(event["status"] == "applied" for event in db.get_events(job_id))


async def test_detail_checkbox_respects_disabled_states(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    await user.open(f"/job/{job_id}")

    assert _checkbox(user, "Applied").enabled is False
    assert _checkbox(user, "Applied").value is True
    assert _checkbox(user, "Interview").enabled is True
    assert _checkbox(user, "Rejected").enabled is True


async def test_detail_profile_contact_shows_and_copies(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _seed_job()
    profile_id = db.create_profile("Primary")
    assert profile_id is not None
    db.save_profile(_profile_payload(profile_id=profile_id, name="Primary"))
    await user.open(f"/job/{job_id}")

    await user.should_see(marker="profile-summary")
    name_field = next(iter(user.find(kind=ui.input, marker="detail-field-name").elements))
    assert name_field.value == "Jane Doe"

    copied: list[str] = []
    monkeypatch.setattr(ui.clipboard, "write", copied.append)
    user.find(marker="detail-copy-email").click()

    await user.should_see("Copied Email!")
    assert copied == ["jane@example.com"]


async def test_detail_profile_section_hidden_without_profiles(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    await user.should_not_see(marker="profile-summary")


async def test_detail_profile_lists_multiple_profiles(user: User) -> None:
    job_id = _seed_job()
    work_id = db.create_profile("Work")
    personal_id = db.create_profile("Personal")
    assert work_id is not None
    assert personal_id is not None
    db.save_profile(_profile_payload(profile_id=work_id, name="Work", full_name="Alice Async"))
    db.save_profile(
        _profile_payload(profile_id=personal_id, name="Personal", full_name="Bob Builder")
    )
    await user.open(f"/job/{job_id}")

    await user.should_see("Work")
    await user.should_see("Personal")
    name_fields = user.find(kind=ui.input, marker="detail-field-name").elements
    assert {field.value for field in name_fields} == {"Alice Async", "Bob Builder"}


def test_days_since_applied_handles_missing_and_bad_dates() -> None:
    assert app._days_since_applied(cast(db.Job, {"date_applied": None})) is None
    assert app._days_since_applied(cast(db.Job, {"date_applied": "not-a-date"})) is None

    now = time.localtime()
    today = date(now.tm_year, now.tm_mon, now.tm_mday)
    three_days_ago = (today - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert app._days_since_applied(cast(db.Job, {"date_applied": three_days_ago})) == 3


def test_deadline_text_relative_phrases() -> None:
    today = date(2026, 10, 8)
    assert app._deadline_text(None, today) is None
    assert app._deadline_text("", today) is None
    assert app._deadline_text("not-a-date", today) is None
    assert app._deadline_text("2026-10-11", today) == "— in 3 days"
    assert app._deadline_text("2026-10-09", today) == "— in 1 day"
    assert app._deadline_text("2026-10-08", today) == "— is today"
    assert app._deadline_text("2026-10-07", today) == "— passed 1 day ago"
    assert app._deadline_text("2026-10-06", today) == "— passed 2 days ago"


def test_attention_items_orders_deadlines_before_staleness() -> None:
    today = date(2026, 10, 8)
    thresholds = attention.Thresholds(21, 10, 7)

    def entry(job_id: int, **overrides: object) -> db.Job:
        row: dict[str, object] = {
            "id": job_id,
            "applied": 0,
            "interview": 0,
            "rejected": 0,
            "outdated": 0,
            "date_added": "2026-10-01 09:00:00",
            "date_applied": None,
            "date_interview": None,
            "date_rejected": None,
            "deadline": None,
        }
        row.update(overrides)
        return cast(db.Job, row)

    stale_older = entry(1, date_added="2020-01-01 09:00:00")
    stale_newer = entry(2, date_added="2025-06-01 09:00:00")
    dated_soon = entry(3, deadline="2026-10-10")
    dated_later = entry(4, deadline="2026-10-14")

    items = app._attention_items(
        [stale_newer, dated_later, stale_older, dated_soon], thresholds, today=today
    )

    assert [job["id"] for job, _reasons in items] == [3, 4, 1, 2]


async def test_card_shows_days_since_applied(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    await user.open("/")

    badge = cast(ui.label, user.find(marker="days-since-applied").elements.pop())
    assert str(badge.text) == "Applied 0 days ago"


async def test_card_hides_days_since_applied_when_unapplied(user: User) -> None:
    _seed_job()
    await user.open("/")

    await user.should_not_see(marker="days-since-applied")


async def test_details_back_button_returns_to_list(user: User) -> None:
    job_id = _seed_job()
    await user.open(f"/job/{job_id}")

    user.find("← Back to list").click()

    await user.should_see("Job URL", retries=20)


def _clear_button(user: User) -> ui.button:
    return cast(ui.button, user.find("Confirm & Clear All Data").elements.pop())


async def test_clear_button_disabled_initially(user: User) -> None:
    await user.open("/")
    assert _clear_button(user).enabled is False


async def test_clear_button_stays_disabled_for_wrong_case(user: User) -> None:
    await user.open("/")
    user.find("Type 'DELETE' to confirm").type("delete")
    await asyncio.sleep(0.1)

    assert _clear_button(user).enabled is False


async def test_clear_button_enabled_with_exact_delete(user: User) -> None:
    await user.open("/")
    user.find("Type 'DELETE' to confirm").type("DELETE")
    await asyncio.sleep(0.1)

    assert _clear_button(user).enabled is True


async def test_clear_database_removes_all_jobs(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await _wait_for(lambda: len(db.get_jobs()) == 1)

    user.find("Type 'DELETE' to confirm").type("DELETE")
    await asyncio.sleep(0.1)
    user.find("Confirm & Clear All Data").click()

    await _wait_for(lambda: db.get_jobs() == [])
    await user.should_see("Successfully cleared 1 positions.")


async def test_profile_tab_renders(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    for label in (
        "Full Name",
        "Email",
        "LinkedIn URL",
        "Location",
        "Phone Number",
        "GitHub URL",
        "Summary",
        "Work History",
        "Education",
        "Skills",
    ):
        await user.should_see(label)


async def test_profile_prefills_existing_values(user: User) -> None:
    db.init_db()
    db.save_profile(_profile_payload(full_name="Jane Doe", summary="# Jane"))
    await user.open("/")

    name_input = cast(ui.input, user.find("Full Name").elements.pop())
    summary_input = cast(ui.textarea, user.find("Summary").elements.pop())
    assert name_input.value == "Jane Doe"
    assert summary_input.value == "# Jane"


async def test_profile_save_persists(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")
    user.find("Full Name").clear().type("Jane Doe")
    user.find("Email").clear().type("jane@example.com")
    user.find("Summary").clear().type("# Jane Doe\n\nNew CV summary")
    user.find("Work History").clear().type("## Work History\n\n- Acme")
    user.find("Education").clear().type("## Education\n\nBSc")
    user.find("Skills").clear().type("## Skills\n\n- Python")
    await asyncio.sleep(0.1)

    user.find("💾 Save Profile & CV").click()

    await user.should_see("Profile and CV saved successfully!")
    profile = db.get_profile()
    assert profile["full_name"] == "Jane Doe"
    assert profile["email"] == "jane@example.com"
    assert profile["summary"] == "# Jane Doe\n\nNew CV summary"
    assert profile["work_history"] == "## Work History\n\n- Acme"
    assert profile["education"] == "## Education\n\nBSc"
    assert profile["skills"] == "## Skills\n\n- Python"


async def test_profile_preview_combines_sections_in_order(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")
    user.find("Summary").clear().type("MARK_SUMMARY")
    user.find("Work History").clear().type("MARK_WORK")
    user.find("Education").clear().type("MARK_EDUCATION")
    user.find("Skills").clear().type("MARK_SKILLS")
    await asyncio.sleep(0.1)

    contents = [
        element.content
        for element in user.find(ui.markdown).elements
        if isinstance(element, ui.markdown)
    ]
    combined = next(
        content for content in contents if "MARK_SUMMARY" in content and "MARK_SKILLS" in content
    )
    markers = ["MARK_SUMMARY", "MARK_WORK", "MARK_EDUCATION", "MARK_SKILLS"]
    positions = [combined.index(marker) for marker in markers]
    assert positions == sorted(positions)


async def test_profile_export_button_renders(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    await user.should_see("📄 Export to PDF")


async def test_profile_export_downloads_pdf(user: User) -> None:
    db.init_db()
    db.save_profile(_profile_payload())
    await user.open("/")

    user.find("📄 Export to PDF").click()

    response = await user.download.next()
    assert response.content.startswith(b"%PDF")


def _profile_select(user: User) -> ui.select:
    return cast(ui.select, user.find(marker="profile-select").elements.pop())


async def test_profile_copy_buttons_render(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    for marker in (
        "copy-full-name",
        "copy-email",
        "copy-phone",
        "copy-linkedin",
        "copy-github",
        "copy-summary",
        "copy-work-history",
        "copy-education",
        "copy-skills",
    ):
        user.find(marker=marker)


async def test_profile_copy_copies_value_and_notifies(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")
    user.find("Email").clear().type("jane@example.com")
    await asyncio.sleep(0.1)

    copied: list[str] = []
    monkeypatch.setattr(ui.clipboard, "write", copied.append)

    user.find(marker="copy-email").click()

    await user.should_see("Copied Email!")
    assert copied == ["jane@example.com"]


async def test_profile_copy_empty_field_warns(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    user.find(marker="copy-phone").click()

    await user.should_see("Phone Number is empty")


async def test_profile_copy_textarea_copies_value_and_notifies(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")
    user.find("Summary").clear().type("MARK_SUMMARY")
    await asyncio.sleep(0.1)

    copied: list[str] = []
    monkeypatch.setattr(ui.clipboard, "write", copied.append)

    user.find(marker="copy-summary").click()

    await user.should_see("Copied Summary!")
    assert copied == ["MARK_SUMMARY"]


async def test_profile_copy_empty_textarea_warns(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    user.find(marker="copy-skills").click()

    await user.should_see("Skills is empty")


async def test_profile_empty_state_without_profiles(user: User) -> None:
    db.init_db()
    await user.open("/")

    await user.should_see("No profiles yet. Add a profile to create your CV.")
    await user.should_not_see(marker="profile-select")
    await user.should_not_see(content="💾 Save Profile & CV")


async def test_profile_dropdown_hidden_with_single_profile(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    await user.should_not_see(marker="profile-select")
    await user.should_see("💾 Save Profile & CV")


async def test_profile_dropdown_switches_between_profiles(user: User) -> None:
    db.init_db()
    first = db.create_profile("Alpha CV")
    second = db.create_profile("Beta CV")
    assert first is not None and second is not None
    db.save_profile(_profile_payload(full_name="Alpha Person", profile_id=first, name="Alpha CV"))
    db.save_profile(_profile_payload(full_name="Beta Person", profile_id=second, name="Beta CV"))
    await user.open("/")

    select = _profile_select(user)
    assert select.value == first
    assert cast(ui.input, user.find("Full Name").elements.pop()).value == "Alpha Person"

    select.set_value(second)
    await asyncio.sleep(0.1)

    assert cast(ui.input, user.find("Full Name").elements.pop()).value == "Beta Person"

    user.find("Full Name").clear().type("Beta Edited")
    await asyncio.sleep(0.1)
    user.find("💾 Save Profile & CV").click()
    await user.should_see("Profile and CV saved successfully!")

    assert db.get_profile(first)["full_name"] == "Alpha Person"
    assert db.get_profile(second)["full_name"] == "Beta Edited"


async def test_profile_add_dialog_creates_and_selects_profile(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    user.find(marker="add-profile").click()
    user.find(marker="new-profile-name").type("Work CV")
    user.find(marker="confirm-add-profile").click()

    await user.should_see("Profile 'Work CV' created.")
    assert [p["name"] for p in db.list_profiles()] == ["Default", "Work CV"]
    await user.should_see(marker="profile-select")
    assert cast(ui.input, user.find("Full Name").elements.pop()).value in ("", None)


async def test_profile_add_rejects_duplicate_name(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    user.find(marker="add-profile").click()
    user.find(marker="new-profile-name").type("default")
    user.find(marker="confirm-add-profile").click()

    await user.should_see("Profile 'default' already exists.")
    assert [p["name"] for p in db.list_profiles()] == ["Default"]


async def test_profile_add_rejects_empty_name(user: User) -> None:
    db.init_db()
    db.create_profile("Default")
    await user.open("/")

    user.find(marker="add-profile").click()
    user.find(marker="confirm-add-profile").click()

    await user.should_see("Enter a profile name.")
    assert [p["name"] for p in db.list_profiles()] == ["Default"]


async def test_profile_delete_requires_typing_delete(user: User) -> None:
    db.init_db()
    keep = db.create_profile("Keep")
    drop = db.create_profile("Drop")
    assert keep is not None and drop is not None
    await user.open("/")

    _profile_select(user).set_value(drop)
    await asyncio.sleep(0.1)

    user.find(marker="delete-profile").click()
    await user.should_see("Profile 'Drop'")
    confirm_button = cast(ui.button, user.find(marker="confirm-delete-profile").elements.pop())
    assert confirm_button.enabled is False

    user.find(marker="delete-profile-confirm").type("delete")
    await asyncio.sleep(0.1)
    confirm_button = cast(ui.button, user.find(marker="confirm-delete-profile").elements.pop())
    assert confirm_button.enabled is False

    user.find(marker="delete-profile-confirm").clear().type("DELETE")
    await asyncio.sleep(0.1)
    confirm_button = cast(ui.button, user.find(marker="confirm-delete-profile").elements.pop())
    assert confirm_button.enabled is True

    user.find(marker="confirm-delete-profile").click()

    await user.should_see("Profile deleted.")
    assert [p["id"] for p in db.list_profiles()] == [keep]
    await user.should_not_see(marker="profile-select")


async def test_profile_delete_last_shows_empty_state(user: User) -> None:
    db.init_db()
    db.create_profile("Only")
    await user.open("/")

    user.find(marker="delete-profile").click()
    user.find(marker="delete-profile-confirm").type("DELETE")
    await asyncio.sleep(0.1)
    user.find(marker="confirm-delete-profile").click()

    await user.should_see("No profiles yet. Add a profile to create your CV.")
    await user.should_not_see(marker="profile-select")
    assert db.list_profiles() == []

    user.find(marker="add-profile").click()
    user.find(marker="new-profile-name").type("Fresh CV")
    user.find(marker="confirm-add-profile").click()

    await user.should_see("Profile 'Fresh CV' created.")
    assert [p["name"] for p in db.list_profiles()] == ["Fresh CV"]


async def test_profile_selection_persists_across_reloads(user: User) -> None:
    db.init_db()
    first = db.create_profile("Alpha CV")
    second = db.create_profile("Beta CV")
    assert first is not None and second is not None
    await user.open("/")

    _profile_select(user).set_value(second)
    await asyncio.sleep(0.1)

    await user.open("/")

    assert _profile_select(user).value == second
    assert db.get_setting(app.SELECTED_PROFILE_KEY) == str(second)


async def test_profile_export_uses_selected_profile(user: User) -> None:
    db.init_db()
    first = db.create_profile("Alpha CV")
    second = db.create_profile("Beta CV")
    assert first is not None and second is not None
    db.save_profile(_profile_payload(full_name="Alpha Person", profile_id=first, name="Alpha CV"))
    db.save_profile(_profile_payload(full_name="Beta Person", profile_id=second, name="Beta CV"))
    await user.open("/")

    _profile_select(user).set_value(second)
    await asyncio.sleep(0.1)
    user.find("📄 Export to PDF").click()

    response = await user.download.next()
    assert response.content.startswith(b"%PDF")
    reader = PdfReader(BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Beta Person" in text
    assert "Alpha Person" not in text


async def test_theme_defaults_to_system(user: User) -> None:
    await user.open("/")

    dark = user.find(ui.dark_mode).elements.pop()
    assert dark.value is None


async def test_settings_drawer_is_wide(user: User) -> None:
    await user.open("/")

    drawer = user.find(ui.right_drawer).elements.pop()
    assert drawer.props["width"] == "480"


async def test_main_tabs_render_inside_header(user: User) -> None:
    await user.open("/")

    stat_tab = cast(ui.tab, user.find("📊 Statistics").elements.pop())
    ancestors = list(stat_tab.ancestors(include_self=True))
    assert any(isinstance(element, ui.header) for element in ancestors)
    tabs_element = next(element for element in ancestors if isinstance(element, ui.tabs))
    assert "header-tabs" in tabs_element.classes


def test_global_css_styles_all_textareas() -> None:
    css = app.load_webapp_css()
    assert ".q-textarea .q-field__native" in css
    assert "line-height: 1.7" in css


def test_global_css_scrolls_header_tabs_when_narrow() -> None:
    css = app.load_webapp_css()
    assert ".header-tabs .q-tabs__content" in css
    assert "overflow-x: auto" in css
    assert "@media (max-width: 999px)" in css


def test_global_css_wraps_markdown_pre() -> None:
    css = app.load_webapp_css()
    assert ".nicegui-markdown pre" in css
    assert "white-space: pre-wrap" in css


def test_load_webapp_css_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Webapp stylesheet not found"):
        app.load_webapp_css(tmp_path / "missing.css")


async def test_theme_toggle_persists_dark(user: User) -> None:
    await user.open("/")

    user.find(ui.toggle).elements.pop().set_value("dark")
    await _wait_for(lambda: db.get_setting("dark_mode") == "dark")

    dark = user.find(ui.dark_mode).elements.pop()
    assert dark.value is True

    await user.open("/")
    reloaded = user.find(ui.dark_mode).elements.pop()
    assert reloaded.value is True


def test_run_forwards_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("NICEGUI_USER_SIMULATION", "true")
    captured: dict[str, object] = {}
    monkeypatch.setattr("cursustrace.app.ui.run", lambda **kwargs: captured.update(kwargs))

    settings = config.Settings(
        host="127.0.0.1", port=9002, reload=True, show=True, backup_on_start=False
    )
    try:
        app.run(settings)
        assert captured["host"] == "127.0.0.1"
        assert captured["port"] == 9002
        assert captured["reload"] is True
        assert captured["show"] is True
        assert app._settings is settings
        assert (tmp_path / "logs" / f"cursustrace-{logsetup._today().isoformat()}.log").exists()
    finally:
        app._settings = None


def test_run_configures_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setenv("NICEGUI_USER_SIMULATION", "true")
    monkeypatch.setattr("cursustrace.app.ui.run", lambda **kwargs: None)
    captured: dict[str, object] = {}

    def fake_setup(level: str, log_dir: Path | None = None, retention_days: int = 7) -> None:
        captured["level"] = level
        captured["retention"] = retention_days

    monkeypatch.setattr("cursustrace.app.setup_logging", fake_setup)

    settings = config.Settings(log_level="debug", log_retention_days=3, backup_on_start=False)
    try:
        app.run(settings)
        assert captured == {"level": "debug", "retention": 3}
    finally:
        app._settings = None


def test_cv_style_path_uses_settings() -> None:
    app._settings = config.Settings(cv_style_path=Path("custom.css"))
    try:
        assert app._cv_style_path() == Path("custom.css")
        app._settings = None
        assert app._cv_style_path() is None
    finally:
        app._settings = None


def _prepare_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setenv("NICEGUI_USER_SIMULATION", "true")
    captured: dict[str, object] = {}
    monkeypatch.setattr("cursustrace.app.ui.run", lambda **kwargs: captured.update(kwargs))
    return captured


def test_run_prints_config_source_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_run(tmp_path, monkeypatch)
    try:
        app.run(config.Settings(backup_on_start=False))
    finally:
        app._settings = None

    out = capsys.readouterr().out
    assert "Config: no config file found, using hardcoded default values" in out


def test_run_prints_config_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_run(tmp_path, monkeypatch)
    settings = config.Settings(config_file=Path("custom.toml"), backup_on_start=False)
    try:
        app.run(settings)
    finally:
        app._settings = None

    assert "Config: using config file custom.toml" in capsys.readouterr().out


def test_run_writes_startup_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_run(tmp_path, monkeypatch)
    dest = tmp_path / "backups"
    settings = config.Settings(backup_dir=dest, backup_keep=5)
    try:
        app.run(settings)
        assert len(list(dest.glob("cursustrace-*.db"))) == 1
    finally:
        app._settings = None

    out = capsys.readouterr().out
    assert f"Backup completed successfully to {dest}/" in out


def test_run_skips_backup_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_run(tmp_path, monkeypatch)
    dest = tmp_path / "backups"
    settings = config.Settings(backup_dir=dest, backup_on_start=False)
    try:
        app.run(settings)
        assert dest.is_dir() is False
    finally:
        app._settings = None

    assert "Backup skipped: backup_on_start is disabled" in capsys.readouterr().out


def test_run_survives_backup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _prepare_run(tmp_path, monkeypatch)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("cursustrace.app.backup.backup_database", explode)
    settings = config.Settings(backup_dir=tmp_path / "backups", backup_on_start=True)
    try:
        app.run(settings)
        assert "host" in captured
    finally:
        app._settings = None

    assert "Backup failed: disk full" in capsys.readouterr().out


def test_backup_on_startup_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "missing.db")
    settings = config.Settings(backup_dir=tmp_path / "backups")

    app._backup_on_startup(settings)

    assert "Backup skipped: no database file yet" in capsys.readouterr().out


# --- Tags -------------------------------------------------------------------


def _tagged_pair() -> tuple[int, int]:
    """Create two unapplied jobs; the 'Zeta' one carries a golang tag."""
    db.init_db()
    db.add_job("https://a.com/1", "Zulu role", "Zeta", "Remote", "Body")
    db.add_job("https://a.com/2", "Alpha role", "Alpha", "Remote", "Body")
    ids = {job["company"]: job["id"] for job in db.get_jobs()}
    db.set_job_tags(ids["Zeta"], ["golang"])
    return ids["Zeta"], ids["Alpha"]


async def test_add_job_manually_assigns_tags(user: User) -> None:
    db.init_db()
    db.create_tag("remote")
    db.create_tag("startup")
    await user.open("/")
    user.find("➕ Add Manually").click()
    with user.scope(marker="job-form"):
        user.find("Job URL").clear().type("https://manual.example/tags")
        user.find("Title").clear().type("Tagged Engineer")
        user.find("Company").clear().type("Acme")
        user.find("Description").clear().type("Body")
        cast(ui.select, user.find(marker="tags-field").elements.pop()).set_value(
            ["remote", "startup", "brand-new"]
        )
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: len(db.get_jobs()) == 1)
    job_id = db.get_jobs()[0]["id"]
    assert db.job_tags(job_id) == ["brand-new", "remote", "startup"]  # alphabetical
    assert db.list_tags() == ["brand-new", "remote", "startup"]  # brand-new auto-created

    tag_select = cast(ui.select, user.find(marker="tag-filter").elements.pop())
    assert sorted(str(name) for name in tag_select.options) == [
        "brand-new",
        "remote",
        "startup",
    ]


async def test_edit_form_replaces_tags(user: User) -> None:
    db.init_db()
    job_id = _seed_job()
    db.set_job_tags(job_id, ["remote"])
    await user.open(f"/job/{job_id}")

    user.find("✏️ Edit").click()
    with user.scope(marker="job-form"):
        cast(ui.select, user.find(marker="tags-field").elements.pop()).set_value(["referral"])
        await asyncio.sleep(0.1)
        user.find("Save").click()

    await _wait_for(lambda: db.job_tags(job_id) == ["referral"])
    assert db.list_tags() == ["referral", "remote"]  # unused tags stay in the catalog


async def test_cards_and_detail_show_tag_chips(user: User) -> None:
    db.init_db()
    job_id = _seed_job()
    db.set_job_tags(job_id, ["golang", "referral"])

    await user.open("/")
    await user.should_see("golang")
    await user.should_see("referral")

    await user.open(f"/job/{job_id}")
    await user.should_see("golang")
    await user.should_see("referral")


async def test_tag_filter_select_narrows_list(user: User) -> None:
    _tagged_pair()
    await user.open("/")
    await user.should_see("Zulu role")
    await user.should_see("Alpha role")

    cast(ui.select, user.find(marker="tag-filter").elements.pop()).set_value(["golang"])

    await user.should_see("Zulu role")
    await user.should_not_see("Alpha role")


async def test_clicking_chip_filters_dashboard(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Zulu role", "Zeta", "Remote", "Body")
    db.add_job("https://a.com/2", "Alpha role", "Alpha", "Remote", "Body")
    ids = {job["company"]: job["id"] for job in db.get_jobs()}
    db.set_job_tags(ids["Zeta"], ["golang"])
    db.set_job_tags(ids["Alpha"], ["rust"])
    await user.open("/")
    await user.should_see("Zulu role")
    await user.should_see("Alpha role")
    advanced = cast(ui.expansion, user.find(marker="advanced-filters").elements.pop())
    assert advanced.value is False

    user.find(kind=ui.chip, content="golang").click()

    tag_select = cast(ui.select, user.find(marker="tag-filter").elements.pop())
    await _wait_for(lambda: list(tag_select.value or []) == ["golang"])
    assert advanced.value is True  # chip click expands the Advanced bar
    await user.should_see("Zulu role")
    await user.should_not_see("Alpha role")


async def test_detail_chip_navigates_to_filtered_dashboard(user: User) -> None:
    db.init_db()
    tagged_id = _seed_job(title="Tagged role")
    db.add_job("https://a.com/other", "Untagged role", "OtherCo", "Berlin", "Body")
    db.set_job_tags(tagged_id, ["golang"])
    await user.open(f"/job/{tagged_id}")

    user.find(kind=ui.chip, content="golang").click()

    await user.should_see("Filter by tag")  # dashboard reloaded with the tag filter
    await user.should_see("Tagged role")
    await user.should_not_see("Untagged role")
    tag_select = cast(ui.select, user.find(marker="tag-filter").elements.pop())
    assert list(tag_select.value or []) == ["golang"]
    advanced = cast(ui.expansion, user.find(marker="advanced-filters").elements.pop())
    assert advanced.value is True  # deep-linked tag opens the Advanced bar


async def test_opening_with_tag_query_filters_dashboard(user: User) -> None:
    _tagged_pair()
    await user.open("/?tag=golang")

    tag_select = cast(ui.select, user.find(marker="tag-filter").elements.pop())
    assert list(tag_select.value or []) == ["golang"]
    await user.should_see("Zulu role")
    await user.should_not_see("Alpha role")


async def test_advanced_filters_hidden_during_search(user: User) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Zulu role", "Zeta", "Remote", "Body")
    await user.open("/")
    advanced = cast(ui.expansion, user.find(marker="advanced-filters").elements.pop())
    assert advanced.visible

    user.find("Search company").clear().type("Zeta")
    await user.should_see("[Unapplied]")
    assert not advanced.visible


async def test_tag_manager_adds_renames_and_deletes(user: User) -> None:
    db.init_db()
    await user.open("/")
    user.find(marker="settings-button").click()
    user.find(marker="manage-tags").click()

    with user.scope(marker="tag-manager"):
        user.find(marker="new-tag-name").clear().type("golang")
        user.find("Add tag").click()
    await _wait_for(lambda: db.list_tags() == ["golang"])
    await user.should_see("Tag 'golang' created.")

    tag_id = db.find_tag("golang")
    assert tag_id is not None
    with user.scope(marker="tag-manager"):
        cast(ui.input, user.find(marker=f"rename-tag-{tag_id}").elements.pop()).set_value("wfh")
        user.find("Rename").click()
    await _wait_for(lambda: db.list_tags() == ["wfh"])
    await user.should_see("Tag renamed.")

    wfh_id = db.find_tag("wfh")
    assert wfh_id is not None
    with user.scope(marker="tag-manager"):
        user.find(marker=f"delete-tag-{wfh_id}").click()
    with user.scope(marker="tag-delete-confirm"):
        await user.should_see("Delete tag 'wfh'?")
        user.find(marker="confirm-delete-tag").click()

    await _wait_for(lambda: db.list_tags() == [])
    await user.should_see("Tag deleted.")


async def test_manager_rename_keeps_assignments_and_filter(user: User) -> None:
    _tagged_pair()
    await user.open("/")
    user.find(marker="manage-tags").click()

    tag_id = db.find_tag("golang")
    assert tag_id is not None
    with user.scope(marker="tag-manager"):
        cast(ui.input, user.find(marker=f"rename-tag-{tag_id}").elements.pop()).set_value("go")
        user.find("Rename").click()
    await _wait_for(lambda: db.list_tags() == ["go"])

    job = next(job for job in db.get_jobs() if job["company"] == "Zeta")
    assert db.job_tags(job["id"]) == ["go"]

    tag_select = cast(ui.select, user.find(marker="tag-filter").elements.pop())
    await _wait_for(lambda: [str(name) for name in tag_select.options] == ["go"])


async def test_advanced_bar_collapsed_by_default(user: User) -> None:
    db.init_db()
    db.add_job(
        "https://a.com/1",
        "High role",
        "HighCo",
        "Remote",
        "Body",
        salary_min=90000,
        salary_currency="EUR",
        salary_period="year",
    )
    db.add_job(
        "https://a.com/2",
        "Low role",
        "LowCo",
        "Remote",
        "Body",
        salary_min=30000,
        salary_currency="EUR",
        salary_period="year",
    )
    await user.open("/")

    advanced = cast(ui.expansion, user.find(marker="advanced-filters").elements.pop())
    assert advanced.value is False  # default view: search, sort, pagination only
    await user.should_see("High role")
    await user.should_see("Low role")

    advanced.open()
    assert advanced.value is True
    cast(ui.number, user.find("Min salary").elements.pop()).set_value(50000)

    await user.should_see("High role")
    await user.should_not_see("Low role")


async def test_advanced_bar_shows_active_count(user: User) -> None:
    _tagged_pair()
    await user.open("/")
    advanced = cast(ui.expansion, user.find(marker="advanced-filters").elements.pop())
    assert advanced.text == "Advanced filters"

    cast(ui.select, user.find(marker="tag-filter").elements.pop()).set_value(["golang"])
    await _wait_for(lambda: advanced.text == "Advanced filters (1 active)")

    cast(ui.number, user.find("Min salary").elements.pop()).set_value(50000)
    await _wait_for(lambda: advanced.text == "Advanced filters (2 active)")

    cast(ui.select, user.find(marker="tag-filter").elements.pop()).set_value([])
    await _wait_for(lambda: advanced.text == "Advanced filters (1 active)")

    cast(ui.number, user.find("Min salary").elements.pop()).set_value(None)
    await _wait_for(lambda: advanced.text == "Advanced filters")
