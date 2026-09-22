"""Streamlit UI tests for cursustrace using AppTest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

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
    assert app_test.warning[0].value == "This URL is already tracked."
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
