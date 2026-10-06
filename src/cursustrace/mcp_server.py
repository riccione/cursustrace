"""MCP server exposing cursustrace to AI agents over stdio."""

from __future__ import annotations

from typing import cast

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cursustrace import applicability, db, discovery, salary, scraper
from cursustrace.cli import (
    SORT_ORDERS,
    STATUSES,
    _check_fields,
    _filtered_jobs,
    _ingest,
    _resolve_delete,
)
from cursustrace.errors import ScrapeError

mcp = MCPServer(
    "cursustrace",
    instructions=(
        "CursusTrace is a job-application tracker with positions, statuses "
        "(unapplied/applied/interview/rejected), tags, salaries, and events. "
        "The database path is relative to the working directory the server was "
        "started in. Adding never overwrites: known URLs are always reported as "
        "duplicates. delete_positions is preview-first: call it with confirm=false "
        "(the default) to see what would be removed, then repeat with confirm=true "
        "to execute. discover lists candidate links only; scan or "
        "discover(add=true) ingest them. Tags are case-insensitive and unknown "
        "tags are created on write. When adding, call scrape_position first and "
        "read the listing: the owner applies only to Serbia-based or remote-EU "
        "roles, never listings restricted to working in another country, and "
        "form fields should come from the scraped data. add_position, scan and "
        "discover(add=true) answer with status=flagged (or a flagged list) "
        "instead of ingesting when the location or description looks tied to a "
        "specific place; review the position and repeat with force=true when "
        "it fits."
    ),
)


def _validate_choice(value: str | None, allowed: tuple[str, ...] | list[str], name: str) -> None:
    if value is not None and value not in allowed:
        raise ToolError(f"{name} must be one of: {', '.join(allowed)}")


def _with_tags(jobs: list[db.Job]) -> list[dict[str, object]]:
    tags_by_id = db.tags_for_jobs([job["id"] for job in jobs])
    return [
        {**dict(job), "status": db.job_status(job), "tags": tags_by_id.get(job["id"], [])}
        for job in jobs
    ]


def _flagged_payload(url: str, flags: list[str], job: scraper.ScrapedJob) -> dict[str, object]:
    return {
        "url": url,
        "applicability": {"flags": flags},
        "position": {
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "description": job["description"],
        },
    }


def _position_or_error(job_id: int) -> db.Job:
    job = db.get_job(job_id)
    if job is None:
        raise ToolError(f"No position with id {job_id}")
    return job


@mcp.tool()
def list_positions(
    status: str | None = None,
    search: str | None = None,
    tags: list[str] | None = None,
    min_salary: int | None = None,
    max_salary: int | None = None,
    salary_currency: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, object]:
    """List tracked positions with optional filters, newest first by default.

    status is one of unapplied/applied/interview/rejected; tags matches any of
    the given tags; sort is one of newest/oldest/company/company_desc/status.
    Returns each position together with its tags.
    """
    db.init_db()
    _validate_choice(status, STATUSES, "status")
    _validate_choice(sort, SORT_ORDERS, "sort")
    _validate_choice(salary_currency, salary.CURRENCIES, "salary_currency")
    jobs = _filtered_jobs(
        status, search, min_salary, max_salary, salary_currency, tuple(tags or ())
    )
    if sort is not None:
        jobs = db.sort_jobs(jobs, cast("db.JobSort", sort))
    if offset:
        jobs = jobs[offset:]
    if limit is not None:
        jobs = jobs[:limit]
    payload = _with_tags(jobs)
    return {"positions": payload, "count": len(payload)}


@mcp.tool()
def get_position(job_id: int) -> dict[str, object]:
    """Fetch one position with its tags and status-change history."""
    db.init_db()
    job = _position_or_error(job_id)
    tags = db.tags_for_jobs([job_id]).get(job_id, [])
    events = [{**dict(event)} for event in db.get_events(job_id)]
    return {
        "position": {**dict(job), "status": db.job_status(job), "tags": tags},
        "events": events,
    }


