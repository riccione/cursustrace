"""SQLite persistence layer for cursustrace."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal, TypedDict, cast

from rapidfuzz import fuzz, process, utils

from cursustrace.utils import clean_url, generate_fingerprint, role_key

DEFAULT_DB_PATH = Path("data/cursustrace.db")

FUZZY_THRESHOLD = 85
FUZZY_CANDIDATE_LIMIT = 50
COMPANY_SEARCH_THRESHOLD = 70
SIMILAR_LIMIT = 3

FINGERPRINT_SCHEME_VERSION = 1

CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT UNIQUE NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    description TEXT,
    applied INTEGER DEFAULT 0,
    interview INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    date_added TEXT NOT NULL,
    date_applied TEXT,
    date_interview TEXT,
    date_rejected TEXT,
    applied_comment TEXT,
    interview_comment TEXT,
    rejected_comment TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT,
    salary_period TEXT,
    salary_note TEXT,
    fingerprint TEXT UNIQUE
)
"""

CREATE_PROFILE_TABLE = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    full_name TEXT,
    location TEXT,
    phone TEXT,
    email TEXT,
    linkedin_url TEXT,
    github_url TEXT,
    summary TEXT,
    work_history TEXT,
    education TEXT,
    skills TEXT,
    date_updated TEXT DEFAULT (datetime('now'))
)
"""

CREATE_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""

CREATE_FINGERPRINT_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint)"
)

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CREATE_EVENTS_INDEX = "CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id)"

CREATE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE
)
"""

CREATE_JOB_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS job_tags (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, tag_id)
)
"""

CREATE_JOB_TAGS_INDEX = "CREATE INDEX IF NOT EXISTS idx_job_tags_tag_id ON job_tags(tag_id)"


class Job(TypedDict):
    """A stored job listing row."""

    id: int
    job_url: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    applied: int
    interview: int
    rejected: int
    date_added: str
    date_applied: str | None
    date_interview: str | None
    date_rejected: str | None
    applied_comment: str | None
    interview_comment: str | None
    rejected_comment: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: str | None
    salary_note: str | None
    fingerprint: str | None


JobStatus = Literal["unapplied", "applied", "interview", "rejected"]
JobFlag = Literal["applied", "interview", "rejected"]


class Event(TypedDict):
    """A logged pipeline status change for a job."""

    id: int
    job_id: int
    status: JobStatus
    created_at: str


def job_status(job: Job) -> JobStatus:
    """Derive a job's current pipeline stage from its status flags."""
    if job["interview"]:
        return "interview"
    if job["rejected"]:
        return "rejected"
    if job["applied"]:
        return "applied"
    return "unapplied"


JobSort = Literal["newest", "oldest", "company", "company_desc", "status"]

STATUS_ORDER: dict[JobStatus, int] = {
    "unapplied": 0,
    "applied": 1,
    "interview": 2,
    "rejected": 3,
}


def sort_jobs(jobs: list[Job], order: JobSort = "newest") -> list[Job]:
    """Return a new list ordered by ``order``, keeping ``id`` descending on ties.

    ``newest``/``oldest`` sort by ``date_added`` (an ISO-like timestamp, so
    lexicographic order is chronological); ``company`` is case-insensitive;
    ``status`` groups by pipeline stage, newest first inside each stage.
    """
    if order in ("newest", "oldest"):
        return sorted(
            jobs,
            key=lambda job: (job["date_added"], job["id"]),
            reverse=order == "newest",
        )
    ordered = sorted(jobs, key=lambda job: job["id"], reverse=True)
    if order == "status":
        return sorted(ordered, key=lambda job: STATUS_ORDER[job_status(job)])
    return sorted(
        ordered,
        key=lambda job: (job["company"] or "").casefold(),
        reverse=order == "company_desc",
    )


