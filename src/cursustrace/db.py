"""SQLite persistence layer for cursustrace."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Final, Literal, TypedDict, cast, get_args

from rapidfuzz import fuzz, process, utils

from cursustrace import config
from cursustrace.utils import clean_url, generate_fingerprint, role_key

DEFAULT_DB_PATH: Path = config.DEFAULT_DB_PATH

FUZZY_THRESHOLD = 85
FUZZY_CANDIDATE_LIMIT = 50
COMPANY_SEARCH_THRESHOLD = 70
SIMILAR_LIMIT = 3

FINGERPRINT_SCHEME_VERSION = 2

# Column definitions shared by CREATE_JOBS_TABLE and the jobs table rebuild.
# Uniqueness moved to per-user partial indexes in init_db.
_JOBS_TABLE_BODY = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_url TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    description TEXT,
    applied INTEGER DEFAULT 0,
    interview INTEGER DEFAULT 0,
    offer INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    outdated INTEGER DEFAULT 0,
    date_added TEXT NOT NULL,
    date_applied TEXT,
    date_interview TEXT,
    date_offer TEXT,
    date_rejected TEXT,
    date_outdated TEXT,
    applied_comment TEXT,
    interview_comment TEXT,
    offer_comment TEXT,
    rejected_comment TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT,
    salary_period TEXT,
    salary_note TEXT,
    deadline TEXT,
    fingerprint TEXT,
    profile_id INTEGER,
    profile_name TEXT,
    user_id INTEGER
"""

CREATE_JOBS_TABLE = f"CREATE TABLE IF NOT EXISTS jobs ({_JOBS_TABLE_BODY})"

# Column definitions shared by CREATE_PROFILE_TABLE and the profile table rebuild.
_PROFILE_COLUMNS = (
    "id",
    "name",
    "full_name",
    "location",
    "phone",
    "email",
    "linkedin_url",
    "github_url",
    "summary",
    "work_history",
    "education",
    "skills",
    "date_updated",
    "user_id",
)
_PROFILE_TABLE_BODY = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
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
    date_updated TEXT DEFAULT (datetime('now')),
    user_id INTEGER
"""

CREATE_PROFILE_TABLE = f"CREATE TABLE IF NOT EXISTS profile ({_PROFILE_TABLE_BODY})"

CREATE_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""

# Per-user uniqueness: rows with no owner share one global partition; each
# user gets their own partition so two users may track the same URL/tag.
# `COLLATE NOCASE` mirrors the old column-level UNIQUE on tags/profile names.
OWNERSHIP_INDEXES = (
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url_unowned "
        "ON jobs(job_url) WHERE user_id IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_fp_unowned "
        "ON jobs(fingerprint) WHERE user_id IS NULL AND fingerprint IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url_user "
        "ON jobs(user_id, job_url) WHERE user_id IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_fp_user "
        "ON jobs(user_id, fingerprint) WHERE user_id IS NOT NULL AND fingerprint IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_unowned "
        "ON tags(name COLLATE NOCASE) WHERE user_id IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_user "
        "ON tags(user_id, name COLLATE NOCASE) WHERE user_id IS NOT NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_name_unowned "
        "ON profile(name COLLATE NOCASE) WHERE user_id IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_name_user "
        "ON profile(user_id, name COLLATE NOCASE) WHERE user_id IS NOT NULL"
    ),
)

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CREATE_EVENTS_INDEX = "CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id)"

# Column definitions shared by CREATE_TAGS_TABLE and the tags table rebuild.
_TAGS_TABLE_BODY = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    user_id INTEGER
"""

CREATE_TAGS_TABLE = f"CREATE TABLE IF NOT EXISTS tags ({_TAGS_TABLE_BODY})"

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
    offer: int
    rejected: int
    outdated: int
    date_added: str
    date_applied: str | None
    date_interview: str | None
    date_offer: str | None
    date_rejected: str | None
    date_outdated: str | None
    applied_comment: str | None
    interview_comment: str | None
    offer_comment: str | None
    rejected_comment: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: str | None
    salary_note: str | None
    deadline: str | None
    fingerprint: str | None
    profile_id: int | None
    profile_name: str | None
    user_id: int | None


JobStatus = Literal["unapplied", "applied", "interview", "offer", "rejected", "outdated"]
JobFlag = Literal["applied", "interview", "offer", "rejected"]

