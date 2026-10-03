"""Command-line interface for headless job ingestion."""

from __future__ import annotations

import json as json_module
import logging
import sqlite3
from pathlib import Path
from typing import TextIO, cast

import click

from cursustrace import backup, db, discovery, exporters, salary, scraper
from cursustrace.config import LOG_LEVELS, load_settings
from cursustrace.errors import ConfigError, ScrapeError
from cursustrace.logsetup import setup_logging
from cursustrace.validation import required_error, validate_url

STATUSES = ["unapplied", "applied", "interview", "rejected"]
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
    if not (title and company and description):
        try:
            scraped = scraper.scrape_job(url)
        except ScrapeError as exc:
            return ("error", {"url": url, "error": str(exc)}, [])
        title = title or scraped["title"]
        company = company or scraped["company"]
        location = location or scraped["location"]
        description = description or scraped["description"]

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


@click.group(invoke_without_command=True)
@click.version_option(
    None,
    "-V",
    "--version",
    package_name="cursustrace",
    prog_name="cursustrace",
    message="cursustrace %(version)s",
)
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    default=None,
    help="Logging verbosity (default from config/env).",
)
@click.pass_context
def cli(ctx: click.Context, log_level: str | None) -> None:
    """CursusTrace — job application tracker."""
    try:
        settings = load_settings(log_level=log_level)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    setup_logging(
        settings.log_level,
        retention_days=settings.log_retention_days,
        console=False,
    )
    ctx.obj = settings.log_level
    if ctx.invoked_subcommand is None:
        from cursustrace.app import run

        run(settings)


@cli.command()
@click.option("--host", default=None, help="Host to bind (default from config/env).")
@click.option("--port", type=int, default=None, help="Port to bind.")
@click.option("--reload/--no-reload", "reload", default=None, help="Auto-reload on file changes.")
@click.option("--show/--no-show", "show", default=None, help="Open a browser tab.")
@click.option(
    "--css",
    "cv_style",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to the CV stylesheet.",
)
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
@click.pass_context
def run_command(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    reload: bool | None,
    show: bool | None,
    cv_style: str | None,
    config_path: str | None,
) -> None:
    """Launch the dashboard (host/port/config overrides)."""
    try:
        settings = load_settings(
            host=host,
            port=port,
            reload=reload,
            show=show,
            cv_style_path=cv_style,
            log_level=ctx.obj,
            config_path=config_path,
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    from cursustrace.app import run

    run(settings)


@cli.command()
@click.option("--url", required=True, help="Full job posting URL.")
@click.option("--title", required=True)
@click.option("--company", required=True)
@click.option("--description", required=True)
@click.option("--location", default=None)
@click.option("--status", type=click.Choice(STATUSES), default="unapplied", show_default=True)
@click.option("--salary-min", type=int, default=None, help="Minimum salary amount.")
@click.option("--salary-max", type=int, default=None, help="Maximum salary amount.")
@click.option("--salary-currency", type=click.Choice(salary.CURRENCIES), default=None)
@click.option("--salary-period", type=click.Choice(salary.PERIODS), default=None)
@click.option("--salary-note", default=None, help="Free-text salary note.")
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Tag to assign (repeatable; unknown tags are created).",
)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def add(
    url: str,
    title: str,
    company: str,
    description: str,
    location: str | None,
    status: str,
    salary_min: int | None,
    salary_max: int | None,
    salary_currency: str | None,
    salary_period: str | None,
    salary_note: str | None,
    tags: tuple[str, ...],
    as_json: bool,
) -> None:
    """Add a position from explicit fields."""
    db.init_db()
    error = _check_fields(url, title, company, description)
    if error is not None:
        raise click.ClickException(error)
    has_amount = salary_min is not None or salary_max is not None
    if has_amount and not (salary_currency and salary_period):
        raise click.ClickException(
            "--salary-currency and --salary-period are required with amounts."
        )

    clean_url = url.strip()
    clean_title = title.strip()
    result = _ingest(
        clean_url,
        clean_title,
        company.strip(),
        location,
        description.strip(),
        status,
        salary_min=salary_min if has_amount else None,
        salary_max=salary_max if has_amount else None,
        salary_currency=salary_currency if has_amount else None,
        salary_period=salary_period if has_amount else None,
        salary_note=salary_note,
        tags=tags,
    )
    if as_json:
        click.echo(json_module.dumps(result))
    elif result["status"] == "duplicate":
        click.echo(f"Skipped (already tracked): {clean_url}")
    else:
        click.echo(f"Added '{clean_title}' (id {result['id']}).")
    for match in cast("list[dict[str, object]]", result.get("similar", [])):
        logger.info("Added position looks similar to '%s' (%s)", match["title"], match["url"])
        if not as_json:
            click.echo(
                f"Note: looks similar to '{match['title']}' ({match['url']})",
                err=True,
            )