class Profile(TypedDict):
    """The single user profile row, including the sectioned Markdown CV."""

    full_name: str
    location: str
    phone: str
    email: str
    linkedin_url: str
    github_url: str
    summary: str
    work_history: str
    education: str
    skills: str
    date_updated: str | None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _recompute_fingerprints(conn: sqlite3.Connection) -> None:
    """Recompute job fingerprints once when the fingerprint scheme changes."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= FINGERPRINT_SCHEME_VERSION:
        return
    rows = conn.execute("SELECT id, job_url, company, title FROM jobs").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE jobs SET fingerprint = ? WHERE id = ?",
            (generate_fingerprint(row["job_url"], row["company"], row["title"]), row["id"]),
        )
    # `user_version` is a fixed internal integer constant (PRAGMAs cannot be parameterized).
    conn.execute(f"PRAGMA user_version = {FINGERPRINT_SCHEME_VERSION}")


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "fingerprint" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN fingerprint TEXT")
    # Column names and definitions are fixed internal constants (DDL identifiers
    # cannot be parameterized), never user input.
    for column, definition in (
        ("interview", "INTEGER DEFAULT 0"),
        ("rejected", "INTEGER DEFAULT 0"),
        ("date_interview", "TEXT"),
        ("date_rejected", "TEXT"),
        ("applied_comment", "TEXT"),
        ("interview_comment", "TEXT"),
        ("rejected_comment", "TEXT"),
        ("salary_min", "INTEGER"),
        ("salary_max", "INTEGER"),
        ("salary_currency", "TEXT"),
        ("salary_period", "TEXT"),
        ("salary_note", "TEXT"),
    ):
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    conn.execute(CREATE_FINGERPRINT_INDEX)
    _recompute_fingerprints(conn)


def _migrate_profile(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
    for column in ("summary", "work_history", "education", "skills"):
        if column not in columns:
            conn.execute(f"ALTER TABLE profile ADD COLUMN {column} TEXT")
    if "cv_markdown" in columns:
        conn.execute("ALTER TABLE profile DROP COLUMN cv_markdown")


def _backfill_events(conn: sqlite3.Connection) -> None:
    """Seed events from stored stage dates for jobs created before logging existed."""
    rows = conn.execute(
        "SELECT id, date_applied, date_interview, date_rejected FROM jobs"
    ).fetchall()
    for job in rows:
        stamps = [
            (job["date_applied"], "applied"),
            (job["date_interview"], "interview"),
            (job["date_rejected"], "rejected"),
        ]
        for created_at, status in sorted(entry for entry in stamps if entry[0] is not None):
            conn.execute(
                "INSERT INTO events (job_id, status, created_at) VALUES (?, ?, ?)",
                (job["id"], status, created_at),
            )


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, creating the parent directory and database on first use."""
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create the jobs and profile tables if needed and apply schema migrations."""
    with closing(get_connection()) as conn:
        events_existed = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
            ).fetchone()
            is not None
        )
        conn.execute(CREATE_JOBS_TABLE)
        conn.execute(CREATE_PROFILE_TABLE)
        conn.execute(CREATE_SETTINGS_TABLE)
        conn.execute(CREATE_EVENTS_TABLE)
        conn.execute(CREATE_TAGS_TABLE)
        conn.execute(CREATE_JOB_TAGS_TABLE)
        _migrate(conn)
        _migrate_profile(conn)
        conn.execute(CREATE_EVENTS_INDEX)
        conn.execute(CREATE_JOB_TAGS_INDEX)
        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")
        if not events_existed:
            _backfill_events(conn)
        conn.commit()


def _canonical_url_exists(conn: sqlite3.Connection, url: str, exclude_id: int) -> bool:
    cleaned = clean_url(url)
    for row in conn.execute("SELECT job_url FROM jobs WHERE id != ?", (exclude_id,)).fetchall():
        if clean_url(row["job_url"]) == cleaned:
            return True
    return False


def check_duplicate(url: str, *, exclude_id: int | None = None) -> bool:
    """Return True when another job already uses the same canonical URL."""
    exclude = exclude_id if exclude_id is not None else -1
    with closing(get_connection()) as conn:
        return _canonical_url_exists(conn, url, exclude)


def find_similar(
    company: str | None,
    title: str | None,
    description: str | None,
    *,
    exclude_id: int | None = None,
    limit: int = SIMILAR_LIMIT,
) -> list[Job]:
    """Return jobs that look like the same role on another link (advisory only)."""
    if not company and not description:
        return []
    exclude = exclude_id if exclude_id is not None else -1
    target_role = role_key(company, title) if company and title else None
    matches: list[Job] = []
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE id != ? ORDER BY id DESC", (exclude,)
        ).fetchall()
        for row in rows:
            job = cast(Job, dict(row))
            same_role = (
                target_role is not None
                and bool(job["company"] and job["title"])
                and role_key(job["company"], job["title"]) == target_role
            )
            candidate_description = job["description"]
            similar_description = (
                company is not None
                and job["company"] == company
                and description is not None
                and candidate_description is not None
                and fuzz.token_set_ratio(description, candidate_description) > FUZZY_THRESHOLD
            )
            if same_role or similar_description:
                matches.append(job)
                if len(matches) >= limit:
                    break
    return matches


def add_job(
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    *,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    salary_note: str | None = None,
) -> int | None:
    """Insert a job listing; return the new id, or None when the URL/fingerprint exists."""
    fingerprint = generate_fingerprint(url, company, title)
    now = _now()
    try:
        with closing(get_connection()) as conn:
            cursor = conn.execute(
                "INSERT INTO jobs "
                "(job_url, title, company, location, description, date_added, fingerprint, "
                "salary_min, salary_max, salary_currency, salary_period, salary_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url,
                    title,
                    company,
                    location,
                    description,
                    now,
                    fingerprint,
                    salary_min,
                    salary_max,
                    salary_currency,
                    salary_period,
                    salary_note,
                ),
            )
            rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO events (job_id, status, created_at) VALUES (?, ?, ?)",
                (rowid, "unapplied", now),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None
    return cast("int", rowid)


def get_job(job_id: int) -> Job | None:
    """Return a single job row by id, or None when it does not exist."""
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return cast(Job, dict(row)) if row is not None else None


def get_events(job_id: int) -> list[Event]:
    """Return a job's logged status changes in chronological order."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    return [cast(Event, dict(row)) for row in rows]


