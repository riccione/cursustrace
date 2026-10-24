"""JSearch (OpenWeb Ninja) client, API key storage, usage counter, and dedupe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from cursustrace import config, db
from cursustrace.applicability import check_applicability
from cursustrace.errors import JSearchError
from cursustrace.utils import clean_url, generate_fingerprint
from cursustrace.validation import normalize_deadline

API_URL = "https://api.openwebninja.com/jsearch/search-v2"
MONTHLY_LIMIT = 200
RESULTS_PER_PAGE = 10
REQUEST_TIMEOUT = 15.0
KEY_FILENAME = ".jsearch-key"
USAGE_KEY_PREFIX = "jsearch_requests"
DATE_POSTED_OPTIONS = ("all", "today", "3days", "week", "month")
SALARY_PERIODS = {"YEAR": "year", "MONTH": "month"}


@dataclass(frozen=True)
class JSearchJob:
    """A normalised job posting returned by JSearch."""

    url: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_note: str | None = None
    deadline: str | None = None
    posted_at: str | None = None
    publisher: str | None = None
    remote: bool | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class JSearchPage:
    """One search request (or a cursor run) with its result count."""

    jobs: tuple[JSearchJob, ...]
    cursor: str | None
    requests: int


@dataclass
class KnownIndex:
    """Tracked positions keyed by canonical URL and fingerprint."""

    by_url: dict[str, db.Job]
    by_fingerprint: dict[str, db.Job]


@dataclass(frozen=True)
class KnownJob:
    """A JSearch result that is already tracked, with the stored position's details."""

    job: JSearchJob
    id: int
    title: str | None
    status: str


def key_path() -> Path:
    """Return the saved-key path, next to the database."""
    return db.DEFAULT_DB_PATH.parent / KEY_FILENAME


def api_key(settings: config.Settings | None = None) -> tuple[str, str] | None:
    """Return the configured key and its source, or None when unset."""
    env_value = os.environ.get(config.ENV_JSEARCH_API_KEY, "").strip()
    if env_value:
        return env_value, "environment"
    current = settings if settings is not None else config.load_settings()
    if current.jsearch_api_key:
        return current.jsearch_api_key, "config file"
    try:
        stored = key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return (stored, "saved key") if stored else None


def save_api_key(key: str) -> None:
    """Store a key in the 0600 file next to the database."""
    cleaned = key.strip()
    if not cleaned:
        raise JSearchError("The API key cannot be empty.")
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(cleaned)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def remove_api_key() -> None:
    """Delete the saved key file when present."""
    key_path().unlink(missing_ok=True)


def mask_key(key: str) -> str:
    """Hide all but the last four characters of a key."""
    return f"••••{key[-4:]}" if len(key) > 4 else "••••"


def month_key(now: date | None = None) -> str:
    """Return the usage-counter key for the month containing ``now``."""
    current = now if now is not None else date.today()
    return f"{USAGE_KEY_PREFIX}:{current:%Y-%m}"


def requests_this_month() -> int:
    """Return how many JSearch requests CursusTrace made this month."""
    value = db.get_setting(month_key(), "0")
    try:
        return int(value or 0)
    except ValueError:
        return 0


def record_request() -> int:
    """Count one successful request and return the new monthly total."""
    count = requests_this_month() + 1
    db.set_setting(month_key(), str(count))
    return count


