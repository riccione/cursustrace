"""SQLite persistence layer for cursustrace."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast

from rapidfuzz import fuzz

from cursustrace.utils import clean_url, generate_fingerprint

DEFAULT_DB_PATH = Path("data/cursustrace.db")

FUZZY_THRESHOLD = 85
FUZZY_CANDIDATE_LIMIT = 50

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
    date_applied TEXT,
    fingerprint TEXT UNIQUE
)
"""

CREATE_FINGERPRINT_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint)"
)


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
    fingerprint: str | None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "fingerprint" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN fingerprint TEXT")
        for row in conn.execute("SELECT id, company, title, location FROM jobs").fetchall():
            conn.execute(
                "UPDATE jobs SET fingerprint = ? WHERE id = ?",
                (generate_fingerprint(row["company"], row["title"], row["location"]), row["id"]),
            )
    conn.execute(CREATE_FINGERPRINT_INDEX)



def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, creating the parent directory and database on first use."""
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the jobs table if needed and apply schema migrations."""
    with closing(get_connection()) as conn:
        conn.execute(CREATE_JOBS_TABLE)
        _migrate(conn)
        conn.commit()


def _duplicate_reason(conn: sqlite3.Connection, url: str, fingerprint: str) -> str | None:
    cleaned = clean_url(url)
    for row in conn.execute("SELECT job_url FROM jobs").fetchall():
        if clean_url(row["job_url"]) == cleaned:
            return "Duplicate position already applied/tracked"
    if fingerprint:
        match = conn.execute(
            "SELECT 1 FROM jobs WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if match is not None:
            return "Duplicate position already applied/tracked"
    return None


def _fuzzy_reason(conn: sqlite3.Connection, company: str | None, description: str | None) -> str | None:
    if not company or not description:
        return None
    rows = conn.execute(
        "SELECT description FROM jobs WHERE company = ? ORDER BY id DESC LIMIT ?",
        (company, FUZZY_CANDIDATE_LIMIT),
    ).fetchall()
    for row in rows:
        candidate = row["description"]
        if candidate and fuzz.token_set_ratio(description, candidate) > FUZZY_THRESHOLD:
            return "Duplicate position already applied/tracked"
    return None


def check_duplicate(
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
) -> tuple[bool, str]:
    """Detect duplicates by canonical URL, fingerprint, or fuzzy description match."""
    fingerprint = generate_fingerprint(company, title, location)
    with closing(get_connection()) as conn:
        reason = _duplicate_reason(conn, url, fingerprint)
        if reason is None:
            reason = _fuzzy_reason(conn, company, description)
    if reason is None:
        return (False, "")
    return (True, "Duplicate position already applied/tracked")


def add_job(
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    fingerprint: str | None = None,
) -> bool:
    """Insert a job listing; return False when the URL or fingerprint already exists."""
    if fingerprint is None:
        fingerprint = generate_fingerprint(company, title, location)
    try:
        with closing(get_connection()) as conn:
            conn.execute(
                "INSERT INTO jobs "
                "(job_url, title, company, location, description, date_added, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (url, title, company, location, description, _now(), fingerprint),
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
