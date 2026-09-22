"""Tests for the cursustrace SQLite layer."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cursustrace import db

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
    assert job["date_applied"] is None
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
        _add(f"https://example.com/{i}")
    ids = [job["id"] for job in db.get_jobs()]
    assert ids == sorted(ids, reverse=True)


def test_get_jobs_applied_filter(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    _add("https://example.com/2")
    jobs = db.get_jobs()
    db.update_applied_status(jobs[0]["id"], True)

    assert [j["job_url"] for j in db.get_jobs(applied_filter=True)] == ["https://example.com/2"]
    assert [j["job_url"] for j in db.get_jobs(applied_filter=False)] == ["https://example.com/1"]
    assert len(db.get_jobs(applied_filter=None)) == 2


def test_update_applied_status_round_trip(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.update_applied_status(job_id, True)
    applied = db.get_jobs()[0]
    assert applied["applied"] == 1
    assert applied["date_applied"] is not None
    assert TIMESTAMP_RE.match(applied["date_applied"] or "")

    db.update_applied_status(job_id, False)
    unapplied = db.get_jobs()[0]
    assert unapplied["applied"] == 0
    assert unapplied["date_applied"] is None


def test_update_applied_status_unknown_id_is_noop(db_path: Path) -> None:
    db.init_db()
    db.update_applied_status(999, True)
    assert db.get_jobs() == []


def test_timestamp_is_close_to_now(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    date_added = db.get_jobs()[0]["date_added"]
    created = time.mktime(time.strptime(date_added, "%Y-%m-%d %H:%M:%S"))
    assert abs(time.time() - created) < 60