def set_job_status(job_id: int, status: JobStatus) -> None:
    """Move a job to a pipeline stage, stamping its date and preserving earlier ones."""
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return

        previous = job_status(cast(Job, dict(row)))
        now = _now()
        if status == "unapplied":
            applied = interview = rejected = 0
            date_applied = date_interview = date_rejected = None
        else:
            applied = 1 if status == "applied" else 0
            interview = 1 if status == "interview" else 0
            rejected = 1 if status == "rejected" else 0
            date_applied = row["date_applied"] or now
            date_interview = now if status == "interview" else None
            date_rejected = now if status == "rejected" else None
            if status == "rejected":
                date_interview = row["date_interview"]

        conn.execute(
            "UPDATE jobs SET applied = ?, interview = ?, rejected = ?, "
            "date_applied = ?, date_interview = ?, date_rejected = ? WHERE id = ?",
            (
                applied,
                interview,
                rejected,
                date_applied,
                date_interview,
                date_rejected,
                job_id,
            ),
        )
        if previous != status:
            conn.execute(
                "INSERT INTO events (job_id, status, created_at) VALUES (?, ?, ?)",
                (job_id, status, now),
            )
        conn.commit()


_COMMENT_COLUMNS: dict[JobFlag, str] = {
    "applied": "applied_comment",
    "interview": "interview_comment",
    "rejected": "rejected_comment",
}


