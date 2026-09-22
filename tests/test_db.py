"""Tests for the cursustrace SQLite layer."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cursustrace import db
from cursustrace.utils import generate_fingerprint

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "cursustrace.db"
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
    yield path


def _add(url: str, title: str = "Engineer") -> bool:
    return db.add_job(url, title, "Acme", "Remote", "desc")


def test_get_connection_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "cursustrace.db"
    with db.get_connection(nested) as conn:
        assert conn.row_factory is sqlite3.Row
    assert nested.exists()


def test_init_db_is_idempotent(db_path: Path) -> None:
    db.init_db()
    db.init_db()
    with db.get_connection() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchall()
    assert len(tables) == 1


def test_add_job_returns_true_and_stamps_date(db_path: Path) -> None:
    db.init_db()
    assert _add("https://example.com/1") is True

    jobs = db.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["job_url"] == "https://example.com/1"
    assert job["title"] == "Engineer"
    assert job["applied"] == 0
    assert job["interview"] == 0
    assert job["rejected"] == 0
    assert job["date_applied"] is None
    assert job["date_interview"] is None
    assert job["date_rejected"] is None
    assert TIMESTAMP_RE.match(job["date_added"])


def test_add_job_duplicate_url_returns_false(db_path: Path) -> None:
    db.init_db()
    assert _add("https://example.com/1") is True
    assert _add("https://example.com/1") is False
    assert len(db.get_jobs()) == 1


def test_add_job_accepts_null_fields(db_path: Path) -> None:
    db.init_db()
    assert db.add_job("https://example.com/1", None, None, None, None) is True
    assert db.get_jobs()[0]["company"] is None


def test_get_jobs_orders_by_id_desc(db_path: Path) -> None:
    db.init_db()
    for i in range(3):
        _add(f"https://example.com/{i}", f"Engineer {i}")
    ids = [job["id"] for job in db.get_jobs()]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == 3


def test_get_jobs_status_filter(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1", "Engineer One")
    _add("https://example.com/2", "Engineer Two")
    _add("https://example.com/3", "Engineer Three")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "interview")
    db.set_job_status(jobs[2]["id"], "rejected")

    assert [j["job_url"] for j in db.get_jobs(status="applied")] == ["https://example.com/3"]
    assert [j["job_url"] for j in db.get_jobs(status="interview")] == ["https://example.com/2"]
    assert [j["job_url"] for j in db.get_jobs(status="rejected")] == ["https://example.com/1"]
    assert db.get_jobs(status="unapplied") == []
    assert len(db.get_jobs(status=None)) == 3


def test_set_job_status_is_mutually_exclusive(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "interview")
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 1, 0)
    assert job["date_interview"] is not None

    db.set_job_status(job_id, "rejected")
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"]) == (0, 0, 1)
    assert job["date_rejected"] is not None


def test_set_job_status_round_trip(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "applied")
    applied = db.get_jobs()[0]
    assert applied["applied"] == 1
    assert TIMESTAMP_RE.match(applied["date_applied"] or "")

    db.set_job_status(job_id, "unapplied")
    unapplied = db.get_jobs()[0]
    assert (unapplied["applied"], unapplied["interview"], unapplied["rejected"]) == (0, 0, 0)
    assert unapplied["date_applied"] is None


def test_set_job_status_preserves_earlier_dates(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "applied")
    date_applied = db.get_jobs()[0]["date_applied"]

    db.set_job_status(job_id, "interview")
    interview = db.get_jobs()[0]
    assert interview["date_applied"] == date_applied
    assert interview["date_interview"] is not None

    db.set_job_status(job_id, "rejected")
    rejected = db.get_jobs()[0]
    assert rejected["date_applied"] == date_applied
    assert rejected["date_interview"] == interview["date_interview"]
    assert rejected["date_rejected"] is not None


def test_set_job_status_unknown_id_is_noop(db_path: Path) -> None:
    db.init_db()
    db.set_job_status(999, "applied")
    assert db.get_jobs() == []


def test_timestamp_is_close_to_now(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    date_added = db.get_jobs()[0]["date_added"]
    created = time.mktime(time.strptime(date_added, "%Y-%m-%d %H:%M:%S"))
    assert abs(time.time() - created) < 60


def _fingerprint_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]


def test_init_db_adds_fingerprint_column_and_index(db_path: Path) -> None:
    db.init_db()
    with db.get_connection() as conn:
        assert "fingerprint" in _fingerprint_columns(conn)
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_jobs_fingerprint'"
        ).fetchall()
    assert len(indexes) == 1


def test_init_db_migrates_legacy_schema_and_backfills(db_path: Path) -> None:
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_url TEXT UNIQUE NOT NULL, title TEXT, "
        "company TEXT, location TEXT, description TEXT, applied INTEGER DEFAULT 0, "
        "date_added TEXT NOT NULL, date_applied TEXT)"
    )
    legacy.execute(
        "INSERT INTO jobs (job_url, title, company, location, description, date_added) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("https://example.com/old", "Engineer", "Acme", "Remote", "old body", "2024-01-01 00:00:00"),
    )
    legacy.commit()
    legacy.close()

    db.init_db()

    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["fingerprint"] == generate_fingerprint("Acme", "Engineer", "Remote")
    assert jobs[0]["interview"] == 0
    assert jobs[0]["rejected"] == 0
    with db.get_connection() as conn:
        columns = set(_fingerprint_columns(conn))
    assert {"interview", "rejected", "date_interview", "date_rejected"} <= columns


def test_add_job_stores_fingerprint(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.get_jobs()[0]["fingerprint"] == generate_fingerprint("Acme", "Engineer", "Remote")


def test_add_job_rejects_same_fingerprint_different_url(db_path: Path) -> None:
    db.init_db()
    assert db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "desc") is True
    assert db.add_job("https://b.com/2", "Engineer", "Acme", "Remote", "desc") is False
    assert len(db.get_jobs()) == 1


def test_check_duplicate_matches_url_ignoring_trackers(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    found, reason = db.check_duplicate(
        "https://example.com/1?utm_source=x&ref=y", "Other", "OtherCo", "Berlin", "body"
    )
    assert found is True
    assert reason == "Duplicate position already applied/tracked"


def test_check_duplicate_matches_fingerprint_from_other_url(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    found, reason = db.check_duplicate(
        "https://example.com/1-variant", "Engineer", "Acme", "Remote", "body"
    )
    assert found is True
    assert reason == "Duplicate position already applied/tracked"


def test_check_duplicate_matches_similar_description(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Backend Developer",
        "Acme",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    found, _ = db.check_duplicate(
        "https://example.com/new",
        "Platform Engineer",
        "Acme",
        "Berlin",
        "Build distributed systems and maintain the APIs for our platform!",
    )
    assert found is True


def test_check_duplicate_ignores_different_company(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Backend Developer",
        "Acme",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    found, reason = db.check_duplicate(
        "https://example.com/new",
        "Backend Developer",
        "Globex",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    assert found is False
    assert reason == ""


def test_check_duplicate_returns_false_for_new_job(db_path: Path) -> None:
    db.init_db()
    found, reason = db.check_duplicate(
        "https://example.com/1", "Engineer", "Acme", "Remote", "unique body"
    )
    assert found is False
    assert reason == ""


def test_clear_all_jobs_on_empty_db_returns_zero(db_path: Path) -> None:
    db.init_db()
    assert db.clear_all_jobs() == 0


def test_clear_all_jobs_returns_count_and_empties_table(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1", "Engineer One")
    _add("https://example.com/2", "Engineer Two")

    assert db.clear_all_jobs() == 2
    assert db.get_jobs() == []


def test_clear_all_jobs_resets_auto_increment(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1", "Engineer One")
    db.clear_all_jobs()
    _add("https://example.com/2", "Engineer Two")

    assert db.get_jobs()[0]["id"] == 1


def test_delete_job_removes_row(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1", "Engineer One")
    _add("https://example.com/2", "Engineer Two")
    job_id = db.get_jobs()[-1]["id"]

    db.delete_job(job_id)

    remaining = db.get_jobs()
    assert len(remaining) == 1
    assert remaining[0]["job_url"] == "https://example.com/2"


def test_delete_job_unknown_id_is_noop(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")

    db.delete_job(999)

    assert len(db.get_jobs()) == 1


def _profile_payload(full_name: str = "Jane Doe", cv_markdown: str = "# Jane Doe\n\nEngineer") -> db.Profile:
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


def test_init_db_creates_profile_table(db_path: Path) -> None:
    db.init_db()
    with db.get_connection() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'profile'"
        ).fetchall()
    assert len(tables) == 1


def test_save_and_get_profile_round_trip(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload())

    profile = db.get_profile()
    assert profile["full_name"] == "Jane Doe"
    assert profile["email"] == "jane@example.com"
    assert profile["linkedin_url"] == "https://linkedin.com/in/jane"
    assert profile["cv_markdown"] == "# Jane Doe\n\nEngineer"
    assert profile["date_updated"] is not None


def test_save_profile_upserts_single_row(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload())
    db.save_profile(_profile_payload(full_name="Jane Smith"))
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0]
    assert count == 1
    assert db.get_profile()["full_name"] == "Jane Smith"


def test_save_profile_keeps_cv_in_database(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload(cv_markdown="# Updated CV"))

    with db.get_connection() as conn:
        stored = conn.execute("SELECT cv_markdown FROM profile WHERE id = 1").fetchone()[0]
    assert stored == "# Updated CV"
    assert not (db_path.parent / "cv.md").exists()


def test_init_db_migrates_profile_cv_markdown(db_path: Path) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE profile ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "full_name TEXT, location TEXT, phone TEXT, email TEXT, "
            "linkedin_url TEXT, github_url TEXT, "
            "date_updated TEXT DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO profile (id, full_name) VALUES (1, 'Legacy')")
        conn.commit()

    db.init_db()

    with db.get_connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
    assert "cv_markdown" in columns
    assert db.get_profile()["full_name"] == "Legacy"


def test_get_profile_without_row(db_path: Path) -> None:
    db.init_db()
    profile = db.get_profile()
    assert profile["cv_markdown"] == ""
    assert profile["full_name"] == ""


def test_setting_round_trip(db_path: Path) -> None:
    db.init_db()
    assert db.get_setting("dark_mode") is None
    assert db.get_setting("dark_mode", "system") == "system"

    db.set_setting("dark_mode", "dark")
    assert db.get_setting("dark_mode") == "dark"

    db.set_setting("dark_mode", "light")
    assert db.get_setting("dark_mode") == "light"
