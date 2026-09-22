"""NiceGUI UI tests for cursustrace using the user simulation."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import cast

import pytest
from nicegui import ui
from nicegui.testing import User

from cursustrace import app, db, scraper
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
    user.find("Job URL").clear().type(url)
    await asyncio.sleep(0.05)
    user.find("Scan & Save Position").click()
    await asyncio.sleep(0.05)


def _seed_job(title: str = "Senior Engineer", description: str = "Body text") -> int:
    db.init_db()
    db.add_job(JOB_URL, title, "Acme", "Remote", description)
    return db.get_jobs()[0]["id"]


def _profile_payload(full_name: str = "Jane Doe", cv_markdown: str = "# Jane") -> db.Profile:
    return {
        "full_name": full_name,
        "location": "Remote",
        "phone": "555-0100",
        "email": "jane@example.com",
        "linkedin_url": "https://linkedin.com/in/jane",
        "github_url": "https://github.com/jane",
        "cv_markdown": cv_markdown,
        "date_updated": None,
    }


async def test_title_renders(user: User) -> None:
    await user.open("/")
    await user.should_see(app.APP_TITLE)


async def test_scan_saves_position(user: User) -> None:
    await user.open("/")
    await _scan(user)

    await user.should_see("Position saved.")
    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Engineer"
    assert jobs[0]["company"] == "Acme"


async def test_duplicate_url_shows_warning(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    await _scan(user)

    await user.should_see("Position already exists in database")
    assert len(db.get_jobs()) == 1


async def test_duplicate_fingerprint_shows_warning(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body text",
        },
    )
    await _scan(user, "https://other.com/jobs/999")

    await user.should_see("Position already exists in database")
    assert len(db.get_jobs()) == 1


async def test_empty_url_shows_warning(user: User) -> None:
    await user.open("/")
    user.find("Scan & Save Position").click()

    await user.should_see("Please enter a job URL.")


async def test_scrape_error_shows_error(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_scrape(url: str) -> scraper.ScrapedJob:
        raise ScrapeError(f"boom: {url}")

    monkeypatch.setattr(scraper, "scrape_job", raise_scrape)
    await user.open("/")
    await _scan(user)

    await user.should_see("boom")
    assert db.get_jobs() == []


async def test_checkbox_marks_job_applied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    user.find(kind=ui.checkbox, content="Mark as Applied").click()

    await _wait_for(lambda: len(db.get_jobs(status="applied")) == 1)
    applied = db.get_jobs(status="applied")[0]
    assert TIMESTAMP_RE.match(applied["date_applied"] or "")
    assert db.get_jobs(status="unapplied") == []


async def test_checkbox_marks_job_interview(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    user.find(kind=ui.checkbox, content="Interview").click()

    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)
    assert job["date_interview"] is not None


async def test_checkbox_marks_job_rejected(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    user.find(kind=ui.checkbox, content="Rejected").click()

    await _wait_for(lambda: db.get_jobs()[0]["rejected"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 0, 1)
    assert job["date_rejected"] is not None


async def test_interview_replaces_applied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    user.find(kind=ui.checkbox, content="Mark as Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    user.find(kind=ui.checkbox, content="Interview").click()
    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)

    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)


async def test_unchecking_returns_to_unapplied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    user.find(kind=ui.checkbox, content="Mark as Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    user.find(kind=ui.checkbox, content="Mark as Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 0)

    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 0, 0)
    assert job["date_applied"] is None


async def test_dashboard_lists_status_tabs(user: User) -> None:
    await user.open("/")

    await user.should_see("No unapplied positions yet.")
    await user.should_see("No applied positions yet.")
    await user.should_see("No interview positions yet.")
    await user.should_see("No rejected positions yet.")


async def test_card_has_full_details_link(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Position saved.")

    await user.should_see("View full details")


async def test_details_view_shows_full_description(user: User) -> None:
    job_id = _seed_job(description="## Responsibilities\n- Build things")
    await user.open(f"/job/{job_id}")

    await user.should_see("Responsibilities")
    await user.should_see("**Company:** Acme")
    await user.should_see("**Location:** Remote")


async def test_details_view_shows_tracking_fields(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "interview")
    await user.open(f"/job/{job_id}")

    await user.should_see("**Status:** Interview")
    await user.should_see("**Applied:**")
    await user.should_see("**Interview:**")
    await user.should_see("**Added:**")


async def test_details_view_unknown_id_warns(user: User) -> None:
    await user.open("/job/999")

    await user.should_see("Position not found.")


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
    await user.open("/")

    for label in (
        "Full Name",
        "Email",
        "LinkedIn URL",
        "Location",
        "Phone Number",
        "GitHub URL",
    ):
        await user.should_see(label)


async def test_profile_prefills_existing_values(user: User) -> None:
    db.init_db()
    db.save_profile(_profile_payload(full_name="Jane Doe", cv_markdown="# Jane"))
    await user.open("/")

    name_input = cast(ui.input, user.find("Full Name").elements.pop())
    cv_input = cast(ui.textarea, user.find("Edit your CV in Markdown format").elements.pop())
    assert name_input.value == "Jane Doe"
    assert cv_input.value == "# Jane"


async def test_profile_save_persists(user: User) -> None:
    await user.open("/")
    user.find("Full Name").clear().type("Jane Doe")
    user.find("Email").clear().type("jane@example.com")
    user.find("Edit your CV in Markdown format").clear().type("# Jane Doe\n\nNew CV")
    await asyncio.sleep(0.1)

    user.find("💾 Save Profile & CV").click()

    await user.should_see("Profile and CV saved successfully!")
    profile = db.get_profile()
    assert profile["full_name"] == "Jane Doe"
    assert profile["email"] == "jane@example.com"
    assert profile["cv_markdown"] == "# Jane Doe\n\nNew CV"


async def test_profile_export_button_renders(user: User) -> None:
    await user.open("/")

    await user.should_see("📄 Export to PDF")


async def test_profile_export_downloads_pdf(user: User) -> None:
    db.init_db()
    db.save_profile(_profile_payload())
    await user.open("/")

    user.find("📄 Export to PDF").click()

    response = await user.download.next()
    assert response.content.startswith(b"%PDF")