JOB_STATUSES: Final[tuple[JobStatus, ...]] = cast("tuple[JobStatus, ...]", get_args(JobStatus))
"""Every pipeline stage, in pipeline order."""

PIPELINE_STAGES: Final[tuple[JobStatus, ...]] = (
    "unapplied",
    "applied",
    "interview",
    "offer",
)
"""The stages a position walks through; rejected and outdated are terminal."""


class Event(TypedDict):
    """A logged pipeline status change for a job."""

    id: int
    job_id: int
    status: JobStatus
    created_at: str


def job_status(job: Job) -> JobStatus:
    """Derive a job's current pipeline stage from its status flags."""
    if job["offer"]:
        return "offer"
    if job["interview"]:
        return "interview"
    if job["rejected"]:
        return "rejected"
    if job["outdated"]:
        return "outdated"
    if job["applied"]:
        return "applied"
    return "unapplied"


JobSort = Literal["newest", "oldest", "company", "company_desc", "status"]

STATUS_ORDER: Final[dict[JobStatus, int]] = {
    status: index for index, status in enumerate(JOB_STATUSES)
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
    """One named CV profile: contact metadata plus the sectioned Markdown CV."""

    id: int
    name: str
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
    user_id: int | None


class UserRow(TypedDict):
    """A registered account row."""

    id: int
    email: str
    password_hash: str
    is_admin: int


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
        ("offer", "INTEGER DEFAULT 0"),
        ("rejected", "INTEGER DEFAULT 0"),
        ("outdated", "INTEGER DEFAULT 0"),
        ("date_interview", "TEXT"),
        ("date_offer", "TEXT"),
        ("date_rejected", "TEXT"),
        ("date_outdated", "TEXT"),
        ("applied_comment", "TEXT"),
        ("interview_comment", "TEXT"),
        ("offer_comment", "TEXT"),
        ("rejected_comment", "TEXT"),
        ("salary_min", "INTEGER"),
        ("salary_max", "INTEGER"),
        ("salary_currency", "TEXT"),
        ("salary_period", "TEXT"),
        ("salary_note", "TEXT"),
        ("deadline", "TEXT"),
        ("profile_id", "INTEGER"),
        ("profile_name", "TEXT"),
        ("user_id", "INTEGER"),
    ):
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    _recompute_fingerprints(conn)


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    # Table names are fixed internal constants (sqlite_master cannot be parameterized).
    row = conn.execute(
        f"SELECT sql FROM sqlite_master WHERE type = 'table' AND name = '{table}'"
    ).fetchone()
    return str(row[0]) if row is not None else ""