@cli.command()
@click.argument("urls", nargs=-1, required=True)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Tag to assign (repeatable; unknown tags are created).",
)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
def scan(urls: tuple[str, ...], tags: tuple[str, ...], as_json: bool) -> None:
    """Scrape one or more URLs and add the positions."""
    db.init_db()
    added = skipped = 0
    errors: list[dict[str, str]] = []
    similar: list[dict[str, object]] = []
    for url in urls:
        try:
            job = scraper.scrape_job(url)
        except ScrapeError as exc:
            errors.append({"url": url, "error": str(exc)})
            continue
        result = _ingest(
            url, job["title"], job["company"], job["location"], job["description"], tags=tags
        )
        if result["status"] == "duplicate":
            skipped += 1
            click.echo(f"Skipped (already tracked): {url}", err=True)
        else:
            added += 1
            similar.extend(cast("list[dict[str, object]]", result.get("similar", [])))
    _report_summary(added, skipped, errors, as_json, similar)


@cli.command()
@click.argument("source_arg", metavar="SOURCE")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
@click.option("--add", "add_new", is_flag=True, help="Scrape and add every new position found.")
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Tag to assign with --add (repeatable; unknown tags are created).",
)
@click.option(
    "--limit",
    type=click.IntRange(1, discovery.MAX_LIMIT),
    default=discovery.DEFAULT_LIMIT,
    show_default=True,
    help="Maximum candidate links to examine.",
)
def discover(
    source_arg: str, as_json: bool, add_new: bool, tags: tuple[str, ...], limit: int
) -> None:
    """Discover position links from a job source (URL or job-sources site name)."""
    db.init_db()
    try:
        source = discovery.resolve_source(source_arg)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        result = discovery.discover(source, limit=limit)
    except ScrapeError as exc:
        if as_json:
            click.echo(json_module.dumps({"source": {"input": source_arg}, "error": str(exc)}))
            raise click.exceptions.Exit(1)
        raise click.ClickException(str(exc)) from exc
    added = skipped = 0
    errors: list[dict[str, str]] = []
    similar: list[dict[str, object]] = []
    if add_new:
        for url in result.new_urls:
            try:
                job = scraper.scrape_job(url)
            except ScrapeError as exc:
                errors.append({"url": url, "error": str(exc)})
                continue
            ingest = _ingest(
                url,
                job["title"],
                job["company"],
                job["location"],
                job["description"],
                tags=tags,
            )
            if ingest["status"] == "duplicate":
                skipped += 1
            else:
                added += 1
                similar.extend(cast("list[dict[str, object]]", ingest.get("similar", [])))
    _report_discovery(result, added, skipped, errors, as_json, similar, add_new)


