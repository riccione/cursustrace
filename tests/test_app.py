"""Streamlit UI tests for cursustrace using AppTest."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Button

from cursustrace import app, db, scraper
from cursustrace.errors import ScrapeError

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "cursustrace" / "app.py"
JOB_URL = "https://example.com/jobs/1"


def _fake_scrape(url: str) -> scraper.ScrapedJob:
    return {
        "title": "Senior Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Body text",
    }


def _raise_scrape(url: str) -> scraper.ScrapedJob:
    raise ScrapeError(f"boom: {url}")


@pytest.fixture
def app_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(scraper, "scrape_job", _fake_scrape)
    return AppTest.from_file(APP_PATH)


def test_run_execs_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _fake_execvp(file: str, args: list[str]) -> None:
        calls.append((file, args))

    monkeypatch.setattr(os, "execvp", _fake_execvp)

    app.run()
    assert len(calls) == 1
    file, argv = calls[0]
    assert file == "streamlit"
    assert argv[0:2] == ["streamlit", "run"]
    assert argv[2] == str(APP_PATH)
    assert argv[-1] == "--browser.gatherUsageStats=false"


def test_run_sets_telemetry_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "execvp", lambda file, args: None)
    monkeypatch.delenv("STREAMLIT_BROWSER_GATHER_USAGE_STATS", raising=False)

    app.run()

    assert os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"


def test_run_fallback_when_streamlit_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing_execvp(file: str, args: list[str]) -> None:
        raise FileNotFoundError

    calls: list[list[str]] = []

    def _fake_run(args: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(os, "execvp", _missing_execvp)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    app.run()
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0:4] == [sys.executable, "-m", "streamlit", "run"]
    assert argv[4] == str(APP_PATH)
    assert argv[-1] == "--browser.gatherUsageStats=false"


def test_title_renders(app_test: AppTest) -> None:
    app_test.run()
    assert not app_test.exception
    assert app_test.title[0].value == app.APP_TITLE


def test_scan_saves_position(app_test: AppTest) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()

    assert not app_test.exception
    assert app_test.success[0].value == "Position saved."
    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Engineer"
    assert jobs[0]["company"] == "Acme"


def test_duplicate_url_shows_warning(app_test: AppTest) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()
    app_test.button[0].click().run()

    assert not app_test.exception
    assert app_test.warning[0].value == (
        "Position already exists in database (Matched by title/company fingerprint)."
    )
    assert len(db.get_jobs()) == 1


def test_duplicate_fingerprint_shows_warning(
    app_test: AppTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()

    def _variant_scrape(url: str) -> scraper.ScrapedJob:
        return {
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body text",
        }

    monkeypatch.setattr(scraper, "scrape_job", _variant_scrape)
    app_test.text_input[0].set_value("https://other.com/jobs/999").run()
    app_test.button[0].click().run()

    assert not app_test.exception
    assert app_test.warning[0].value.startswith("Position already exists")
    assert len(db.get_jobs()) == 1


def test_empty_url_shows_warning(app_test: AppTest) -> None:
    app_test.run()
    app_test.button[0].click().run()

    assert not app_test.exception
    assert app_test.warning[0].value == "Please enter a job URL."


def test_scrape_error_shows_error(
    app_test: AppTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scraper, "scrape_job", _raise_scrape)
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()

    assert not app_test.exception
    assert "boom" in app_test.error[0].value
    assert db.get_jobs() == []


def test_checkbox_marks_job_applied(app_test: AppTest) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()
    assert len(app_test.checkbox) == 1

    app_test.checkbox[0].check().run()

    assert not app_test.exception
    applied = db.get_jobs(applied_filter=True)
    assert len(applied) == 1
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", applied[0]["date_applied"] or "")
    assert db.get_jobs(applied_filter=False) == []


def _seed_job(title: str = "Senior Engineer", description: str = "Body text") -> int:
    db.init_db()
    db.add_job(JOB_URL, title, "Acme", "Remote", description)
    return db.get_jobs()[0]["id"]


def test_card_has_full_details_link(app_test: AppTest) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()

    markdown = [element.value for element in app_test.markdown]
    assert any("View full details" in value and "?job=" in value for value in markdown)


def test_details_view_shows_full_description(app_test: AppTest) -> None:
    job_id = _seed_job(description="## Responsibilities\n- Build things")
    app_test.query_params[app.DETAIL_PARAM] = str(job_id)
    app_test.run()

    assert not app_test.exception
    assert app_test.subheader[0].value == "Senior Engineer"
    markdown = [element.value for element in app_test.markdown]
    assert any("Responsibilities" in value for value in markdown)
    assert any("**Company:** Acme" in value for value in markdown)
    assert any("**Location:** Remote" in value for value in markdown)


def test_details_view_shows_tracking_fields(app_test: AppTest) -> None:
    job_id = _seed_job()
    db.update_applied_status(job_id, True)
    app_test.query_params[app.DETAIL_PARAM] = str(job_id)
    app_test.run()

    assert not app_test.exception
    markdown = [element.value for element in app_test.markdown]
    assert any(value.startswith("**Applied:** Yes") for value in markdown)
    assert any("**Added:**" in value for value in markdown)


def test_details_view_unknown_id_warns(app_test: AppTest) -> None:
    app_test.query_params[app.DETAIL_PARAM] = "999"
    app_test.run()

    assert not app_test.exception
    assert app_test.warning[0].value == "Position not found."


def test_details_back_button_returns_to_list(app_test: AppTest) -> None:
    job_id = _seed_job()
    app_test.query_params[app.DETAIL_PARAM] = str(job_id)
    app_test.run()

    next(button for button in app_test.button if button.label == "← Back to list").click().run()

    assert not app_test.exception
    assert any(field.label == "Job URL" for field in app_test.text_input)
    assert app.DETAIL_PARAM not in app_test.query_params


CLEAR_BUTTON_LABEL = "Confirm & Clear All Data"


def _clear_button(app_test: AppTest) -> Button:
    return next(
        button for button in app_test.sidebar.button if button.label == CLEAR_BUTTON_LABEL
    )


def test_clear_button_disabled_initially(app_test: AppTest) -> None:
    app_test.run()
    assert _clear_button(app_test).disabled is True


def test_clear_button_stays_disabled_for_wrong_case(app_test: AppTest) -> None:
    app_test.run()
    confirm = next(
        field for field in app_test.sidebar.text_input if field.label.startswith("Type 'DELETE'")
    )
    confirm.set_value("delete").run()

    assert _clear_button(app_test).disabled is True


def test_clear_button_enabled_with_exact_delete(app_test: AppTest) -> None:
    app_test.run()
    confirm = next(
        field for field in app_test.sidebar.text_input if field.label.startswith("Type 'DELETE'")
    )
    confirm.set_value("DELETE").run()

    assert _clear_button(app_test).disabled is False


def test_clear_database_removes_all_jobs(app_test: AppTest) -> None:
    app_test.run()
    app_test.text_input[0].set_value(JOB_URL).run()
    app_test.button[0].click().run()
    assert len(db.get_jobs()) == 1

    confirm = next(
        field for field in app_test.sidebar.text_input if field.label.startswith("Type 'DELETE'")
    )
    confirm.set_value("DELETE").run()
    _clear_button(app_test).click().run()

    assert not app_test.exception
    assert db.get_jobs() == []
    assert any(
        message.value == "Successfully cleared 1 positions." for message in app_test.success
    )