def _rebuild_table(conn: sqlite3.Connection, table: str, body: str, columns: list[str]) -> None:
    """Swap ``table`` for a rebuilt copy under ``body``; FKs disabled for the swap.

    ``PRAGMA foreign_keys`` is a no-op inside a transaction, so any open
    transaction is committed first; with FKs off, DROP TABLE leaves child rows
    intact and the copied primary keys keep their references valid after the
    rename.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        # `table` is a fixed internal constant, never user input.
        conn.execute(f"CREATE TABLE {table}_migrated ({body})")
        conn.execute(
            f"INSERT INTO {table}_migrated ({', '.join(columns)}) "
            f"SELECT {', '.join(columns)} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_migrated RENAME TO {table}")
        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys=ON")
        raise


def _migrate_jobs_uniqueness(conn: sqlite3.Connection) -> None:
    """Rebuild jobs once, replacing column-level UNIQUE with partial indexes."""
    if "job_url TEXT UNIQUE" not in _table_sql(conn, "jobs"):
        return
    stale_index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_jobs_fingerprint'"
    ).fetchone()
    if stale_index is None and "fingerprint TEXT UNIQUE" not in _table_sql(conn, "jobs"):
        return
    columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    _rebuild_table(conn, "jobs", _JOBS_TABLE_BODY, columns)


def _migrate_tags_uniqueness(conn: sqlite3.Connection) -> None:
    """Rebuild tags once, replacing the column-level UNIQUE name with partial indexes."""
    if "name TEXT UNIQUE" not in _table_sql(conn, "tags"):
        return
    columns = [row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()]
    _rebuild_table(conn, "tags", _TAGS_TABLE_BODY, columns)


def _migrate_profile(conn: sqlite3.Connection) -> None:
    _recover_profile_rebuild(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
    legacy = "name" not in columns
    name_unique = "name TEXT UNIQUE" in _table_sql(conn, "profile")
    if not legacy and not name_unique:
        return
    # Legacy tables are single-row (CHECK (id = 1)); rebuild to the multi-profile
    # schema, backfilling a name and carrying over any legacy CV content.
    # Non-legacy rebuilds only drop the global UNIQUE name (moved to partial
    # per-user indexes) and add user_id.
    if legacy:
        select_columns = ["id", "'Default' AS name"]
        for column in ("full_name", "location", "phone", "email", "linkedin_url", "github_url"):
            select_columns.append(column if column in columns else "NULL")
        for column in ("summary", "work_history", "education", "skills"):
            if column == "summary" and "cv_markdown" in columns:
                if "summary" in columns:
                    select_columns.append(
                        "CASE WHEN TRIM(COALESCE(summary, '')) = '' "
                        "THEN COALESCE(cv_markdown, '') ELSE summary END"
                    )
                else:
                    select_columns.append("COALESCE(cv_markdown, '')")
            else:
                select_columns.append(f"COALESCE({column}, '')" if column in columns else "''")
        select_columns.append("date_updated" if "date_updated" in columns else "NULL")
        select_columns.append("NULL")
    else:
        select_columns = [column if column in columns else "NULL" for column in _PROFILE_COLUMNS]

    started_here = not conn.in_transaction
    if started_here:
        conn.execute("BEGIN")
    try:
        conn.execute(f"CREATE TABLE profile_migrated ({_PROFILE_TABLE_BODY})")
        conn.execute(
            f"INSERT INTO profile_migrated ({', '.join(_PROFILE_COLUMNS)}) "
            f"SELECT {', '.join(select_columns)} FROM profile"
        )
        conn.execute("DROP TABLE profile")
        conn.execute("ALTER TABLE profile_migrated RENAME TO profile")
        if started_here:
            conn.execute("COMMIT")
    except Exception:
        if started_here:
            conn.execute("ROLLBACK")
        raise


def _recover_profile_rebuild(conn: sqlite3.Connection) -> None:
    """Adopt a half-finished profile rebuild from a previously interrupted run."""
    leftover = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile_migrated'"
    ).fetchone()
    if leftover is None:
        return
    # CREATE_PROFILE_TABLE may have recreated an empty `profile` first; the
    # rebuilt table always matches the current schema, so its rows win. The
    # explicit INSERT keeps missing columns (e.g. user_id) at their defaults.
    conn.execute(
        "INSERT OR IGNORE INTO profile (id, name, full_name, location, phone, email, "
        "linkedin_url, github_url, summary, work_history, education, skills, date_updated) "
        "SELECT id, name, full_name, location, phone, email, "
        "linkedin_url, github_url, summary, work_history, education, skills, date_updated "
        "FROM profile_migrated"
    )
    conn.execute("DROP TABLE profile_migrated")


def _backfill_events(conn: sqlite3.Connection) -> None:
    """Seed events from stored stage dates for jobs created before logging existed."""
    rows = conn.execute(
        "SELECT id, date_applied, date_interview, date_offer, date_rejected FROM jobs"
    ).fetchall()
    for job in rows:
        stamps = [
            (job["date_applied"], "applied"),
            (job["date_interview"], "interview"),
            (job["date_offer"], "offer"),
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
    """Create the tables if needed and apply schema migrations."""
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
        conn.execute(CREATE_USERS_TABLE)
        _migrate(conn)
        _migrate_jobs_uniqueness(conn)
        _migrate_tags_uniqueness(conn)
        _migrate_profile(conn)
        # Drop the legacy global fingerprint index; uniqueness is per-user now.
        conn.execute("DROP INDEX IF EXISTS idx_jobs_fingerprint")
        for index in OWNERSHIP_INDEXES:
            conn.execute(index)
        conn.execute(CREATE_EVENTS_INDEX)
        conn.execute(CREATE_JOB_TAGS_INDEX)
        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")
        if not events_existed:
            _backfill_events(conn)
        conn.commit()


def _canonical_url_exists(
    conn: sqlite3.Connection, url: str, exclude_id: int, user_id: int | None
) -> bool:
    cleaned = clean_url(url)
    if user_id is None:
        rows = conn.execute("SELECT job_url FROM jobs WHERE id != ?", (exclude_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT job_url FROM jobs WHERE id != ? AND user_id = ?", (exclude_id, user_id)
        ).fetchall()
    for row in rows:
        if clean_url(row["job_url"]) == cleaned:
            return True
    return False


def check_duplicate(url: str, *, exclude_id: int | None = None, user_id: int | None = None) -> bool:
    """Return True when another job already uses the same canonical URL."""
    exclude = exclude_id if exclude_id is not None else -1
    with closing(get_connection()) as conn:
        return _canonical_url_exists(conn, url, exclude, user_id)


def find_similar(
    company: str | None,
    title: str | None,
    description: str | None,
    *,
    exclude_id: int | None = None,
    limit: int = SIMILAR_LIMIT,
    user_id: int | None = None,
) -> list[Job]:
    """Return jobs that look like the same role on another link (advisory only)."""
    if not company and not description:
        return []
    exclude = exclude_id if exclude_id is not None else -1
    target_role = role_key(company, title) if company and title else None
    matches: list[Job] = []
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE id != ? ORDER BY id DESC", (exclude,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE id != ? AND user_id = ? ORDER BY id DESC",
                (exclude, user_id),
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
    deadline: str | None = None,
    user_id: int | None = None,
) -> int | None:
    """Insert a job listing; return the new id, or None when the URL/fingerprint exists."""
    fingerprint = generate_fingerprint(url, company, title)
    now = _now()
    try:
        with closing(get_connection()) as conn:
            cursor = conn.execute(
                "INSERT INTO jobs "
                "(job_url, title, company, location, description, date_added, fingerprint, "
                "salary_min, salary_max, salary_currency, salary_period, salary_note, deadline, "
                "user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    deadline,
                    user_id,
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


def get_job(job_id: int, *, user_id: int | None = None) -> Job | None:
    """Return a single job row by id, or None when it does not exist or is not visible."""
    with closing(get_connection()) as conn:
        if user_id is None:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
            ).fetchone()
    return cast(Job, dict(row)) if row is not None else None


def get_events(job_id: int) -> list[Event]:
    """Return a job's logged status changes in chronological order."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    return [cast(Event, dict(row)) for row in rows]