def _status_breakdown(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    order = ("unapplied", "applied", "interview", "rejected")
    parts = [f"{counts[status]} {status}" for status in order if status in counts]
    return f" ({', '.join(parts)})"


def _report_discovery(
    result: discovery.DiscoveryResult,
    added: int,
    skipped: int,
    errors: list[dict[str, str]],
    as_json: bool,
    similar: list[dict[str, object]],
    add_new: bool,
) -> None:
    """Print the discovery result; exit non-zero when ingestion had errors."""
    source = result.source
    if as_json:
        for error in errors:
            logger.error("Discovery ingest error (%s): %s", error["url"], error["error"])
        for match in similar:
            logger.info("Added position looks similar to '%s' (%s)", match["title"], match["url"])
        payload: dict[str, object] = {
            "source": {
                "input": source.input,
                "url": source.url,
                "site": source.site,
                "site_status": source.site_status,
            },
            "extracted": len(result.candidates),
            "new_count": result.new_count,
            "known_count": result.known_count,
            "known_by_status": result.status_counts,
            "candidates": list(result.candidates),
        }
        if add_new:
            payload["added"] = added
            payload["skipped"] = skipped
            payload["errors"] = errors
            if similar:
                payload["similar"] = similar
        click.echo(json_module.dumps(payload))
        if errors:
            raise click.exceptions.Exit(1)
        return
    label = source.site or source.input
    status = f" ({source.site_status})" if source.site_status else ""
    click.echo(f"Source: {label} → {source.url}{status}")
    breakdown = _status_breakdown(result.status_counts)
    click.echo(
        f"Extracted {len(result.candidates)} links · {result.new_count} new"
        f" · {result.known_count} already in database{breakdown}"
    )
    for url in result.new_urls:
        click.echo(f"  {url}")
    if add_new:
        _report_summary(added, skipped, errors, False, similar)
    elif result.new_count:
        click.echo(
            "Tip: add --add to ingest all new positions, or pass URLs to `cursustrace scan`."
        )


@cli.command(name="import")
@click.argument("source", type=click.File("r"), required=False, default="-")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
def import_jobs(source: TextIO, as_json: bool) -> None:
    """Import positions from a JSON array (path or stdin)."""
    db.init_db()
    try:
        items = json_module.load(source)
    except json_module.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON: {exc}") from exc
    if not isinstance(items, list):
        raise click.ClickException("Expected a JSON array of job objects.")

    added = skipped = 0
    errors: list[dict[str, str]] = []
    similar: list[dict[str, object]] = []
    for index, item in enumerate(items, start=1):
        outcome, error, item_similar = _ingest_item(item, index)
        if outcome == "added":
            added += 1
            similar.extend(item_similar)
        elif outcome == "skipped":
            skipped += 1
        elif error is not None:
            errors.append(error)

    _report_summary(added, skipped, errors, as_json, similar)


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


@cli.command(name="list")
@click.option("--status", type=click.Choice(STATUSES), default=None)
@click.option("--search", default=None, help="Fuzzy company search.")
@click.option("--min-salary", type=int, default=None, help="Minimum annualized salary.")
@click.option("--max-salary", type=int, default=None, help="Maximum annualized salary.")
@click.option("--salary-currency", type=click.Choice(salary.CURRENCIES), default=None)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Only positions with this tag (repeatable, any-match).",
)
@click.option(
    "--sort",
    "sort_order",
    type=click.Choice(SORT_ORDERS),
    default=None,
    help="Order the output (default: insertion order, newest first).",
)
@click.option(
    "--limit",
    type=click.IntRange(min=0),
    default=None,
    help="Print at most this many positions, after sorting.",
)
@click.option(
    "--offset",
    type=click.IntRange(min=0),
    default=0,
    help="Skip this many positions after sorting.",
)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON array.")
def list_jobs(
    status: str | None,
    search: str | None,
    min_salary: int | None,
    max_salary: int | None,
    salary_currency: str | None,
    tags: tuple[str, ...],
    sort_order: str | None,
    limit: int | None,
    offset: int,
    as_json: bool,
) -> None:
    """List stored positions."""
    db.init_db()
    jobs = _filtered_jobs(status, search, min_salary, max_salary, salary_currency, tags)
    if sort_order is not None:
        jobs = db.sort_jobs(jobs, cast("db.JobSort", sort_order))
    if offset:
        jobs = jobs[offset:]
    if limit is not None:
        jobs = jobs[:limit]
    if as_json:
        click.echo(json_module.dumps([dict(job) for job in jobs]))
        return
    if not jobs:
        click.echo("No positions.")
        return
    for job in jobs:
        title = job["title"] or "Untitled position"
        company = job["company"] or "Unknown company"
        click.echo(f"{job['id']}. {title} — {company} [{db.job_status(job)}]")


@cli.command()
@click.argument("output", type=str, default="-")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "csv"]),
    default=None,
    help="Output format (default: from the file extension, else json).",
)
@click.option("--status", type=click.Choice(STATUSES), default=None)
@click.option("--search", default=None, help="Fuzzy company search.")
@click.option("--min-salary", type=int, default=None, help="Minimum annualized salary.")
@click.option("--max-salary", type=int, default=None, help="Maximum annualized salary.")
@click.option("--salary-currency", type=click.Choice(salary.CURRENCIES), default=None)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Only positions with this tag (repeatable, any-match).",
)
def export(
    output: str,
    fmt: str | None,
    status: str | None,
    search: str | None,
    min_salary: int | None,
    max_salary: int | None,
    salary_currency: str | None,
    tags: tuple[str, ...],
) -> None:
    """Export positions to JSON (import-compatible) or CSV, on stdout by default."""
    db.init_db()
    jobs = _filtered_jobs(status, search, min_salary, max_salary, salary_currency, tags)
    chosen = fmt or ("csv" if output.lower().endswith(".csv") else "json")
    payload = exporters.render_csv(jobs) if chosen == "csv" else exporters.render_json(jobs)
    if output == "-":
        click.echo(payload)
        return
    Path(output).write_text(payload + "\n", encoding="utf-8")
    click.echo(f"Exported {len(jobs)} position(s) to {output}.")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Print a JSON object.")