def set_job_comment(job_id: int, stage: JobFlag, comment: str) -> None:
    """Store the free-text comment for a pipeline stage."""
    column = _COMMENT_COLUMNS[stage]
    with closing(get_connection()) as conn:
        # `column` is a fixed internal name from _COMMENT_COLUMNS, never user input.
        conn.execute(f"UPDATE jobs SET {column} = ? WHERE id = ?", (comment, job_id))
        conn.commit()


def update_job_comments(job_id: int, applied: str, interview: str, rejected: str) -> None:
    """Replace all three stage comments at once."""
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE jobs SET applied_comment = ?, interview_comment = ?, "
            "rejected_comment = ? WHERE id = ?",
            (applied, interview, rejected, job_id),
        )
        conn.commit()


def get_jobs(status: JobStatus | None = None) -> list[Job]:
    """Return jobs ordered by id descending, optionally filtered by pipeline status."""
    query = "SELECT * FROM jobs"
    params: tuple[int, ...] = ()
    if status == "unapplied":
        query += " WHERE applied = 0 AND interview = 0 AND rejected = 0"
    elif status == "applied":
        query += " WHERE applied = 1"
    elif status == "interview":
        query += " WHERE interview = 1"
    elif status == "rejected":
        query += " WHERE rejected = 1"
    query += " ORDER BY id DESC"

    with closing(get_connection()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [cast(Job, dict(row)) for row in rows]


def job_counts() -> dict[str, int]:
    """Return position totals per pipeline stage (plus 'total')."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(applied), 0) AS applied, "
            "COALESCE(SUM(interview), 0) AS interview, "
            "COALESCE(SUM(rejected), 0) AS rejected FROM jobs"
        ).fetchone()
    total = int(row["total"])
    applied = int(row["applied"])
    interview = int(row["interview"])
    rejected = int(row["rejected"])
    return {
        "total": total,
        "unapplied": total - applied - interview - rejected,
        "applied": applied,
        "interview": interview,
        "rejected": rejected,
    }


def applications_by_month() -> list[tuple[str, int]]:
    """Return application counts per calendar month (YYYY-MM), ascending."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT substr(date_applied, 1, 7) AS month, COUNT(*) AS count "
            "FROM jobs WHERE date_applied IS NOT NULL GROUP BY month ORDER BY month"
        ).fetchall()
    return [(str(row["month"]), int(row["count"])) for row in rows]


def pipeline_funnel() -> dict[str, int]:
    """Return monotonic pipeline counts for the response funnel.

    Stages are counted from the event log so they survive unmarking a
    status (which clears ``date_applied``): ``applied`` counts jobs that
    ever left the unapplied stage, ``response`` jobs that reached an
    interview or a rejection, and ``interview`` jobs that reached an
    interview. The subset relation guarantees a non-increasing funnel.
    """
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT "
            "COUNT(DISTINCT CASE WHEN status != 'unapplied' THEN job_id END) AS applied, "
            "COUNT(DISTINCT CASE WHEN status IN ('interview', 'rejected') "
            "THEN job_id END) AS response, "
            "COUNT(DISTINCT CASE WHEN status = 'interview' THEN job_id END) AS interview "
            "FROM events"
        ).fetchone()
    applied = int(row["applied"])
    response = int(row["response"])
    interview = int(row["interview"])
    return {
        "added": job_counts()["total"],
        "applied": applied,
        "response": response,
        "interview": interview,
    }


def search_jobs(
    query: str,
    status: JobStatus | None = None,
    *,
    threshold: int = COMPANY_SEARCH_THRESHOLD,
) -> list[Job]:
    """Return jobs whose company fuzzy-matches the query, best match first."""
    text = (query or "").strip()
    jobs = get_jobs(status=status)
    if not text:
        return jobs

    choices = {job["id"]: job["company"] or "" for job in jobs}
    matches = process.extract(
        text,
        choices,
        scorer=fuzz.WRatio,
        processor=utils.default_process,
        score_cutoff=threshold,
        limit=None,
    )
    by_id = {job["id"]: job for job in jobs}
    return [by_id[key] for _choice, _score, key in matches]