def job_ids_with_status_event(status: JobStatus, *, user_id: int | None = None) -> set[int]:
    """Return the ids of every job whose event log contains this status."""
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT DISTINCT job_id FROM events WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT e.job_id FROM events e "
                "JOIN jobs j ON j.id = e.job_id "
                "WHERE e.status = ? AND j.user_id = ?",
                (status, user_id),
            ).fetchall()
    return {int(row[0]) for row in rows}


_FLAG_COLUMNS: Final[tuple[str, ...]] = ("applied", "interview", "offer", "rejected", "outdated")
_DATE_COLUMNS: Final[tuple[str, ...]] = (
    "date_applied",
    "date_interview",
    "date_offer",
    "date_rejected",
    "date_outdated",
)

_STATUS_SPEC: Final[dict[JobStatus, tuple[str, str, tuple[str, ...]]]] = {
    # status: (flag column, own date column, date columns carried over)
    "applied": ("applied", "date_applied", ("date_applied",)),
    "interview": ("interview", "date_interview", ("date_applied", "date_interview")),
    "offer": ("offer", "date_offer", ("date_applied", "date_interview", "date_offer")),
    "rejected": (
        "rejected",
        "date_rejected",
        ("date_applied", "date_interview", "date_rejected"),
    ),
    "outdated": ("outdated", "date_outdated", ("date_applied", "date_outdated")),
}

_STATUS_FLAG_COLUMNS: Final[dict[JobStatus, str]] = {
    status: spec[0] for status, spec in _STATUS_SPEC.items()
}


