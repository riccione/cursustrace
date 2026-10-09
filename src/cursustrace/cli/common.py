"""Shared CLI helpers: statuses, ingestion, filtering, and reporting."""

from __future__ import annotations

import json as json_module
import logging
from typing import cast

import click

from cursustrace import db, salary, scraper
from cursustrace.errors import ScrapeError
from cursustrace.validation import normalize_deadline, required_error, validate_url

STATUSES = ["unapplied", "applied", "interview", "rejected", "outdated"]
SORT_ORDERS = ["newest", "oldest", "company", "company_desc", "status"]
SIMILAR_NOTICE_KEY = "similar_notice"

logger = logging.getLogger(__name__)


def _similar_enabled() -> bool:
    return db.get_setting(SIMILAR_NOTICE_KEY, "on") != "off"


def _similar_payload(job: db.Job) -> dict[str, object]:
    return {
        "id": job["id"],
        "title": job["title"],
        "company": job["company"],
        "url": job["job_url"],
        "status": db.job_status(job),
    }


def _check_fields(
    url: str, title: str | None, company: str | None, description: str | None
) -> str | None:
    for message in (
        validate_url(url),
        required_error("Title", title),
        required_error("Company", company),
        required_error("Description", description),
    ):
        if message is not None:
            return message
    return None