@mcp.tool()
def get_stats() -> dict[str, object]:
    """Return per-status position counts and the salary summary."""
    db.init_db()
    return {
        "counts": dict(db.job_counts()),
        "salary": dict(salary.salary_summary(db.get_jobs())),
    }


@mcp.tool()
def list_tags() -> dict[str, object]:
    """List tags with their position counts."""
    db.init_db()
    return {"tags": db.tag_counts()}


@mcp.tool()
def scrape_position(url: str) -> dict[str, object]:
    """Scrape a job listing and return its fields plus applicability flags (read-only).

    Call this before add_position: read the returned description and judge
    whether the role fits — the tracker only wants Serbia-based or remote-EU
    positions, never listings restricted to working in another country. Then
    pass the scraped fields on to add_position so the stored form is filled
    from real page data instead of guesses.
    """
    cleaned_url = url.strip()
    try:
        job = scraper.scrape_job(cleaned_url)
    except ScrapeError as exc:
        raise ToolError(str(exc)) from exc
    flags = applicability.check_applicability(job["location"], job["description"])
    return {
        "url": cleaned_url,
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "description": job["description"],
        "applicability": {"flags": flags},
    }


@mcp.tool()
def add_position(
    url: str,
    title: str | None = None,
    company: str | None = None,
    description: str | None = None,
    location: str | None = None,
    status: str = "unapplied",
    tags: list[str] | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Add a position; missing title/company/description/location are scraped from the URL.

    Known URLs are reported as duplicates and never overwritten. When the
    fields being stored look tied to a specific country or work authorization,
    nothing is ingested: the response carries status=flagged with the reasons
    and the position fields for review — call again with force=true if the
    listing still fits a Serbia/remote-EU applicant. Explicitly passed fields
    override scraped values, so prefer passing what scrape_position returned.
    Returns the add result with status (added/duplicate/flagged) and, when
    added, the id and any similar positions worth checking.
    """
    db.init_db()
    _validate_choice(status, STATUSES, "status")
    _validate_choice(salary_currency, salary.CURRENCIES, "salary_currency")
    _validate_choice(salary_period, salary.PERIODS, "salary_period")
    if (salary_min is not None or salary_max is not None) and not (
        salary_currency and salary_period
    ):
        raise ToolError("salary_currency and salary_period are required with amounts.")
    cleaned_url = url.strip()
    if db.check_duplicate(cleaned_url):
        return {"status": "duplicate", "url": cleaned_url}
    scraped: scraper.ScrapedJob | None = None
    if title is None or company is None or description is None or location is None:
        try:
            scraped = scraper.scrape_job(cleaned_url)
        except ScrapeError as exc:
            raise ToolError(str(exc)) from exc
    if scraped is not None:
        title = title if title is not None else scraped["title"]
        company = company if company is not None else scraped["company"]
        description = description if description is not None else scraped["description"]
        if location is None:
            location = scraped["location"]
    error = _check_fields(cleaned_url, title, company, description)
    if error is not None:
        raise ToolError(error)
    assert title is not None and company is not None and description is not None
    clean_title = title.strip()
    clean_company = company.strip()
    clean_description = description.strip()
    flags = applicability.check_applicability(location, clean_description)
    if flags and not force:
        return {
            "status": "flagged",
            "url": cleaned_url,
            "applicability": {"flags": flags},
            "position": {
                "title": clean_title,
                "company": clean_company,
                "location": location,
                "description": clean_description,
            },
            "hint": (
                "Read the listing; if it still fits a Serbia/remote-EU applicant, "
                "call again with force=true."
            ),
        }
    result = _ingest(
        cleaned_url,
        clean_title,
        clean_company,
        location,
        clean_description,
        status,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        tags=tuple(tags or ()),
    )
    result["applicability"] = {"flags": flags}
    return result


@mcp.tool()
def scan(urls: list[str], tags: list[str] | None = None, force: bool = False) -> dict[str, object]:
    """Scrape and add the positions at each URL; duplicates are skipped.

    Listings whose fields look tied to a specific country are held back under
    flagged (with the reasons and the position fields for review) instead of
    being added; re-scan those URLs with force=true after reading them.
    Returns added/skipped counts plus per-URL errors, flagged entries and any
    similar matches.
    """
    db.init_db()
    tag_tuple = tuple(tags or ())
    added = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    similar: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    for url in urls:
        try:
            job = scraper.scrape_job(url)
        except ScrapeError as exc:
            errors.append({"url": url, "error": str(exc)})
            continue
        if db.check_duplicate(url):
            skipped += 1
            continue
        flags = applicability.check_applicability(job["location"], job["description"])
        if flags and not force:
            flagged.append(_flagged_payload(url, flags, job))
            continue
        result = _ingest(
            url, job["title"], job["company"], job["location"], job["description"], tags=tag_tuple
        )
        if result["status"] == "added":
            added += 1
            match = result.get("similar")
            if match is not None:
                similar.append(cast("dict[str, object]", match))
        else:
            skipped += 1
    payload: dict[str, object] = {"added": added, "skipped": skipped, "errors": errors}
    if flagged:
        payload["flagged"] = flagged
    if similar:
        payload["similar"] = similar
    return payload


@mcp.tool()
def discover(
    source: str,
    limit: int = discovery.DEFAULT_LIMIT,
    add: bool = False,
    tags: list[str] | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Extract candidate listing links from a job source (URL or job-sources name).

    Without add, returns the candidates split into new-vs-known. With
    add=true, scrapes and ingests every new candidate like scan does;
    candidates whose fields look tied to a specific country are held back
    under flagged (with the reasons and fields for review) unless force=true.
    """
    db.init_db()
    if not 1 <= limit <= discovery.MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {discovery.MAX_LIMIT}")
    try:
        src = discovery.resolve_source(source)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    try:
        result = discovery.discover(src, limit=limit)
    except ScrapeError as exc:
        raise ToolError(str(exc)) from exc
    src_ref = result.source
    payload: dict[str, object] = {
        "source": {
            "input": src_ref.input,
            "url": src_ref.url,
            "site": src_ref.site,
            "site_status": src_ref.site_status,
        },
        "extracted": len(result.candidates),
        "new_count": result.new_count,
        "known_count": result.known_count,
        "known_by_status": result.status_counts,
        "candidates": list(result.candidates),
    }
    if add:
        added = 0
        skipped = 0
        errors: list[dict[str, str]] = []
        similar: list[dict[str, object]] = []
        flagged: list[dict[str, object]] = []
        tag_tuple = tuple(tags or ())
        for url in result.new_urls:
            try:
                job = scraper.scrape_job(url)
            except ScrapeError as exc:
                errors.append({"url": url, "error": str(exc)})
                continue
            flags = applicability.check_applicability(job["location"], job["description"])
            if flags and not force:
                flagged.append(_flagged_payload(url, flags, job))
                continue
            ingested = _ingest(
                url,
                job["title"],
                job["company"],
                job["location"],
                job["description"],
                tags=tag_tuple,
            )
            if ingested["status"] == "added":
                added += 1
                match = ingested.get("similar")
                if match is not None:
                    similar.append(cast("dict[str, object]", match))
            else:
                skipped += 1
        payload["added"] = added
        payload["skipped"] = skipped
        payload["errors"] = errors
        if flagged:
            payload["flagged"] = flagged
        if similar:
            payload["similar"] = similar
    return payload


@mcp.tool()
def update_status(job_id: int, status: str, comment: str | None = None) -> dict[str, object]:
    """Move a position to unapplied/applied/interview/rejected.

    comment (optional) records a note for the status-change history and is not
    allowed when resetting to unapplied.
    """
    db.init_db()
    _validate_choice(status, STATUSES, "status")
    _position_or_error(job_id)
    if comment is not None and status == "unapplied":
        raise ToolError("comment is not allowed when resetting to unapplied")
    db.set_job_status(job_id, cast("db.JobStatus", status))
    if comment is not None:
        db.set_job_comment(job_id, cast("db.JobFlag", status), comment)
    return {"id": job_id, "status": status, "comment_set": comment is not None}


@mcp.tool()
def set_tags(
    job_id: int, add: list[str] | None = None, remove: list[str] | None = None
) -> dict[str, object]:
    """Add and/or remove tags on a position (case-insensitive; unknown adds are created)."""
    db.init_db()
    _position_or_error(job_id)
    names = db.job_tags(job_id)
    for name in remove or []:
        names = [existing for existing in names if existing.lower() != name.lower()]
    existing_lower = {name.lower() for name in names}
    for name in add or []:
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in existing_lower:
            names.append(cleaned)
            existing_lower.add(cleaned.lower())
    db.set_job_tags(job_id, names)
    return {"id": job_id, "tags": db.job_tags(job_id)}


@mcp.tool()
def delete_positions(
    job_ids: list[int] | None = None,
    urls: list[str] | None = None,
    status: str | None = None,
    search: str | None = None,
    tags: list[str] | None = None,
    min_salary: int | None = None,
    max_salary: int | None = None,
    salary_currency: str | None = None,
    confirm: bool = False,
) -> dict[str, object]:
    """Delete positions by id, URL, or filters (at least one selector required).

    Preview-first: with confirm=false (the default) only reports what matches;
    repeat with confirm=true to actually delete. Unmatched ids/URLs are listed.
    """
    db.init_db()
    _validate_choice(status, STATUSES, "status")
    _validate_choice(salary_currency, salary.CURRENCIES, "salary_currency")
    id_tuple = tuple(job_ids or ())
    wanted_urls = list(urls or ())
    if (
        not id_tuple
        and not wanted_urls
        and not any((status, search, tags, min_salary, max_salary, salary_currency))
    ):
        raise ToolError("Provide job_ids, urls, or at least one filter.")
    target, not_found_ids, not_found_urls = _resolve_delete(
        id_tuple,
        wanted_urls,
        status,
        search,
        min_salary,
        max_salary,
        salary_currency,
        tuple(tags or ()),
    )
    matched_ids = [job["id"] for job in target]
    payload: dict[str, object] = {
        "matched": len(target),
        "ids": matched_ids,
        "sample": [
            {"id": job["id"], "title": job["title"], "company": job["company"]}
            for job in target[:5]
        ],
        "not_found_ids": not_found_ids,
        "not_found_urls": not_found_urls,
    }
    if not confirm:
        return payload
    for job_id in matched_ids:
        db.delete_job(job_id)
    payload["deleted"] = len(matched_ids)
    return payload


@mcp.tool()
def rename_tag(name: str, new_name: str) -> dict[str, object]:
    """Rename a tag everywhere it is used."""
    db.init_db()
    tag_id = db.find_tag(name)
    if tag_id is None:
        raise ToolError(f"No tag named '{name}'")
    if not db.rename_tag(tag_id, new_name):
        raise ToolError(f"Could not rename '{name}' to '{new_name}' (empty or taken?)")
    return {"renamed": name, "name": new_name.strip()}


@mcp.tool()
def delete_tag(name: str) -> dict[str, object]:
    """Delete a tag; every use of it is removed from positions."""
    db.init_db()
    tag_id = db.find_tag(name)
    if tag_id is None:
        raise ToolError(f"No tag named '{name}'")
    db.delete_tag(tag_id)
    return {"deleted": name}


def main() -> None:
    """Start the MCP server over stdio (blocks until the client disconnects)."""
    db.init_db()
    mcp.run()