def search(
    query: str,
    *,
    api_key: str,
    country: str | None = None,
    date_posted: str | None = None,
    remote_only: bool = False,
    cursor: str | None = None,
) -> JSearchPage:
    """Run one JSearch search request and normalise its jobs."""
    term = query.strip()
    if not term:
        raise JSearchError("Enter a search query.")
    if date_posted is not None and date_posted not in DATE_POSTED_OPTIONS:
        raise JSearchError(f"Unknown date filter: {date_posted}")
    params: dict[str, str | int] = {"query": term, "num_pages": 1}
    if country and country.strip():
        params["country"] = country.strip().lower()
    if date_posted and date_posted != "all":
        params["date_posted"] = date_posted
    if remote_only:
        params["work_from_home"] = "true"
    if cursor:
        params["cursor"] = cursor
    try:
        response = httpx.get(
            API_URL,
            params=params,
            headers={"x-api-key": api_key},
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise JSearchError(f"Could not reach JSearch: {exc}") from exc
    _raise_for_status(response.status_code)
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise JSearchError("JSearch returned an unreadable response.") from exc
    if not isinstance(payload, dict):
        raise JSearchError("JSearch returned an unexpected response.")
    if payload.get("status") != "OK":
        raise JSearchError(_error_message(payload))
    record_request()
    data = payload.get("data")
    raw_jobs: list[object]
    next_cursor: str | None = None
    if isinstance(data, dict):
        jobs_value = data.get("jobs")
        raw_jobs = jobs_value if isinstance(jobs_value, list) else []
        next_cursor = _text(data.get("cursor"))
    elif isinstance(data, list):
        raw_jobs = data
    else:
        raw_jobs = []
    jobs = tuple(job for raw in raw_jobs if (job := _job_from_raw(raw)) is not None)
    return JSearchPage(jobs=jobs, cursor=next_cursor, requests=1)


def search_many(
    query: str,
    *,
    api_key: str,
    total: int,
    country: str | None = None,
    date_posted: str | None = None,
    remote_only: bool = False,
) -> JSearchPage:
    """Fetch up to ``total`` results, one request per page, following the cursor."""
    wanted = max(1, total)
    jobs: list[JSearchJob] = []
    cursor: str | None = None
    requests = 0
    while len(jobs) < wanted:
        page = search(
            query,
            api_key=api_key,
            country=country,
            date_posted=date_posted,
            remote_only=remote_only,
            cursor=cursor,
        )
        requests += page.requests
        jobs.extend(page.jobs)
        cursor = page.cursor
        if cursor is None or not page.jobs:
            break
    return JSearchPage(jobs=tuple(jobs[:wanted]), cursor=cursor, requests=requests)


def known_index(user_id: int | None = None) -> KnownIndex:
    """Index the tracked positions for fast duplicate checks."""
    jobs = db.get_jobs(user_id=user_id)
    return KnownIndex(
        by_url={clean_url(job["job_url"]): job for job in jobs},
        by_fingerprint={job["fingerprint"]: job for job in jobs if job["fingerprint"]},
    )


def partition(
    jobs: list[JSearchJob] | tuple[JSearchJob, ...],
    *,
    index: KnownIndex | None = None,
    user_id: int | None = None,
) -> tuple[tuple[JSearchJob, ...], tuple[KnownJob, ...]]:
    """Split results into new positions and ones already tracked.

    Duplicates are detected by canonical URL and by fingerprint, including
    repeats within the same batch.
    """
    known = index if index is not None else known_index(user_id)
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()
    new: list[JSearchJob] = []
    tracked: list[KnownJob] = []
    for job in jobs:
        canonical = clean_url(job.url)
        fingerprint = generate_fingerprint(job.url, job.company, job.title)
        existing = known.by_url.get(canonical) or known.by_fingerprint.get(fingerprint)
        if existing is not None:
            tracked.append(
                KnownJob(
                    job=job,
                    id=existing["id"],
                    title=existing["title"],
                    status=db.job_status(existing),
                )
            )
            continue
        if canonical in seen_urls or fingerprint in seen_fingerprints:
            continue
        seen_urls.add(canonical)
        seen_fingerprints.add(fingerprint)
        new.append(job)
    return tuple(new), tuple(tracked)


def add_jobs(
    jobs: list[JSearchJob] | tuple[JSearchJob, ...], *, user_id: int | None = None
) -> tuple[int, int, list[list[db.Job]]]:
    """Insert results directly, skipping duplicates; return saved, skipped, similar."""
    saved = duplicates = 0
    similar: list[list[db.Job]] = []
    for job in jobs:
        if db.check_duplicate(job.url, user_id=user_id):
            duplicates += 1
            continue
        job_id = db.add_job(
            job.url,
            job.title,
            job.company,
            job.location,
            job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            salary_note=job.salary_note,
            deadline=job.deadline,
            user_id=user_id,
        )
        if job_id is None:
            duplicates += 1
            continue
        saved += 1
        matches = db.find_similar(
            job.company, job.title, job.description, exclude_id=job_id, user_id=user_id
        )
        if matches:
            similar.append(matches)
    return saved, duplicates, similar


def _raise_for_status(status_code: int) -> None:
    if status_code in (401, 403):
        raise JSearchError("JSearch rejected the API key — check or replace it.")
    if status_code == 429:
        raise JSearchError("JSearch monthly request limit reached (200 on the free plan).")
    if status_code >= 400:
        raise JSearchError(f"JSearch request failed (HTTP {status_code}).")


def _error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = _text(error.get("message")) or _text(error.get("code"))
        if message:
            return f"JSearch error: {message}"
    elif isinstance(error, str) and error.strip():
        return f"JSearch error: {error.strip()}"
    message = _text(payload.get("message"))
    return f"JSearch error: {message}" if message else "JSearch returned an error."


def _job_from_raw(raw: object) -> JSearchJob | None:
    if not isinstance(raw, dict):
        return None
    url = _apply_link(raw)
    if url is None:
        return None
    location = _text(raw.get("job_location")) or _join_location(raw)
    description = _text(raw.get("job_description"))
    salary_min, salary_max, currency, period, note = _salary(raw)
    deadline_raw = _text(raw.get("job_offer_expiration_datetime_utc"))
    remote_value = raw.get("job_is_remote")
    return JSearchJob(
        url=url,
        title=_text(raw.get("job_title")),
        company=_text(raw.get("employer_name")),
        location=location,
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        salary_period=period,
        salary_note=note,
        deadline=normalize_deadline(deadline_raw[:10]) if deadline_raw else None,
        posted_at=_text(raw.get("job_posted_at_datetime_utc")),
        publisher=_text(raw.get("job_publisher")),
        remote=remote_value if isinstance(remote_value, bool) else None,
        flags=tuple(check_applicability(location, description)),
    )


def _apply_link(raw: dict[str, object]) -> str | None:
    link = _text(raw.get("job_apply_link"))
    if link and not _is_google(link):
        return link
    options = raw.get("apply_options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            candidate = _text(option.get("apply_link"))
            if candidate and not _is_google(candidate):
                return candidate
    return None


def _is_google(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "google.com" or host.endswith(".google.com")


def _join_location(raw: dict[str, object]) -> str | None:
    parts = [
        _text(raw.get("job_city")),
        _text(raw.get("job_state")),
        _text(raw.get("job_country")),
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or None


def _salary(
    raw: dict[str, object],
) -> tuple[int | None, int | None, str | None, str | None, str | None]:
    low = _as_int(raw.get("job_min_salary"))
    high = _as_int(raw.get("job_max_salary"))
    if low is None and high is None:
        return None, None, None, None, None
    currency = _text(raw.get("job_salary_currency"))
    period = _text(raw.get("job_salary_period"))
    mapped = SALARY_PERIODS.get((period or "").upper())
    if mapped is None:
        return None, None, None, None, _salary_note(low, high, currency, period)
    return low, high, currency, mapped, None


def _salary_note(
    low: int | None, high: int | None, currency: str | None, period: str | None
) -> str:
    amounts = [value for value in (low, high) if value is not None]
    amount = f"{min(amounts):,} – {max(amounts):,}" if len(amounts) > 1 else f"{amounts[0]:,}"
    prefix = f"{currency} " if currency else ""
    suffix = f" / {period.lower()}" if period else ""
    return f"{prefix}{amount}{suffix}"


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