def clear_all_jobs() -> int:
    """Delete every job row, reset the auto-increment counter, and return the count."""
    with closing(get_connection()) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
        conn.commit()
    return count


def delete_job(job_id: int) -> None:
    """Delete a single job listing by id."""
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()


def clear_all_data() -> dict[str, int]:
    """Delete all jobs, the profile row, settings, and tags; return the counts."""
    with closing(get_connection()) as conn:
        positions = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        settings = int(conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0])
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM settings")
        conn.execute("DELETE FROM tags")
        conn.commit()
    return {"positions": positions, "settings": settings}


def list_tags() -> list[str]:
    """Return every tag name, alphabetically, case-insensitively."""
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    return [row["name"] for row in rows]


def tag_counts() -> dict[str, int]:
    """Return tag name -> number of assigned positions, alphabetically."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT t.name, COUNT(jt.job_id) AS uses FROM tags t "
            "LEFT JOIN job_tags jt ON jt.tag_id = t.id GROUP BY t.id "
            "ORDER BY t.name COLLATE NOCASE"
        ).fetchall()
    return {row["name"]: int(row["uses"]) for row in rows}


def find_tag(name: str) -> int | None:
    """Return the id of a tag by name (case-insensitive), or None when unknown."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (cleaned,)
        ).fetchone()
    return int(row["id"]) if row is not None else None


def create_tag(name: str) -> int | None:
    """Create a tag; return its id, or None when empty or already present."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    with closing(get_connection()) as conn:
        try:
            cursor = conn.execute("INSERT INTO tags (name) VALUES (?)", (cleaned,))
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return cast("int", cursor.lastrowid)


def rename_tag(tag_id: int, new_name: str) -> bool:
    """Rename a tag; False when the new name is empty, taken, or the id is unknown."""
    cleaned = (new_name or "").strip()
    if not cleaned:
        return False
    try:
        with closing(get_connection()) as conn:
            cursor = conn.execute("UPDATE tags SET name = ? WHERE id = ?", (cleaned, tag_id))
            conn.commit()
    except sqlite3.IntegrityError:
        return False
    return cursor.rowcount > 0


def delete_tag(tag_id: int) -> None:
    """Delete a tag; its job assignments go away with it (ON DELETE CASCADE)."""
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.commit()


def _ensure_tag(conn: sqlite3.Connection, name: str) -> int | None:
    """Return the tag id for ``name``, creating it when missing (case-insensitive)."""
    cleaned = name.strip()
    if not cleaned:
        return None
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (cleaned,)).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = conn.execute("INSERT INTO tags (name) VALUES (?)", (cleaned,))
    return cast("int", cursor.lastrowid)


def set_job_tags(job_id: int, names: list[str] | tuple[str, ...]) -> None:
    """Replace a job's tags, creating any unknown names."""
    with closing(get_connection()) as conn:
        wanted: set[int] = set()
        for name in names:
            tag_id = _ensure_tag(conn, name)
            if tag_id is not None:
                wanted.add(tag_id)
        current = {
            int(row["tag_id"])
            for row in conn.execute(
                "SELECT tag_id FROM job_tags WHERE job_id = ?", (job_id,)
            ).fetchall()
        }
        for tag_id in current - wanted:
            conn.execute("DELETE FROM job_tags WHERE job_id = ? AND tag_id = ?", (job_id, tag_id))
        for tag_id in wanted - current:
            conn.execute(
                "INSERT OR IGNORE INTO job_tags (job_id, tag_id) VALUES (?, ?)",
                (job_id, tag_id),
            )
        conn.commit()