def stats(as_json: bool) -> None:
    """Show position counts per pipeline stage and salary statistics."""
    db.init_db()
    counts = db.job_counts()
    summary = salary.salary_summary(db.get_jobs())
    if as_json:
        payload: dict[str, object] = dict(counts)
        if summary:
            payload["salary"] = summary
        click.echo(json_module.dumps(payload))
        return
    click.echo(f"Total: {counts['total']}")
    click.echo(f"Unapplied: {counts['unapplied']}")
    click.echo(f"Applied: {counts['applied']}")
    click.echo(f"Interview: {counts['interview']}")
    click.echo(f"Rejected: {counts['rejected']}")
    if not summary:
        return
    for currency, entry in summary.items():
        click.echo(
            f"Salary ({currency}, annualized): {entry['count']} positions, "
            f"min {entry['minimum']:,}, median {entry['median']:,}, max {entry['maximum']:,}"
        )


@cli.group(name="tags")
def tags_group() -> None:
    """Manage tags (labels) for filtering positions."""


@tags_group.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON object.")
def tags_list(as_json: bool) -> None:
    """List tags with their position counts."""
    db.init_db()
    counts = db.tag_counts()
    if as_json:
        click.echo(json_module.dumps(counts))
        return
    if not counts:
        click.echo("No tags.")
        return
    for name, count in counts.items():
        click.echo(f"{name} ({count})")


