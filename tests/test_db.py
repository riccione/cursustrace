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
    return db.add_job(url, title, "Acme", "Remote", "desc") is not None


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


def test_init_db_enables_wal_and_busy_timeout(db_path: Path) -> None:
    db.init_db()
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


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


def test_add_job_returns_new_id(db_path: Path) -> None:
    db.init_db()
    job_id = db.add_job("https://example.com/1", "Engineer", "Acme", "Remote", "desc")

    assert job_id is not None
    assert db.get_jobs()[0]["id"] == job_id


def test_add_job_duplicate_returns_none(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://example.com/1", "Engineer", "Acme", "Remote", "desc")

    assert db.add_job("https://example.com/1", "Engineer", "Acme", "Remote", "desc") is None


def test_get_job_round_trip(db_path: Path) -> None:
    db.init_db()
    job_id = db.add_job("https://example.com/1", "Engineer", "Acme", "Remote", "desc")
    assert job_id is not None

    job = db.get_job(job_id)

    assert job is not None
    assert job["job_url"] == "https://example.com/1"


def test_get_job_unknown_returns_none(db_path: Path) -> None:
    db.init_db()
    assert db.get_job(999) is None


def test_add_job_accepts_null_fields(db_path: Path) -> None:
    db.init_db()
    assert db.add_job("https://example.com/1", None, None, None, None) is not None
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
    _add("https://example.com/4", "Engineer Four")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "interview")
    db.set_job_status(jobs[2]["id"], "rejected")
    db.set_job_status(jobs[3]["id"], "outdated")

    assert [j["job_url"] for j in db.get_jobs(status="applied")] == ["https://example.com/4"]
    assert [j["job_url"] for j in db.get_jobs(status="interview")] == ["https://example.com/3"]
    assert [j["job_url"] for j in db.get_jobs(status="rejected")] == ["https://example.com/2"]
    assert [j["job_url"] for j in db.get_jobs(status="outdated")] == ["https://example.com/1"]
    assert db.get_jobs(status="unapplied") == []
    assert len(db.get_jobs(status=None)) == 4