def _ingest(
    url: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    status: str = "unapplied",
    *,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    salary_note: str | None = None,
    deadline: str | None = None,
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    if db.check_duplicate(url):
        logger.info("Skipped duplicate: %s", url)
        return {"status": "duplicate", "url": url}

    job_id = db.add_job(
        url,
        title,
        company,
        location or None,
        description,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        salary_note=salary_note,
        deadline=deadline,
    )
    if job_id is None:
        logger.info("Skipped duplicate: %s", url)
        return {"status": "duplicate", "url": url}

    if status != "unapplied":
        db.set_job_status(job_id, cast("db.JobStatus", status))
    if tags:
        db.set_job_tags(job_id, list(tags))
    result: dict[str, object] = {"status": "added", "id": job_id, "url": url}
    if _similar_enabled():
        matches = db.find_similar(company, title, description, exclude_id=job_id)
        if matches:
            result["similar"] = [_similar_payload(job) for job in matches]
    return result


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_tags(value: object) -> tuple[str, ...]:
    """Normalize an import ``tags`` field (list or ';'/','-separated string)."""
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    elif isinstance(value, list):
        parts = [str(part) for part in value]
    else:
        return ()
    return tuple(name.strip() for name in parts if name.strip())


def _ingest_item(
    item: object, index: int
) -> tuple[str, dict[str, str] | None, list[dict[str, object]]]:
    """Validate, scrape if needed, and ingest one imported JSON item."""
    if not isinstance(item, dict):
        return ("error", {"item": str(index), "error": "expected an object"}, [])

    url = str(item.get("url") or item.get("job_url") or "").strip()
    url_error = validate_url(url)
    if url_error is not None:
        return ("error", {"item": str(index), "error": url_error}, [])

    title = item.get("title")
    company = item.get("company")
    location = item.get("location")
    description = item.get("description")
    status = _optional_str(item.get("status")) or "unapplied"
    if status not in STATUSES:
        return ("error", {"item": str(index), "error": f"invalid status: {status}"}, [])
    deadline = _optional_str(item.get("deadline"))
    if deadline is not None:
        normalized = normalize_deadline(deadline)
        if normalized is None:
            return ("error", {"item": str(index), "error": f"invalid deadline: {deadline}"}, [])
        deadline = normalized
    if not (title and company and description):
        try:
            scraped = scraper.scrape_job(url)
        except ScrapeError as exc:
            return ("error", {"url": url, "error": str(exc)}, [])
        title = title or scraped["title"]
        company = company or scraped["company"]
        location = location or scraped["location"]
        description = description or scraped["description"]
        deadline = deadline or scraped["deadline"]

    result = _ingest(
        url,
        title,
        company,
        location,
        description,
        status,
        salary_min=_optional_int(item.get("salary_min")),
        salary_max=_optional_int(item.get("salary_max")),
        salary_currency=_optional_str(item.get("salary_currency")),
        salary_period=_optional_str(item.get("salary_period")),
        salary_note=_optional_str(item.get("salary_note")),
        deadline=deadline,
        tags=_optional_tags(item.get("tags")),
    )
    similar = cast("list[dict[str, object]]", result.get("similar", []))
    if result["status"] == "added":
        _restore_comments(item, cast("int", result["id"]))
    return ("skipped" if result["status"] == "duplicate" else "added", None, similar)


def _restore_comments(item: dict[object, object], job_id: int) -> None:
    """Copy stage comments back onto a freshly imported position when present."""
    comments = {
        stage: _optional_str(item.get(f"{stage}_comment"))
        for stage in ("applied", "interview", "rejected")
    }
    if any(comment is not None for comment in comments.values()):
        db.update_job_comments(
            job_id,
            comments["applied"] or "",
            comments["interview"] or "",
            comments["rejected"] or "",
        )


def _report_summary(
    added: int,
    skipped: int,
    errors: list[dict[str, str]],
    as_json: bool,
    similar: list[dict[str, object]] | None = None,
) -> None:
    """Print the batch result and exit non-zero when any item errored."""
    similar = similar or []
    for error in errors:
        label = error.get("url", error.get("item", "?"))
        logger.error("Ingestion error (%s): %s", label, error["error"])
    for match in similar:
        logger.info("Added position looks similar to '%s' (%s)", match["title"], match["url"])
    if as_json:
        payload: dict[str, object] = {"added": added, "skipped": skipped, "errors": errors}
        if similar:
            payload["similar"] = similar
        click.echo(json_module.dumps(payload))
    else:
        for error in errors:
            label = error.get("url", error.get("item", "?"))
            click.echo(f"Error ({label}): {error['error']}", err=True)
        click.echo(f"Added {added}, skipped {skipped}, errors {len(errors)}.")
        for match in similar:
            click.echo(
                f"Note: added position looks similar to '{match['title']}' ({match['url']})",
                err=True,
            )
    if errors:
        raise click.exceptions.Exit(1)


def _filtered_jobs(
    status: str | None,
    search: str | None,
    min_salary: int | None,
    max_salary: int | None,
    salary_currency: str | None,
    tags: tuple[str, ...] = (),
) -> list[db.Job]:
    """Apply the shared status/company/salary/tag filters used by list and export."""
    jobs = db.search_jobs(search or "", status=cast("db.JobStatus | None", status))
    jobs = salary.filter_by_salary(
        jobs, min_annual=min_salary, max_annual=max_salary, currency=salary_currency
    )
    return db.filter_by_tags(jobs, tags)


def _resolve_delete(
    job_ids: tuple[int, ...],
    wanted_urls: list[str],
    status: str | None,
    search: str | None,
    min_salary: int | None,
    max_salary: int | None,
    salary_currency: str | None,
    tags: tuple[str, ...],
) -> tuple[list[db.Job], list[int], list[str]]:
    """Resolve delete selectors into sorted target jobs plus unmatched ids/URLs."""
    jobs = db.get_jobs()
    by_id = {job["id"]: job for job in jobs}
    by_url = {job["job_url"]: job for job in jobs}
    not_found_ids = [job_id for job_id in job_ids if job_id not in by_id]
    not_found_urls = [url for url in wanted_urls if url not in by_url]

    selected: dict[int, None] = {}
    for job_id in job_ids:
        if job_id in by_id:
            selected[job_id] = None
    for url in wanted_urls:
        job = by_url.get(url)
        if job is not None:
            selected[job["id"]] = None
    if any((status, search, min_salary, max_salary, salary_currency, tags)):
        for job in _filtered_jobs(status, search, min_salary, max_salary, salary_currency, tags):
            selected[job["id"]] = None
    return [by_id[job_id] for job_id in sorted(selected)], not_found_ids, not_found_urls