@tags_group.command(name="add")
@click.argument("names", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def tags_add(names: tuple[str, ...], as_json: bool) -> None:
    """Create one or more tags (existing names are reported, not an error)."""
    db.init_db()
    cleaned = [name.strip() for name in names]
    if any(not name for name in cleaned):
        raise click.ClickException("Tag names must not be empty.")
    created = [name for name in cleaned if db.create_tag(name) is not None]
    existing = [name for name in cleaned if name not in created]
    if as_json:
        click.echo(json_module.dumps({"created": created, "existing": existing}))
        return
    if created:
        click.echo(f"Created {len(created)} tag(s): {', '.join(created)}.")
    if existing:
        click.echo(f"Already present: {', '.join(existing)}.")


@tags_group.command(name="rename")
@click.argument("old_name")
@click.argument("new_name")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def tags_rename(old_name: str, new_name: str, as_json: bool) -> None:
    """Rename a tag, keeping its assignments."""
    db.init_db()
    tag_id = db.find_tag(old_name)
    if tag_id is None:
        raise click.ClickException(f"Unknown tag: {old_name}")
    if not db.rename_tag(tag_id, new_name):
        raise click.ClickException(
            f"Cannot rename '{old_name}' to '{new_name.strip()}' (empty or taken)."
        )
    if as_json:
        click.echo(json_module.dumps({"renamed": old_name, "to": new_name.strip()}))
        return
    click.echo(f"Renamed '{old_name}' -> '{new_name.strip()}'.")


@tags_group.command(name="rm")
@click.argument("names", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def tags_rm(names: tuple[str, ...], as_json: bool) -> None:
    """Delete tags (unknown names abort before anything is removed)."""
    db.init_db()
    found = [db.find_tag(name) for name in names]
    unknown = [name for name, tag_id in zip(names, found, strict=True) if tag_id is None]
    if unknown:
        raise click.ClickException(f"Unknown tag(s): {', '.join(unknown)}")
    removed = [name.strip() for name in names]
    for tag_id in found:
        if tag_id is not None:
            db.delete_tag(tag_id)
    if as_json:
        click.echo(json_module.dumps({"removed": removed}))
        return
    click.echo(f"Removed {len(removed)} tag(s): {', '.join(removed)}.")


@cli.command()
@click.option(
    "--dir",
    "target_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Backup directory (default from config/env).",
)
@click.option("--keep", type=int, default=None, help="Backups to retain (0 = keep all).")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def backup_command(target_dir: str | None, keep: int | None, as_json: bool) -> None:
    """Write a consistent copy of the database and prune older backups."""
    db.init_db()
    try:
        settings = load_settings(backup_dir=target_dir, backup_keep=keep)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        result = backup.backup_database(settings.backup_dir, keep=settings.backup_keep)
    except (OSError, sqlite3.Error) as exc:
        raise click.ClickException(f"Backup failed: {exc}") from exc
    if result is None:
        raise click.ClickException("No database to back up yet.")

    payload: dict[str, object] = {
        "status": "ok",
        "path": str(result.path),
        "positions": db.job_counts()["total"],
        "pruned": list(result.pruned),
    }
    if as_json:
        click.echo(json_module.dumps(payload))
        return
    click.echo(f"Backed up {payload['positions']} position(s) to {result.path}.")
    if result.pruned:
        click.echo(f"Pruned {len(result.pruned)} older backup(s).")


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


@cli.command()
@click.argument("job_ids", nargs=-1, type=int)
@click.option("--url", "urls", multiple=True, help="Delete positions with this URL (repeatable).")
@click.option(
    "--url-file",
    type=click.Path(dir_okay=False, exists=True),
    default=None,
    help="File with one position URL per line (# comments and blank lines ignored).",
)
@click.option("--status", type=click.Choice(STATUSES), default=None)
@click.option("--search", default=None, help="Fuzzy company search.")
@click.option("--min-salary", type=int, default=None, help="Minimum annualized salary.")
@click.option("--max-salary", type=int, default=None, help="Maximum annualized salary.")
@click.option("--salary-currency", type=click.Choice(salary.CURRENCIES), default=None)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Only positions with this tag (repeatable, any-match).",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result (requires --yes).")
def delete(
    job_ids: tuple[int, ...],
    urls: tuple[str, ...],
    url_file: str | None,
    status: str | None,
    search: str | None,
    min_salary: int | None,
    max_salary: int | None,
    salary_currency: str | None,
    tags: tuple[str, ...],
    yes: bool,
    as_json: bool,
) -> None:
    """Delete positions selected by ID, URL, or the shared list filters."""
    db.init_db()
    has_filter = any((status, search, min_salary, max_salary, salary_currency, tags))
    file_urls: list[str] = []
    if url_file is not None:
        file_urls = [
            line.strip()
            for line in Path(url_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    wanted_urls = [*urls, *file_urls]
    if not job_ids and not wanted_urls and not has_filter:
        raise click.ClickException(
            "Provide position IDs, --url/--url-file, or at least one filter."
        )
    if as_json and not yes:
        raise click.ClickException("--json requires --yes.")

    target_jobs, not_found_ids, not_found_urls = _resolve_delete(
        job_ids, wanted_urls, status, search, min_salary, max_salary, salary_currency, tags
    )
    target = [job["id"] for job in target_jobs]

    if not as_json:
        for job in target_jobs[:5]:
            title = job["title"] or "Untitled position"
            company = job["company"] or "Unknown company"
            click.echo(f"  {job['id']}. {title} — {company}")
        if len(target_jobs) > 5:
            click.echo(f"  … and {len(target_jobs) - 5} more")
    if target_jobs and not yes:
        click.confirm(f"Delete {len(target_jobs)} position(s)? This cannot be undone.", abort=True)
    for job_id in target:
        db.delete_job(job_id)

    if as_json:
        payload: dict[str, object] = {
            "deleted": len(target),
            "ids": target,
            "not_found_ids": not_found_ids,
            "not_found_urls": not_found_urls,
        }
        click.echo(json_module.dumps(payload))
        return
    if not target:
        click.echo("No matching positions.")
    else:
        click.echo(f"Removed {len(target)} position(s).")
    if not_found_ids or not_found_urls:
        click.echo(
            f"Not found: {len(not_found_ids)} id(s), {len(not_found_urls)} URL(s).",
            err=True,
        )


@cli.command()
@click.option(
    "--all",
    "clear_all",
    is_flag=True,
    help="Also delete profile/CV, settings, and tags.",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def clear(clear_all: bool, yes: bool, as_json: bool) -> None:
    """Delete job positions, or ALL data with --all."""
    db.init_db()
    prompt = (
        "Delete ALL data (positions, profile/CV, settings, tags)? This cannot be undone."
        if clear_all
        else "Delete all job positions? This cannot be undone."
    )
    if not yes:
        click.confirm(prompt, abort=True)

    if clear_all:
        counts = db.clear_all_data()
        payload: dict[str, object] = {
            "positions": counts["positions"],
            "profile": True,
            "settings": counts["settings"],
        }
        message = f"Removed {counts['positions']} position(s) and cleared profile & settings."
    else:
        positions = db.clear_all_jobs()
        payload = {"positions": positions}
        message = f"Removed {positions} position(s)."

    if as_json:
        click.echo(json_module.dumps(payload))
    else:
        click.echo(message)


if __name__ == "__main__":
    cli()