def job_tags(job_id: int) -> list[str]:
    """Return one job's tag names, alphabetically, case-insensitively."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT t.name FROM job_tags jt JOIN tags t ON t.id = jt.tag_id "
            "WHERE jt.job_id = ? ORDER BY t.name COLLATE NOCASE",
            (job_id,),
        ).fetchall()
    return [row["name"] for row in rows]


def tags_for_jobs(job_ids: list[int]) -> dict[int, list[str]]:
    """Return job id -> tag names for a batch of jobs (missing ids map to [])."""
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT jt.job_id, t.name FROM job_tags jt JOIN tags t ON t.id = jt.tag_id "
            f"WHERE jt.job_id IN ({placeholders}) ORDER BY t.name COLLATE NOCASE",
            tuple(job_ids),
        ).fetchall()
    grouped: dict[int, list[str]] = {job_id: [] for job_id in job_ids}
    for row in rows:
        grouped[int(row["job_id"])].append(row["name"])
    return grouped


def filter_by_tags(jobs: list[Job], wanted: list[str] | tuple[str, ...]) -> list[Job]:
    """Keep jobs carrying at least one of ``wanted`` (case-insensitive); no-op when empty."""
    names = [name.strip() for name in wanted if name.strip()]
    if not names or not jobs:
        return jobs
    placeholders = ",".join("?" for _ in names)
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT jt.job_id FROM job_tags jt JOIN tags t ON t.id = jt.tag_id "
            f"WHERE t.name COLLATE NOCASE IN ({placeholders})",
            tuple(names),
        ).fetchall()
    keep = {int(row["job_id"]) for row in rows}
    return [job for job in jobs if job["id"] in keep]


def update_job(
    job_id: int,
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    *,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    salary_note: str | None = None,
) -> bool:
    """Update a job's editable fields; return False when the URL or fingerprint collides."""
    fingerprint = generate_fingerprint(url, company, title)
    try:
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE jobs SET job_url = ?, title = ?, company = ?, location = ?, "
                "description = ?, fingerprint = ?, salary_min = ?, salary_max = ?, "
                "salary_currency = ?, salary_period = ?, salary_note = ? WHERE id = ?",
                (
                    url,
                    title,
                    company,
                    location,
                    description,
                    fingerprint,
                    salary_min,
                    salary_max,
                    salary_currency,
                    salary_period,
                    salary_note,
                    job_id,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return False
    return True


def get_setting(key: str, default: str | None = None) -> str | None:
    """Return a stored preference value, or the default when unset."""
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(key: str, value: str) -> None:
    """Store a preference value, replacing any existing value for the key."""
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def save_profile(data: Profile) -> None:
    """Upsert the single profile row with contact metadata and the Markdown CV sections."""
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile "
            "(id, full_name, location, phone, email, linkedin_url, github_url, "
            "summary, work_history, education, skills, date_updated) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                data["full_name"],
                data["location"],
                data["phone"],
                data["email"],
                data["linkedin_url"],
                data["github_url"],
                data["summary"],
                data["work_history"],
                data["education"],
                data["skills"],
            ),
        )
        conn.commit()


def get_profile() -> Profile:
    """Return the stored profile, or an empty profile when no row exists."""
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        return {
            "full_name": "",
            "location": "",
            "phone": "",
            "email": "",
            "linkedin_url": "",
            "github_url": "",
            "summary": "",
            "work_history": "",
            "education": "",
            "skills": "",
            "date_updated": None,
        }
    return {
        "full_name": row["full_name"] or "",
        "location": row["location"] or "",
        "phone": row["phone"] or "",
        "email": row["email"] or "",
        "linkedin_url": row["linkedin_url"] or "",
        "github_url": row["github_url"] or "",
        "summary": row["summary"] or "",
        "work_history": row["work_history"] or "",
        "education": row["education"] or "",
        "skills": row["skills"] or "",
        "date_updated": row["date_updated"],
    }
