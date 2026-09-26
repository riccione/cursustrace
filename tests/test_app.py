"""NiceGUI UI tests for cursustrace using the user simulation."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pytest
from nicegui import ui
from nicegui.testing import User

from cursustrace import app, config, db, logsetup, scraper
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
    user.find("Scan & Save Positions").click()
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

    await user.should_see("Saved 1")
    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Engineer"
    assert jobs[0]["company"] == "Acme"


async def test_duplicate_url_shows_warning(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

    await _scan(user)

    await user.should_see("Duplicates 1")
    assert len(db.get_jobs()) == 1


async def test_duplicate_fingerprint_shows_warning(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

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

    await user.should_see("Duplicates 1")
    assert len(db.get_jobs()) == 1


async def test_empty_url_shows_warning(user: User) -> None:
    await user.open("/")
    user.find("Scan & Save Positions").click()

    await user.should_see("Please enter at least one job URL.")


async def test_scrape_error_shows_error(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_scrape(url: str) -> scraper.ScrapedJob:
        raise ScrapeError(f"boom: {url}")

    monkeypatch.setattr(scraper, "scrape_job", raise_scrape)
    await user.open("/")
    await _scan(user)

    await user.should_see("boom")
    assert db.get_jobs() == []


async def test_scan_multiple_urls_saves_all(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    def scrape(url: str) -> scraper.ScrapedJob:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "title": f"Engineer {slug}",
            "company": "Acme",
            "location": "Remote",
            "description": f"Body {slug}",
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    user.find("Job URL").clear().type(
        "https://example.com/jobs/1\nhttps://example.com/jobs/2\nhttps://example.com/jobs/3"
    )
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()

    await _wait_for(lambda: len(db.get_jobs()) == 3)
    await user.should_see("Saved 3")


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
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    await user.open("/")
    await _scan(user, "https://example.com/jobs/1")
    await user.should_see("Saved 1")

    user.find("Job URL").clear().type(
        "https://example.com/jobs/2\nhttps://example.com/jobs/1\nhttps://example.com/jobs/bad"
    )
    await asyncio.sleep(0.05)
    user.find("Scan & Save Positions").click()

    await _wait_for(lambda: len(db.get_jobs()) == 2)
    await user.should_see("Saved 1 · Duplicates 1 · Failed 1")
    assert db.get_jobs()[0]["job_url"] == "https://example.com/jobs/2"


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
    await user.should_see("Saved 1")

    user.find(kind=ui.checkbox, content="Applied").click()

    await _wait_for(lambda: len(db.get_jobs(status="applied")) == 1)
    applied = db.get_jobs(status="applied")[0]
    assert TIMESTAMP_RE.match(applied["date_applied"] or "")
    assert db.get_jobs(status="unapplied") == []


async def test_checkbox_marks_job_interview(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

    user.find(kind=ui.checkbox, content="Interview").click()

    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)
    assert job["date_interview"] is not None


async def test_checkbox_marks_job_rejected(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

    user.find(kind=ui.checkbox, content="Rejected").click()

    await _wait_for(lambda: db.get_jobs()[0]["rejected"] == 1)
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 0, 1)
    assert job["date_rejected"] is not None


async def test_interview_replaces_applied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

    user.find(kind=ui.checkbox, content="Applied").click()
    await _wait_for(lambda: db.get_jobs()[0]["applied"] == 1)

    user.find(kind=ui.checkbox, content="Interview").click()
    await _wait_for(lambda: db.get_jobs()[0]["interview"] == 1)

    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)


async def test_unchecking_rejected_returns_to_unapplied(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

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


async def test_delete_requires_confirmation(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

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
    await user.should_see("Saved 1")

    job_id = db.get_jobs()[0]["id"]
    user.find("🗑️ Delete").click()
    user.find(marker=f"delete-confirm-{job_id}").click()

    await _wait_for(lambda: db.get_jobs() == [])
    await user.should_see("Position deleted.")


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
    await user.should_see("Position added.")
    job = db.get_jobs()[0]
    assert job["job_url"] == "https://manual.example/1"
    assert job["title"] == "Manual Engineer"
    assert job["company"] == "ManualCo"
    assert job["description"] == "Manual body"


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
    await user.should_see("Position updated.")
    assert db.get_jobs()[0]["company"] == "UpdatedCo"


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


async def test_card_has_full_details_link(user: User) -> None:
    await user.open("/")
    await _scan(user)
    await user.should_see("Saved 1")

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


async def test_details_view_unknown_id_warns(user: User) -> None:
    await user.open("/job/999")

    await user.should_see("Position not found.")


async def test_detail_shows_history_timeline(user: User) -> None:
    job_id = _seed_job()
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "interview")
    await user.open(f"/job/{job_id}")

    await user.should_see("History")
    titles = {entry.props["title"] for entry in user.find(ui.timeline_entry).elements}
    assert titles == {"Unapplied", "Applied", "Interview"}


def test_days_since_applied_handles_missing_and_bad_dates() -> None:
    assert app._days_since_applied(cast(db.Job, {"date_applied": None})) is None
    assert app._days_since_applied(cast(db.Job, {"date_applied": "not-a-date"})) is None

    now = time.localtime()
    today = date(now.tm_year, now.tm_mon, now.tm_mday)
    three_days_ago = (today - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert app._days_since_applied(cast(db.Job, {"date_applied": three_days_ago})) == 3


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


async def test_theme_defaults_to_system(user: User) -> None:
    await user.open("/")

    dark = user.find(ui.dark_mode).elements.pop()
    assert dark.value is None


async def test_settings_drawer_is_wide(user: User) -> None:
    await user.open("/")

    drawer = user.find(ui.right_drawer).elements.pop()
    assert drawer.props["width"] == "480"


def test_global_css_styles_all_textareas() -> None:
    assert ".q-textarea .q-field__native" in app.GLOBAL_CSS
    assert "line-height: 1.7" in app.GLOBAL_CSS


def test_global_css_wraps_markdown_pre() -> None:
    assert ".nicegui-markdown pre" in app.GLOBAL_CSS
    assert "white-space: pre-wrap" in app.GLOBAL_CSS


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

    settings = config.Settings(host="127.0.0.1", port=9002, reload=True, show=True)
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

    settings = config.Settings(log_level="debug", log_retention_days=3)
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