def set_job_status(job_id: int, status: JobStatus, *, user_id: int | None = None) -> None:
    """Move a job to a pipeline stage, stamping its date and preserving earlier ones."""
    with closing(get_connection()) as conn:
        if user_id is None:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
            ).fetchone()
        if row is None:
            return

        previous = job_status(cast(Job, dict(row)))
        now = _now()
        if status == "unapplied":
            flags = dict.fromkeys(_FLAG_COLUMNS, 0)
            dates: dict[str, str | None] = dict.fromkeys(_DATE_COLUMNS, None)
        else:
            flag_column, date_column, kept = _STATUS_SPEC[status]
            flags = dict.fromkeys(_FLAG_COLUMNS, 0)
            flags[flag_column] = 1
            dates = {column: row[column] if column in kept else None for column in _DATE_COLUMNS}
            if status == "outdated":
                # Parking must never invent an application date.
                dates["date_applied"] = row["date_applied"]
            else:
                dates["date_applied"] = row["date_applied"] or now
            if date_column != "date_applied":
                dates[date_column] = now

        conn.execute(
            "UPDATE jobs SET applied = ?, interview = ?, offer = ?, rejected = ?, outdated = ?, "
            "date_applied = ?, date_interview = ?, date_offer = ?, date_rejected = ?, "
            "date_outdated = ? WHERE id = ?",
            (
                flags["applied"],
                flags["interview"],
                flags["offer"],
                flags["rejected"],
                flags["outdated"],
                dates["date_applied"],
                dates["date_interview"],
                dates["date_offer"],
                dates["date_rejected"],
                dates["date_outdated"],
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
    "offer": "offer_comment",
    "rejected": "rejected_comment",
}

COMMENT_STAGES: Final[tuple[JobFlag, ...]] = tuple(_COMMENT_COLUMNS)
"""The stages that store a free-text comment."""


def set_job_comment(
    job_id: int, stage: JobFlag, comment: str, *, user_id: int | None = None
) -> None:
    """Store the free-text comment for a pipeline stage."""
    column = _COMMENT_COLUMNS[stage]
    with closing(get_connection()) as conn:
        # `column` is a fixed internal name from _COMMENT_COLUMNS, never user input.
        if user_id is None:
            conn.execute(f"UPDATE jobs SET {column} = ? WHERE id = ?", (comment, job_id))
        else:
            conn.execute(
                f"UPDATE jobs SET {column} = ? WHERE id = ? AND user_id = ?",
                (comment, job_id, user_id),
            )
        conn.commit()


def update_job_comments(
    job_id: int,
    applied: str,
    interview: str,
    rejected: str,
    *,
    offer: str = "",
    user_id: int | None = None,
) -> None:
    """Replace every stage comment at once."""
    with closing(get_connection()) as conn:
        if user_id is None:
            conn.execute(
                "UPDATE jobs SET applied_comment = ?, interview_comment = ?, "
                "offer_comment = ?, rejected_comment = ? WHERE id = ?",
                (applied, interview, offer, rejected, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET applied_comment = ?, interview_comment = ?, "
                "offer_comment = ?, rejected_comment = ? WHERE id = ? AND user_id = ?",
                (applied, interview, offer, rejected, job_id, user_id),
            )
        conn.commit()


def set_job_profile(
    job_id: int, profile_id: int, profile_name: str, *, user_id: int | None = None
) -> None:
    """Record which profile (CV) was used for a job application."""
    with closing(get_connection()) as conn:
        if user_id is None:
            conn.execute(
                "UPDATE jobs SET profile_id = ?, profile_name = ? WHERE id = ?",
                (profile_id, profile_name, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET profile_id = ?, profile_name = ? WHERE id = ? AND user_id = ?",
                (profile_id, profile_name, job_id, user_id),
            )
        conn.commit()


def get_jobs(status: JobStatus | None = None, *, user_id: int | None = None) -> list[Job]:
    """Return jobs ordered by id descending, optionally filtered by pipeline status."""
    conditions: list[str] = []
    params: list[object] = []
    if status == "unapplied":
        conditions.append(
            "applied = 0 AND interview = 0 AND offer = 0 AND rejected = 0 AND outdated = 0"
        )
    elif status == "applied":
        conditions.append("applied = 1")
    elif status == "interview":
        conditions.append("interview = 1")
    elif status == "offer":
        conditions.append("offer = 1")
    elif status == "rejected":
        conditions.append("rejected = 1")
    elif status == "outdated":
        conditions.append("outdated = 1")
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    query = "SELECT * FROM jobs"
    if conditions:
        query += " WHERE " + " AND ".join(f"({c})" for c in conditions)
    query += " ORDER BY id DESC"

    with closing(get_connection()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [cast(Job, dict(row)) for row in rows]


def job_counts(*, user_id: int | None = None) -> dict[str, int]:
    """Return position totals per pipeline stage (plus 'total')."""
    totals = ", ".join(f"COALESCE(SUM({column}), 0) AS {column}" for column in _FLAG_COLUMNS)
    query = f"SELECT COUNT(*) AS total, {totals} FROM jobs"
    params: tuple[object, ...] = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        params = (user_id,)
    with closing(get_connection()) as conn:
        row = conn.execute(query, params).fetchone()
    total = int(row["total"])
    counts = {column: int(row[column]) for column in _FLAG_COLUMNS}
    return {
        "total": total,
        "unapplied": total - sum(counts.values()),
        **{status: counts[column] for status, column in _STATUS_FLAG_COLUMNS.items()},
    }


def applications_by_month(*, user_id: int | None = None) -> list[tuple[str, int]]:
    """Return application counts per calendar month (YYYY-MM), ascending."""
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT substr(date_applied, 1, 7) AS month, COUNT(*) AS count "
                "FROM jobs WHERE date_applied IS NOT NULL GROUP BY month ORDER BY month"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT substr(date_applied, 1, 7) AS month, COUNT(*) AS count "
                "FROM jobs WHERE date_applied IS NOT NULL AND user_id = ? "
                "GROUP BY month ORDER BY month",
                (user_id,),
            ).fetchall()
    return [(str(row["month"]), int(row["count"])) for row in rows]


def pipeline_funnel(*, user_id: int | None = None) -> dict[str, int]:
    """Return monotonic pipeline counts for the response funnel.

    Stages are counted from the event log so they survive unmarking a
    status (which clears ``date_applied``): ``applied`` counts jobs that
    ever reached a real stage (applied, interview, offer, or rejected —
    parking in outdated does not count), ``response`` jobs that reached an
    interview, an offer, or a rejection, ``interview`` jobs that reached an
    interview or an offer, and ``offer`` jobs that got one. The subset
    relation guarantees a non-increasing funnel.
    """
    with closing(get_connection()) as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT "
                "COUNT(DISTINCT CASE WHEN status IN ('applied', 'interview', 'offer', 'rejected') "
                "THEN job_id END) AS applied, "
                "COUNT(DISTINCT CASE WHEN status IN ('interview', 'offer', 'rejected') "
                "THEN job_id END) AS response, "
                "COUNT(DISTINCT CASE WHEN status IN ('interview', 'offer') "
                "THEN job_id END) AS interview, "
                "COUNT(DISTINCT CASE WHEN status = 'offer' THEN job_id END) AS offer "
                "FROM events"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT "
                "COUNT(DISTINCT CASE WHEN e.status IN ('applied', 'interview', 'offer', 'rejected') "
                "THEN e.job_id END) AS applied, "
                "COUNT(DISTINCT CASE WHEN e.status IN ('interview', 'offer', 'rejected') "
                "THEN e.job_id END) AS response, "
                "COUNT(DISTINCT CASE WHEN e.status IN ('interview', 'offer') "
                "THEN e.job_id END) AS interview, "
                "COUNT(DISTINCT CASE WHEN e.status = 'offer' THEN e.job_id END) AS offer "
                "FROM events e JOIN jobs j ON j.id = e.job_id WHERE j.user_id = ?",
                (user_id,),
            ).fetchone()
    applied = int(row["applied"])
    response = int(row["response"])
    interview = int(row["interview"])
    offer = int(row["offer"])
    return {
        "added": job_counts(user_id=user_id)["total"],
        "applied": applied,
        "response": response,
        "interview": interview,
        "offer": offer,
    }


def search_jobs(
    query: str,
    status: JobStatus | None = None,
    *,
    threshold: int = COMPANY_SEARCH_THRESHOLD,
    user_id: int | None = None,
) -> list[Job]:
    """Return jobs whose company fuzzy-matches the query, best match first."""
    text = (query or "").strip()
    jobs = get_jobs(status=status, user_id=user_id)
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


def delete_job(job_id: int, *, user_id: int | None = None) -> None:
    """Delete a single job listing by id."""
    with closing(get_connection()) as conn:
        if user_id is None:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        else:
            conn.execute("DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
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


def list_tags(*, user_id: int | None = None) -> list[str]:
    """Return every tag name, alphabetically, case-insensitively."""
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
        else:
            rows = conn.execute(
                "SELECT name FROM tags WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                (user_id,),
            ).fetchall()
    return [row["name"] for row in rows]


def tag_counts(*, user_id: int | None = None) -> dict[str, int]:
    """Return tag name -> number of assigned positions, alphabetically."""
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT t.name, COUNT(jt.job_id) AS uses FROM tags t "
                "LEFT JOIN job_tags jt ON jt.tag_id = t.id GROUP BY t.id "
                "ORDER BY t.name COLLATE NOCASE"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT t.name, COUNT(jt.job_id) AS uses FROM tags t "
                "LEFT JOIN job_tags jt ON jt.tag_id = t.id WHERE t.user_id = ? "
                "GROUP BY t.id ORDER BY t.name COLLATE NOCASE",
                (user_id,),
            ).fetchall()
    return {row["name"]: int(row["uses"]) for row in rows}


def find_tag(name: str, *, user_id: int | None = None) -> int | None:
    """Return the id of a tag by name (case-insensitive), or None when unknown."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    with closing(get_connection()) as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (cleaned,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND user_id = ?",
                (cleaned, user_id),
            ).fetchone()
    return int(row["id"]) if row is not None else None


def create_tag(name: str, *, user_id: int | None = None) -> int | None:
    """Create a tag; return its id, or None when empty or already present."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    with closing(get_connection()) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO tags (name, user_id) VALUES (?, ?)", (cleaned, user_id)
            )
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


def _ensure_tag(conn: sqlite3.Connection, name: str, *, user_id: int | None = None) -> int | None:
    """Return the tag id for ``name``, creating it when missing (case-insensitive)."""
    cleaned = name.strip()
    if not cleaned:
        return None
    if user_id is None:
        row = conn.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (cleaned,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND user_id = ?",
            (cleaned, user_id),
        ).fetchone()
    if row is not None:
        return int(row["id"])
    cursor = conn.execute("INSERT INTO tags (name, user_id) VALUES (?, ?)", (cleaned, user_id))
    return cast("int", cursor.lastrowid)


def set_job_tags(
    job_id: int,
    names: list[str] | tuple[str, ...],
    *,
    user_id: int | None = None,
) -> None:
    """Replace a job's tags, creating any unknown names."""
    with closing(get_connection()) as conn:
        wanted: set[int] = set()
        for name in names:
            tag_id = _ensure_tag(conn, name, user_id=user_id)
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
    deadline: str | None = None,
    user_id: int | None = None,
) -> bool:
    """Update a job's editable fields; return False when the URL or fingerprint collides."""
    fingerprint = generate_fingerprint(url, company, title)
    try:
        with closing(get_connection()) as conn:
            if user_id is None:
                conn.execute(
                    "UPDATE jobs SET job_url = ?, title = ?, company = ?, location = ?, "
                    "description = ?, fingerprint = ?, salary_min = ?, salary_max = ?, "
                    "salary_currency = ?, salary_period = ?, salary_note = ?, deadline = ? "
                    "WHERE id = ?",
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
                        deadline,
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET job_url = ?, title = ?, company = ?, location = ?, "
                    "description = ?, fingerprint = ?, salary_min = ?, salary_max = ?, "
                    "salary_currency = ?, salary_period = ?, salary_note = ?, deadline = ? "
                    "WHERE id = ? AND user_id = ?",
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
                        deadline,
                        job_id,
                        user_id,
                    ),
                )
            conn.commit()
    except sqlite3.IntegrityError:
        return False
    return True


