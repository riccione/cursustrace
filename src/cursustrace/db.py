"""SQLite persistence layer for cursustrace."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast

DEFAULT_DB_PATH = Path("data/cursustrace.db")

CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT UNIQUE NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    description TEXT,
    applied INTEGER DEFAULT 0,
    date_added TEXT NOT NULL,
    date_applied TEXT
)
"""


class Job(TypedDict):
    """A stored job listing row."""

    id: int
    job_url: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    applied: int
    date_added: str
    date_applied: str | None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, creating the parent directory and database on first use."""
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the jobs table if it does not already exist."""
    with closing(get_connection()) as conn:
        conn.execute(CREATE_JOBS_TABLE)
        conn.commit()


def add_job(
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
) -> bool:
    """Insert a job listing; return False when the URL already exists."""
    try:
        with closing(get_connection()) as conn:
            conn.execute(
                "INSERT INTO jobs (job_url, title, company, location, description, date_added) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (url, title, company, location, description, _now()),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return False
    return True


def update_applied_status(job_id: int, applied: bool) -> None:
    """Mark a job as applied or not, stamping or clearing date_applied."""
    date_applied = _now() if applied else None
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE jobs SET applied = ?, date_applied = ? WHERE id = ?",
            (1 if applied else 0, date_applied, job_id),
        )
        conn.commit()


def get_jobs(applied_filter: bool | None = None) -> list[Job]:
    """Return jobs ordered by id descending, optionally filtered by applied status."""
    query = "SELECT * FROM jobs"
    params: tuple[int, ...] = ()
    if applied_filter is not None:
        query += " WHERE applied = ?"
        params = (1 if applied_filter else 0,)
    query += " ORDER BY id DESC"

    with closing(get_connection()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [cast(Job, dict(row)) for row in rows]