def test_search_jobs_matches_company_fuzzy(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA Engineer", "Adapty", "Remote", "desc")
    db.add_job("https://a.com/2", "QA Engineer", "Paysend", "Remote", "desc")

    matches = db.search_jobs("adapt")

    assert [job["company"] for job in matches] == ["Adapty"]


def test_search_jobs_matches_company_typo(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Friendly HR (Agency)", "Remote", "desc")

    assert [job["company"] for job in db.search_jobs("frendly hr")] == ["Friendly HR (Agency)"]


def test_search_jobs_no_match(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Adapty", "Remote", "desc")

    assert db.search_jobs("zzzz") == []


def test_search_jobs_empty_query_returns_all(db_path: Path) -> None:
    db.init_db()
    _add("https://a.com/1", "Engineer One")
    _add("https://a.com/2", "Engineer Two")

    assert len(db.search_jobs("")) == 2
    assert len(db.search_jobs("   ")) == 2


def test_search_jobs_filters_by_status(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Adapty", "Remote", "desc")
    db.add_job("https://a.com/2", "QA", "Adapty", "Berlin", "desc")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    assert len(db.search_jobs("adapt")) == 2
    assert len(db.search_jobs("adapt", status="applied")) == 1
    assert db.search_jobs("adapt", status="rejected") == []


def test_search_jobs_orders_by_score(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Ada", "Remote", "desc")
    db.add_job("https://a.com/2", "QA", "Adapty", "Remote", "desc")

    matches = db.search_jobs("adapty")

    assert [job["company"] for job in matches] == ["Adapty", "Ada"]


def _job(
    job_id: int,
    *,
    company: str | None = "Acme",
    date_added: str = "2026-01-01 00:00:00",
    applied: bool = False,
    interview: bool = False,
    rejected: bool = False,
    outdated: bool = False,
) -> db.Job:
    return {
        "id": job_id,
        "job_url": f"https://example.com/{job_id}",
        "title": "Engineer",
        "company": company,
        "location": "Remote",
        "description": "desc",
        "applied": int(applied),
        "interview": int(interview),
        "rejected": int(rejected),
        "outdated": int(outdated),
        "date_added": date_added,
        "date_applied": None,
        "date_interview": None,
        "date_rejected": None,
        "date_outdated": None,
        "applied_comment": None,
        "interview_comment": None,
        "rejected_comment": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "salary_note": None,
        "deadline": None,
        "fingerprint": None,
    }


def test_sort_jobs_newest_and_oldest() -> None:
    jobs = [
        _job(1, date_added="2026-01-03 10:00:00"),
        _job(2, date_added="2026-01-01 10:00:00"),
        _job(3, date_added="2026-01-02 10:00:00"),
    ]

    assert [job["id"] for job in db.sort_jobs(jobs)] == [1, 3, 2]
    assert [job["id"] for job in db.sort_jobs(jobs, "newest")] == [1, 3, 2]
    assert [job["id"] for job in db.sort_jobs(jobs, "oldest")] == [2, 3, 1]


def test_sort_jobs_date_ties_break_by_id() -> None:
    same = "2026-01-01 10:00:00"
    jobs = [_job(1, date_added=same), _job(2, date_added=same), _job(3, date_added=same)]

    assert [job["id"] for job in db.sort_jobs(jobs, "newest")] == [3, 2, 1]
    assert [job["id"] for job in db.sort_jobs(jobs, "oldest")] == [1, 2, 3]


def test_sort_jobs_company_is_case_insensitive() -> None:
    jobs = [_job(1, company="beta"), _job(2, company="Alpha"), _job(3, company="acme")]

    assert [job["company"] for job in db.sort_jobs(jobs, "company")] == [
        "acme",
        "Alpha",
        "beta",
    ]
    assert [job["company"] for job in db.sort_jobs(jobs, "company_desc")] == [
        "beta",
        "Alpha",
        "acme",
    ]


def test_sort_jobs_company_ties_keep_id_descending() -> None:
    jobs = [_job(1), _job(2), _job(3)]

    assert [job["id"] for job in db.sort_jobs(jobs, "company")] == [3, 2, 1]
    assert [job["id"] for job in db.sort_jobs(jobs, "company_desc")] == [3, 2, 1]


def test_sort_jobs_company_handles_missing_company() -> None:
    jobs = [_job(1, company="Acme"), _job(2, company=None)]

    assert [job["id"] for job in db.sort_jobs(jobs, "company")] == [2, 1]


def test_sort_jobs_status_uses_pipeline_order() -> None:
    jobs = [
        _job(1, rejected=True),
        _job(2, applied=True),
        _job(3),
        _job(4, interview=True),
        _job(5, applied=True),
        _job(6, outdated=True),
    ]

    statuses = [db.job_status(job) for job in db.sort_jobs(jobs, "status")]
    assert statuses == ["unapplied", "applied", "applied", "interview", "rejected", "outdated"]
    applied_ids = [
        job["id"] for job in db.sort_jobs(jobs, "status") if db.job_status(job) == "applied"
    ]
    assert applied_ids == [5, 2]


def test_sort_jobs_returns_new_list() -> None:
    jobs = [_job(1, date_added="2026-01-02 10:00:00"), _job(2, date_added="2026-01-01 10:00:00")]
    original = list(jobs)

    result = db.sort_jobs(jobs, "oldest")

    assert jobs == original
    assert result is not jobs


def test_set_job_status_is_mutually_exclusive(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "interview")
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"], job["outdated"]) == (0, 1, 0, 0)
    assert job["date_interview"] is not None

    db.set_job_status(job_id, "rejected")
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"], job["outdated"]) == (0, 0, 1, 0)
    assert job["date_rejected"] is not None

    db.set_job_status(job_id, "outdated")
    job = db.get_jobs()[0]
    assert (job["applied"], job["interview"], job["rejected"], job["outdated"]) == (0, 0, 0, 1)
    assert job["date_outdated"] is not None


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


def test_job_status_derives_stage(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    assert db.job_status(db.get_jobs()[0]) == "unapplied"

    db.set_job_status(job_id, "applied")
    assert db.job_status(db.get_jobs()[0]) == "applied"

    db.set_job_status(job_id, "interview")
    assert db.job_status(db.get_jobs()[0]) == "interview"

    db.set_job_status(job_id, "rejected")
    assert db.job_status(db.get_jobs()[0]) == "rejected"

    db.set_job_status(job_id, "outdated")
    assert db.job_status(db.get_jobs()[0]) == "outdated"


def test_set_job_status_outdated_never_fabricates_application_date(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "outdated")

    job = db.get_jobs()[0]
    assert job["outdated"] == 1
    assert job["date_applied"] is None
    assert TIMESTAMP_RE.match(job["date_outdated"] or "")


def test_set_job_status_outdated_preserves_application_date(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "applied")
    date_applied = db.get_jobs()[0]["date_applied"]

    db.set_job_status(job_id, "outdated")
    parked = db.get_jobs()[0]
    assert parked["date_applied"] == date_applied
    assert TIMESTAMP_RE.match(parked["date_outdated"] or "")

    db.set_job_status(job_id, "applied")
    restored = db.get_jobs()[0]
    assert restored["outdated"] == 0
    assert restored["date_applied"] == date_applied
    assert restored["date_outdated"] is None

    db.set_job_status(job_id, "unapplied")
    reset = db.get_jobs()[0]
    assert reset["date_outdated"] is None


def test_add_job_logs_creation_event(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    events = db.get_events(job_id)

    assert [(event["job_id"], event["status"]) for event in events] == [(job_id, "unapplied")]
    assert TIMESTAMP_RE.match(events[0]["created_at"])


def test_set_job_status_logs_transitions(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "interview")
    db.set_job_status(job_id, "rejected")
    db.set_job_status(job_id, "unapplied")

    assert [event["status"] for event in db.get_events(job_id)] == [
        "unapplied",
        "applied",
        "interview",
        "rejected",
        "unapplied",
    ]


def test_set_job_status_same_stage_logs_nothing(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_status(job_id, "unapplied")
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "applied")

    assert [event["status"] for event in db.get_events(job_id)] == ["unapplied", "applied"]


def test_set_job_status_unknown_id_logs_nothing(db_path: Path) -> None:
    db.init_db()
    db.set_job_status(999, "applied")
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_job_ids_with_status_event(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    _add("https://example.com/2")
    jobs = db.get_jobs()

    assert db.job_ids_with_status_event("outdated") == set()

    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[0]["id"], "outdated")
    db.set_job_status(jobs[1]["id"], "applied")

    assert db.job_ids_with_status_event("outdated") == {jobs[0]["id"]}
    assert db.job_ids_with_status_event("applied") == {jobs[0]["id"], jobs[1]["id"]}


def test_delete_job_cascades_events(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]
    db.set_job_status(job_id, "applied")

    db.delete_job(job_id)

    assert db.get_events(job_id) == []
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_clear_all_jobs_cascades_events(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    db.clear_all_jobs()

    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_clear_all_data_cascades_events(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    db.clear_all_data()

    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def _drop_event_history() -> None:
    with db.get_connection() as conn:
        conn.execute("DROP TABLE events")
        conn.commit()


def test_init_db_backfills_events_from_dates(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]
    db.set_job_status(job_id, "applied")
    db.set_job_status(job_id, "interview")
    _drop_event_history()

    db.init_db()

    assert [event["status"] for event in db.get_events(job_id)] == ["applied", "interview"]


def test_init_db_backfill_runs_once(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")
    _drop_event_history()

    db.init_db()
    db.init_db()

    assert len(db.get_events(db.get_jobs()[0]["id"])) == 1


def test_job_counts(db_path: Path) -> None:
    db.init_db()
    assert db.job_counts() == {
        "total": 0,
        "unapplied": 0,
        "applied": 0,
        "interview": 0,
        "rejected": 0,
        "outdated": 0,
    }

    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "desc")
    db.add_job("https://a.com/4", "Four", "Acme", "Remote", "desc")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    db.set_job_status(jobs[2]["id"], "outdated")

    assert db.job_counts() == {
        "total": 4,
        "unapplied": 1,
        "applied": 1,
        "interview": 0,
        "rejected": 1,
        "outdated": 1,
    }


def test_applications_by_month(db_path: Path) -> None:
    db.init_db()
    assert db.applications_by_month() == []

    _add("https://a.com/1")
    _add("https://a.com/2")
    _add("https://a.com/3")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET date_applied = '2020-01-15 09:00:00' WHERE id = ?",
            (jobs[0]["id"],),
        )
        conn.commit()

    assert db.applications_by_month() == [
        ("2020-01", 1),
        (time.strftime("%Y-%m"), 1),
    ]


def test_pipeline_funnel(db_path: Path) -> None:
    db.init_db()
    assert db.pipeline_funnel() == {
        "added": 0,
        "applied": 0,
        "response": 0,
        "interview": 0,
    }

    _add("https://a.com/1")
    _add("https://a.com/2")
    _add("https://a.com/3")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "rejected")
    db.set_job_status(jobs[2]["id"], "interview")

    assert db.pipeline_funnel() == {
        "added": 3,
        "applied": 3,
        "response": 2,
        "interview": 1,
    }


def test_pipeline_funnel_keeps_history_after_unmark(db_path: Path) -> None:
    db.init_db()
    _add("https://a.com/1")
    job_id = db.get_jobs()[0]["id"]
    db.set_job_status(job_id, "rejected")

    db.set_job_status(job_id, "unapplied")

    assert db.pipeline_funnel() == {
        "added": 1,
        "applied": 1,
        "response": 1,
        "interview": 0,
    }


def test_pipeline_funnel_does_not_count_parking_as_applied(db_path: Path) -> None:
    db.init_db()
    _add("https://a.com/1")
    _add("https://a.com/2")
    jobs = db.get_jobs()

    db.set_job_status(jobs[0]["id"], "outdated")
    db.set_job_status(jobs[1]["id"], "applied")
    db.set_job_status(jobs[1]["id"], "outdated")

    assert db.pipeline_funnel() == {
        "added": 2,
        "applied": 1,
        "response": 0,
        "interview": 0,
    }


def test_set_job_comment_round_trip(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.set_job_comment(job_id, "applied", "referred by a friend")
    db.set_job_comment(job_id, "interview", "technical round scheduled")

    job = db.get_jobs()[0]
    assert job["applied_comment"] == "referred by a friend"
    assert job["interview_comment"] == "technical round scheduled"
    assert job["rejected_comment"] is None


def test_update_job_comments_replaces_all(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    db.update_job_comments(job_id, "a", "i", "r")

    job = db.get_jobs()[0]
    assert (job["applied_comment"], job["interview_comment"], job["rejected_comment"]) == (
        "a",
        "i",
        "r",
    )


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
        (
            "https://example.com/old",
            "Engineer",
            "Acme",
            "Remote",
            "old body",
            "2024-01-01 00:00:00",
        ),
    )
    legacy.commit()
    legacy.close()

    db.init_db()

    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["fingerprint"] == generate_fingerprint(
        "https://example.com/old", "Acme", "Engineer"
    )
    assert jobs[0]["interview"] == 0
    assert jobs[0]["rejected"] == 0
    assert jobs[0]["outdated"] == 0
    with db.get_connection() as conn:
        columns = set(_fingerprint_columns(conn))
    assert {
        "interview",
        "rejected",
        "outdated",
        "date_interview",
        "date_rejected",
        "date_outdated",
        "applied_comment",
        "interview_comment",
        "rejected_comment",
        "deadline",
    } <= columns


def test_add_job_stores_fingerprint(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.get_jobs()[0]["fingerprint"] == generate_fingerprint(
        "https://example.com/1", "Acme", "Engineer"
    )


def test_add_job_stores_salary(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Engineer",
        "Acme",
        "Remote",
        "body",
        salary_min=60000,
        salary_max=80000,
        salary_currency="EUR",
        salary_period="year",
        salary_note="plus bonus",
    )

    job = db.get_jobs()[0]
    assert job["salary_min"] == 60000
    assert job["salary_max"] == 80000
    assert job["salary_currency"] == "EUR"
    assert job["salary_period"] == "year"
    assert job["salary_note"] == "plus bonus"


def test_add_job_stores_deadline(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Engineer",
        "Acme",
        "Remote",
        "body",
        deadline="2026-12-31",
    )

    assert db.get_jobs()[0]["deadline"] == "2026-12-31"


def test_add_job_deadline_defaults_to_null(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")

    assert db.get_jobs()[0]["deadline"] is None


def test_init_db_adds_salary_columns(db_path: Path) -> None:
    db.init_db()
    with db.get_connection() as conn:
        columns = set(_fingerprint_columns(conn))
    assert {
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_note",
    } <= columns


def test_add_job_allows_same_role_different_url(db_path: Path) -> None:
    db.init_db()
    assert db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "desc") is not None
    assert db.add_job("https://b.com/2", "Engineer", "Acme", "Remote", "desc") is not None
    assert len(db.get_jobs()) == 2


def test_check_duplicate_matches_url_ignoring_trackers(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.check_duplicate("https://example.com/1?utm_source=x&ref=y") is True


def test_check_duplicate_ignores_same_role_on_different_url(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.check_duplicate("https://example.com/1-variant") is False


def test_find_similar_matches_same_company_and_title(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    _add("https://example.com/2")
    matches = db.find_similar("Acme", "Engineer", "desc", exclude_id=2)
    assert [job["job_url"] for job in matches] == ["https://example.com/1"]


def test_find_similar_matches_similar_description(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Backend Developer",
        "Acme",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    matches = db.find_similar(
        "Acme",
        "Platform Engineer",
        "Build distributed systems and maintain the APIs for our platform!",
    )
    assert [job["job_url"] for job in matches] == ["https://example.com/1"]


def test_find_similar_ignores_different_company(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Backend Developer",
        "Acme",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    assert db.find_similar("Globex", "Backend Developer", "totally unrelated body") == []


def test_find_similar_excludes_given_id(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.find_similar("Acme", "Engineer", "desc", exclude_id=1) == []


def test_find_similar_honors_limit(db_path: Path) -> None:
    db.init_db()
    for index in range(1, 4):
        db.add_job(f"https://example.com/{index}", "Engineer", "Acme", "Remote", "desc")
    assert len(db.find_similar("Acme", "Engineer", "desc", limit=2)) == 2


def test_find_similar_no_match(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    assert db.find_similar("Other", "Manager", "unrelated") == []


def test_check_duplicate_returns_false_for_new_job(db_path: Path) -> None:
    db.init_db()
    assert db.check_duplicate("https://example.com/1") is False


def test_init_db_recomputes_fingerprints_once(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    with db.get_connection() as conn:
        conn.execute("UPDATE jobs SET fingerprint = 'stale'")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

    db.init_db()

    assert db.get_jobs()[0]["fingerprint"] == generate_fingerprint(
        "https://example.com/1", "Acme", "Engineer"
    )
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.FINGERPRINT_SCHEME_VERSION


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


def test_update_job_updates_fields_and_fingerprint(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    assert (
        db.update_job(job_id, "https://example.com/2", "New Title", "NewCo", "Berlin", "new body")
        is True
    )

    job = db.get_jobs()[0]
    assert job["job_url"] == "https://example.com/2"
    assert job["title"] == "New Title"
    assert job["company"] == "NewCo"
    assert job["location"] == "Berlin"
    assert job["description"] == "new body"
    assert job["fingerprint"] == generate_fingerprint("https://example.com/2", "NewCo", "New Title")


def test_update_job_updates_salary(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    assert (
        db.update_job(
            job_id,
            "https://example.com/1",
            "Engineer",
            "Acme",
            "Remote",
            "body",
            salary_min=3000,
            salary_currency="USD",
            salary_period="month",
        )
        is True
    )

    job = db.get_jobs()[0]
    assert job["salary_min"] == 3000
    assert job["salary_max"] is None
    assert job["salary_currency"] == "USD"
    assert job["salary_period"] == "month"


def test_update_job_sets_and_clears_deadline(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    def update(deadline: str | None = None) -> bool:
        return db.update_job(
            job_id,
            "https://example.com/1",
            "Engineer",
            "Acme",
            "Remote",
            "body",
            deadline=deadline,
        )

    assert update("2026-12-31") is True
    assert db.get_jobs()[0]["deadline"] == "2026-12-31"

    assert update() is True
    assert db.get_jobs()[0]["deadline"] is None


def test_update_job_rejects_duplicate_url(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1", "Engineer One")
    _add("https://example.com/2", "Engineer Two")
    target = db.get_jobs()[0]

    assert (
        db.update_job(
            target["id"], "https://example.com/1", "Engineer Two", "Acme", "Remote", "desc"
        )
        is False
    )
    assert db.get_jobs()[0]["job_url"] == "https://example.com/2"


def test_update_job_rejects_url_used_by_another_row(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "body one")
    db.add_job("https://b.com/2", "Other", "OtherCo", "Berlin", "body two")
    target = db.get_jobs()[1]

    assert (
        db.update_job(target["id"], "https://b.com/2", "Engineer", "Acme", "Remote", "body two")
        is False
    )
    assert db.get_jobs()[0]["title"] == "Other"


def test_update_job_allows_same_role_different_url(db_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "body one")
    db.add_job("https://b.com/2", "Engineer", "Acme", "Remote", "body two")
    target = db.get_jobs()[1]

    assert (
        db.update_job(target["id"], "https://c.com/3", "Engineer", "Acme", "Remote", "body two")
        is True
    )


def test_check_duplicate_excludes_given_id(db_path: Path) -> None:
    db.init_db()
    _add("https://example.com/1")
    job_id = db.get_jobs()[0]["id"]

    assert db.check_duplicate("https://example.com/1", exclude_id=job_id) is False
    assert db.check_duplicate("https://example.com/1") is True


def test_find_similar_exclude_id_ignores_own_description(db_path: Path) -> None:
    db.init_db()
    db.add_job(
        "https://example.com/1",
        "Engineer",
        "Acme",
        "Remote",
        "Build distributed systems and maintain APIs for our platform.",
    )
    job_id = db.get_jobs()[0]["id"]

    assert (
        db.find_similar(
            "Acme",
            "Engineer",
            "Build distributed systems and maintain APIs for our platform.",
            exclude_id=job_id,
        )
        == []
    )


def _profile_payload(
    full_name: str = "Jane Doe", summary: str = "# Jane Doe\n\nEngineer"
) -> db.Profile:
    return {
        "id": 1,
        "name": "Default",
        "full_name": full_name,
        "location": "Remote",
        "phone": "555-0100",
        "email": "jane@example.com",
        "linkedin_url": "https://linkedin.com/in/jane",
        "github_url": "https://github.com/jane",
        "summary": summary,
        "work_history": "## Work History\n\n- Acme Corp",
        "education": "## Education\n\nBSc",
        "skills": "## Skills\n\n- Python",
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
    assert profile["id"] == 1
    assert profile["name"] == "Default"
    assert profile["full_name"] == "Jane Doe"
    assert profile["email"] == "jane@example.com"
    assert profile["linkedin_url"] == "https://linkedin.com/in/jane"
    assert profile["summary"] == "# Jane Doe\n\nEngineer"
    assert profile["work_history"] == "## Work History\n\n- Acme Corp"
    assert profile["education"] == "## Education\n\nBSc"
    assert profile["skills"] == "## Skills\n\n- Python"
    assert profile["date_updated"] is not None


def test_save_profile_upserts_single_row(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload())
    db.save_profile(_profile_payload(full_name="Jane Smith"))
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0]
    assert count == 1
    assert db.get_profile()["full_name"] == "Jane Smith"


def test_save_profile_keeps_sections_in_database(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload(summary="# Updated CV"))

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT summary, work_history, education, skills FROM profile WHERE id = 1"
        ).fetchone()
    assert row["summary"] == "# Updated CV"
    assert row["work_history"] == "## Work History\n\n- Acme Corp"
    assert row["education"] == "## Education\n\nBSc"
    assert row["skills"] == "## Skills\n\n- Python"
    assert not (db_path.parent / "cv.md").exists()


def test_init_db_migrates_profile_cv_sections(db_path: Path) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE profile ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "full_name TEXT, location TEXT, phone TEXT, email TEXT, "
            "linkedin_url TEXT, github_url TEXT, cv_markdown TEXT, "
            "date_updated TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO profile (id, full_name, cv_markdown) VALUES (1, 'Legacy', '# Old CV')"
        )
        conn.commit()

    db.init_db()

    with db.get_connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'profile'"
        ).fetchone()[0]
    assert {"name", "summary", "work_history", "education", "skills"} <= columns
    assert "cv_markdown" not in columns
    assert "CHECK (id = 1)" not in table_sql
    migrated = db.get_profile()
    assert migrated["name"] == "Default"
    assert migrated["full_name"] == "Legacy"
    assert migrated["summary"] == "# Old CV"
    # The rebuilt table accepts more than one profile and enforces unique names.
    assert db.create_profile("Second") is not None
    assert db.create_profile("default") is None


def test_init_db_migrates_profile_without_cv_markdown(db_path: Path) -> None:
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
    assert {"name", "summary", "work_history", "education", "skills"} <= columns
    assert db.get_profile()["full_name"] == "Legacy"
    assert db.get_profile()["name"] == "Default"


def test_init_db_migrates_single_row_profile_with_sections(db_path: Path) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE profile ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "full_name TEXT, location TEXT, phone TEXT, email TEXT, "
            "linkedin_url TEXT, github_url TEXT, "
            "summary TEXT, work_history TEXT, education TEXT, skills TEXT, "
            "date_updated TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO profile (id, full_name, summary) VALUES (1, 'Sergei', '# Summary body')"
        )
        conn.commit()

    db.init_db()

    profile = db.get_profile()
    assert profile["name"] == "Default"
    assert profile["full_name"] == "Sergei"
    assert profile["summary"] == "# Summary body"
    assert db.create_profile("Work CV") is not None


def test_init_db_profile_migration_is_idempotent(db_path: Path) -> None:
    db.init_db()
    db.save_profile(_profile_payload())
    db.init_db()
    db.init_db()

    profiles = db.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Default"
    assert profiles[0]["full_name"] == "Jane Doe"


def test_init_db_recovers_interrupted_profile_rebuild(db_path: Path) -> None:
    db.init_db()
    with db.get_connection() as conn:
        conn.execute(
            "CREATE TABLE profile_migrated ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT UNIQUE NOT NULL COLLATE NOCASE, "
            "full_name TEXT, location TEXT, phone TEXT, email TEXT, "
            "linkedin_url TEXT, github_url TEXT, "
            "summary TEXT, work_history TEXT, education TEXT, skills TEXT, "
            "date_updated TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO profile_migrated (id, name, full_name) "
            "VALUES (1, 'Recovered', 'Rescued Name')"
        )
        conn.commit()

    db.init_db()

    assert db.get_profile()["name"] == "Recovered"
    assert db.get_profile()["full_name"] == "Rescued Name"
    with db.get_connection() as conn:
        leftover = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile_migrated'"
        ).fetchone()
    assert leftover is None


def test_create_profile_validates_name(db_path: Path) -> None:
    db.init_db()
    assert db.create_profile("   ") is None
    first = db.create_profile("Default")
    assert first is not None
    assert db.create_profile("Default") is None
    assert db.create_profile("default") is None
    second = db.create_profile("Work CV")
    assert second is not None
    assert second != first


def test_list_profiles_orders_by_name(db_path: Path) -> None:
    db.init_db()
    db.create_profile("beta")
    db.create_profile("Alpha")
    db.create_profile("gamma")

    assert [profile["name"] for profile in db.list_profiles()] == ["Alpha", "beta", "gamma"]


def test_get_profile_by_id_and_missing(db_path: Path) -> None:
    db.init_db()
    first = db.create_profile("First")
    second = db.create_profile("Second")
    assert first is not None and second is not None

    assert db.get_profile(second)["name"] == "Second"
    assert db.get_profile(first)["name"] == "First"
    assert db.get_profile()["id"] == first
    assert db.get_profile(999)["id"] == 0
    assert db.get_profile(999)["name"] == ""


def test_delete_profile(db_path: Path) -> None:
    db.init_db()
    first = db.create_profile("First")
    second = db.create_profile("Second")
    assert first is not None and second is not None

    db.delete_profile(first)

    assert [profile["id"] for profile in db.list_profiles()] == [second]
    db.delete_profile(first)


def test_get_profile_without_row(db_path: Path) -> None:
    db.init_db()
    profile = db.get_profile()
    assert profile["id"] == 0
    assert profile["name"] == ""
    assert profile["summary"] == ""
    assert profile["work_history"] == ""
    assert profile["education"] == ""
    assert profile["skills"] == ""
    assert profile["full_name"] == ""


def test_setting_round_trip(db_path: Path) -> None:
    db.init_db()
    assert db.get_setting("dark_mode") is None
    assert db.get_setting("dark_mode", "system") == "system"

    db.set_setting("dark_mode", "dark")
    assert db.get_setting("dark_mode") == "dark"

    db.set_setting("dark_mode", "light")
    assert db.get_setting("dark_mode") == "light"


# --- Tags -------------------------------------------------------------------


def _tagged(url: str, company: str = "Acme", tags: tuple[str, ...] = ()) -> int:
    job_id = db.add_job(url, "Engineer", company, "Remote", "Body")
    assert job_id is not None
    if tags:
        db.set_job_tags(job_id, tags)
    return job_id


def test_create_tag_and_list(db_path: Path) -> None:
    db.init_db()
    assert db.list_tags() == []
    assert db.create_tag("startup") is not None
    assert db.create_tag("remote") is not None
    assert db.create_tag("REMOTE") is None  # case-insensitive duplicate
    assert db.create_tag("   ") is None  # empty after strip
    assert db.list_tags() == ["remote", "startup"]  # alphabetical, case-insensitive


def test_create_tag_preserves_original_case(db_path: Path) -> None:
    db.init_db()
    assert db.create_tag("Remote First") is not None
    assert db.list_tags() == ["Remote First"]
    assert db.find_tag("remote first") is not None  # lookup ignores case
    assert db.find_tag("  REMOTE FIRST  ") is not None  # and whitespace
    assert db.find_tag("missing") is None
    assert db.find_tag("") is None


def test_rename_tag(db_path: Path) -> None:
    db.init_db()
    remote = db.create_tag("remote")
    startup = db.create_tag("startup")
    assert remote is not None and startup is not None

    assert db.rename_tag(remote, "wfh") is True
    assert db.list_tags() == ["startup", "wfh"]

    assert db.rename_tag(startup, "WFH") is False  # collision with wfh
    assert db.rename_tag(startup, "  ") is False  # empty after strip
    assert db.rename_tag(99_999, "x") is False  # unknown id
    assert db.list_tags() == ["startup", "wfh"]


def test_rename_tag_keeps_assignments(db_path: Path) -> None:
    db.init_db()
    job_id = _tagged("https://a.com/1", tags=("remote",))
    tag_id = db.find_tag("remote")
    assert tag_id is not None

    assert db.rename_tag(tag_id, "wfh") is True
    assert db.job_tags(job_id) == ["wfh"]
    assert db.list_tags() == ["wfh"]


def test_delete_tag_cascades_assignments(db_path: Path) -> None:
    db.init_db()
    job_id = _tagged("https://a.com/1", tags=("remote", "startup"))
    tag_id = db.find_tag("remote")
    assert tag_id is not None

    db.delete_tag(tag_id)
    assert db.list_tags() == ["startup"]
    assert db.job_tags(job_id) == ["startup"]


def test_delete_job_cascades_assignments(db_path: Path) -> None:
    db.init_db()
    job_id = _tagged("https://a.com/1", tags=("remote",))
    db.delete_job(job_id)
    assert db.list_tags() == ["remote"]  # catalog survives
    assert db.tag_counts() == {"remote": 0}


def test_set_job_tags_creates_and_syncs(db_path: Path) -> None:
    db.init_db()
    job_id = _tagged("https://a.com/1")

    db.set_job_tags(job_id, ["remote", "Startup"])
    assert db.job_tags(job_id) == ["remote", "Startup"]  # alphabetical, case-insensitive
    assert db.list_tags() == ["remote", "Startup"]  # alphabetical, case-insensitive

    db.set_job_tags(job_id, ["startup", "referral"])  # Startup matched case-insensitively
    assert db.job_tags(job_id) == ["referral", "Startup"]
    assert db.list_tags() == ["referral", "remote", "Startup"]

    db.set_job_tags(job_id, [])
    assert db.job_tags(job_id) == []
    assert db.list_tags() == ["referral", "remote", "Startup"]  # tags are not jobs


def test_job_tags_empty_for_untagged(db_path: Path) -> None:
    db.init_db()
    job_id = _tagged("https://a.com/1")
    assert db.job_tags(job_id) == []


def test_tags_for_jobs(db_path: Path) -> None:
    db.init_db()
    first = _tagged("https://a.com/1", tags=("remote",))
    second = _tagged("https://a.com/2", tags=("startup", "remote"))

    grouped = db.tags_for_jobs([first, second])
    assert grouped == {first: ["remote"], second: ["remote", "startup"]}
    assert db.tags_for_jobs([first, 99_999])[first] == ["remote"]
    assert db.tags_for_jobs([]) == {}


def test_filter_by_tags_any_semantics(db_path: Path) -> None:
    db.init_db()
    remote_job = _tagged("https://a.com/1", company="Zeta", tags=("remote",))
    startup_job = _tagged("https://a.com/2", company="Alpha", tags=("startup",))
    _tagged("https://a.com/3", company="Mid")
    all_jobs = db.get_jobs()

    def kept(wanted: list[str]) -> list[int]:
        return [job["id"] for job in db.filter_by_tags(all_jobs, wanted)]

    assert kept([]) == [job["id"] for job in all_jobs]  # no filter
    assert kept(["referral"]) == []  # unknown tag
    assert kept(["REMOTE"]) == [remote_job]  # case-insensitive
    # ANY: remote + startup keeps both tagged jobs, preserves id-DESC order.
    assert kept(["remote", "startup"]) == [startup_job, remote_job]
    assert kept(["remote", "referral"]) == [remote_job]
    # Blank names are ignored, so no filter matches everything.
    assert kept(["   "]) == [job["id"] for job in all_jobs]


def test_tag_counts(db_path: Path) -> None:
    db.init_db()
    _tagged("https://a.com/1", tags=("remote",))
    _tagged("https://a.com/2", tags=("remote", "startup"))
    _tagged("https://a.com/3")

    assert db.tag_counts() == {"remote": 2, "startup": 1}


def test_clear_all_jobs_keeps_tag_catalog(db_path: Path) -> None:
    db.init_db()
    _tagged("https://a.com/1", tags=("remote",))

    assert db.clear_all_jobs() == 1
    assert db.list_tags() == ["remote"]
    assert db.tag_counts() == {"remote": 0}


def test_clear_all_data_wipes_tags(db_path: Path) -> None:
    db.init_db()
    _tagged("https://a.com/1", tags=("remote",))

    counts = db.clear_all_data()
    assert counts == {"positions": 1, "settings": 0}
    assert db.list_tags() == []
    assert db.tag_counts() == {}