def _setting_key(key: str, user_id: int | None) -> str:
    """Prefix ``key`` with the owner when scoped, leaving operator keys global."""
    return f"user:{user_id}:{key}" if user_id is not None else key


def get_setting(key: str, default: str | None = None, *, user_id: int | None = None) -> str | None:
    """Return a stored preference value, or the default when unset."""
    effective = _setting_key(key, user_id)
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (effective,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(key: str, value: str, *, user_id: int | None = None) -> None:
    """Store a preference value, replacing any existing value for the key."""
    effective = _setting_key(key, user_id)
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (effective, value),
        )
        conn.commit()


def _row_to_profile(row: sqlite3.Row) -> Profile:
    return {
        "id": int(row["id"]),
        "name": row["name"] or "",
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
        "user_id": row["user_id"],
    }


def _empty_profile() -> Profile:
    return {
        "id": 0,
        "name": "",
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
        "user_id": None,
    }


def save_profile(data: Profile) -> None:
    """Replace the profile row with the given id, keeping its name."""
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile "
            "(id, name, full_name, location, phone, email, linkedin_url, github_url, "
            "summary, work_history, education, skills, date_updated, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)",
            (
                data["id"],
                data["name"],
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
                data["user_id"],
            ),
        )
        conn.commit()


def get_profile(profile_id: int | None = None, *, user_id: int | None = None) -> Profile:
    """Return one profile by id (or the first visible profile when omitted), else empty."""
    with closing(get_connection()) as conn:
        if profile_id is None:
            if user_id is None:
                row = conn.execute("SELECT * FROM profile ORDER BY id LIMIT 1").fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM profile WHERE user_id = ? ORDER BY id LIMIT 1", (user_id,)
                ).fetchone()
        else:
            row = conn.execute("SELECT * FROM profile WHERE id = ?", (profile_id,)).fetchone()
    return _row_to_profile(row) if row is not None else _empty_profile()


def list_profiles(*, user_id: int | None = None) -> list[Profile]:
    """Return all profiles ordered by name for the selection dropdown."""
    with closing(get_connection()) as conn:
        if user_id is None:
            rows = conn.execute("SELECT * FROM profile ORDER BY name COLLATE NOCASE, id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM profile WHERE user_id = ? ORDER BY name COLLATE NOCASE, id",
                (user_id,),
            ).fetchall()
    return [_row_to_profile(row) for row in rows]


def create_profile(
    name: str,
    copy_from: int | None = None,
    *,
    copy_cv: bool = False,
    user_id: int | None = None,
) -> int | None:
    """Create a named profile, optionally copying contact fields from another.

    ``copy_cv`` additionally copies the Summary, Work History, Education and
    Skills sections. Returns the new id, or None when the name is empty or
    already taken.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    contact_columns = ("full_name", "location", "phone", "email", "linkedin_url", "github_url")
    cv_columns = ("summary", "work_history", "education", "skills")
    columns = contact_columns + (cv_columns if copy_cv else ())
    with closing(get_connection()) as conn:
        copied: tuple[str, ...] | None = None
        if copy_from is not None:
            row = conn.execute("SELECT * FROM profile WHERE id = ?", (copy_from,)).fetchone()
            if row is not None:
                copied = tuple((row[column] or "") for column in columns)
        try:
            if copied is None:
                cursor = conn.execute(
                    "INSERT INTO profile (name, user_id) VALUES (?, ?)", (cleaned, user_id)
                )
            else:
                placeholders = ", ".join("?" for _ in columns)
                cursor = conn.execute(
                    f"INSERT INTO profile (name, {', '.join(columns)}, user_id) "
                    f"VALUES (?, {placeholders}, ?)",
                    (cleaned, *copied, user_id),
                )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return cast("int", cursor.lastrowid)


def delete_profile(profile_id: int, *, user_id: int | None = None) -> None:
    """Delete one profile row; unknown ids are ignored."""
    with closing(get_connection()) as conn:
        if user_id is None:
            conn.execute("DELETE FROM profile WHERE id = ?", (profile_id,))
        else:
            conn.execute("DELETE FROM profile WHERE id = ? AND user_id = ?", (profile_id, user_id))
        conn.commit()


def create_user(email: str, password_hash: str, *, is_admin: bool = False) -> int | None:
    """Create an account; return the new id, or None when the email is already taken."""
    cleaned = (email or "").strip()
    if not cleaned:
        return None
    try:
        with closing(get_connection()) as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
                (cleaned, password_hash, 1 if is_admin else 0),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return None
    return cast("int", cursor.lastrowid)


def get_user_by_email(email: str) -> UserRow | None:
    """Return the account row for an email (case-insensitive), or None."""
    cleaned = (email or "").strip()
    if not cleaned:
        return None
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (cleaned,)).fetchone()
    return cast(UserRow, dict(row)) if row is not None else None


def get_user(user_id: int) -> UserRow | None:
    """Return an account row by id, or None."""
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return cast(UserRow, dict(row)) if row is not None else None


def set_user_password(user_id: int, password_hash: str) -> bool:
    """Replace an account's password hash; False when the id is unknown."""
    with closing(get_connection()) as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def adopt_orphan_rows(user_id: int) -> dict[str, int]:
    """Assign every pre-auth row (user_id IS NULL) to ``user_id``; return counts."""
    counts: dict[str, int] = {}
    with closing(get_connection()) as conn:
        for table in ("jobs", "profile", "tags"):
            cursor = conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            counts[table] = cursor.rowcount
        conn.commit()
    return counts
