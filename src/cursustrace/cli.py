"""Command-line interface for headless job ingestion."""

from __future__ import annotations

import json as json_module
import logging
from typing import TextIO, cast

import click

from cursustrace import db, scraper
from cursustrace.config import LOG_LEVELS, load_settings
from cursustrace.errors import ConfigError, ScrapeError
from cursustrace.logsetup import setup_logging
from cursustrace.validation import required_error, validate_url

STATUSES = ["unapplied", "applied", "interview", "rejected"]
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
) -> dict[str, object]:
    if db.check_duplicate(url):
        logger.info("Skipped duplicate: %s", url)
        return {"status": "duplicate", "url": url}

    job_id = db.add_job(url, title, company, location or None, description)
    if job_id is None:
        logger.info("Skipped duplicate: %s", url)
        return {"status": "duplicate", "url": url}

    if status != "unapplied":
        db.set_job_status(job_id, cast("db.JobStatus", status))
    result: dict[str, object] = {"status": "added", "id": job_id, "url": url}
    if _similar_enabled():
        matches = db.find_similar(company, title, description, exclude_id=job_id)
        if matches:
            result["similar"] = [_similar_payload(job) for job in matches]
    return result


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
    if not (title and company and description):
        try:
            scraped = scraper.scrape_job(url)
        except ScrapeError as exc:
            return ("error", {"url": url, "error": str(exc)}, [])
        title = title or scraped["title"]
        company = company or scraped["company"]
        location = location or scraped["location"]
        description = description or scraped["description"]

    result = _ingest(url, title, company, location, description)
    similar = cast("list[dict[str, object]]", result.get("similar", []))
    return ("skipped" if result["status"] == "duplicate" else "added", None, similar)


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
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def add(
    url: str,
    title: str,
    company: str,
    description: str,
    location: str | None,
    status: str,
    as_json: bool,
) -> None:
    """Add a position from explicit fields."""
    db.init_db()
    error = _check_fields(url, title, company, description)
    if error is not None:
        raise click.ClickException(error)

    clean_url = url.strip()
    clean_title = title.strip()
    result = _ingest(
        clean_url,
        clean_title,
        company.strip(),
        location,
        description.strip(),
        status,
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
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
def scan(urls: tuple[str, ...], as_json: bool) -> None:
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
        result = _ingest(url, job["title"], job["company"], job["location"], job["description"])
        if result["status"] == "duplicate":
            skipped += 1
            click.echo(f"Skipped (already tracked): {url}", err=True)
        else:
            added += 1
            similar.extend(cast("list[dict[str, object]]", result.get("similar", [])))
    _report_summary(added, skipped, errors, as_json, similar)


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


@cli.command(name="list")
@click.option("--status", type=click.Choice(STATUSES), default=None)
@click.option("--search", default=None, help="Fuzzy company search.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON array.")
def list_jobs(status: str | None, search: str | None, as_json: bool) -> None:
    """List stored positions."""
    db.init_db()
    jobs = db.search_jobs(search or "", status=cast("db.JobStatus | None", status))
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
@click.option("--json", "as_json", is_flag=True, help="Print a JSON object.")
def stats(as_json: bool) -> None:
    """Show position counts per pipeline stage."""
    db.init_db()
    counts = db.job_counts()
    if as_json:
        click.echo(json_module.dumps(counts))
        return
    click.echo(f"Total: {counts['total']}")
    click.echo(f"Unapplied: {counts['unapplied']}")
    click.echo(f"Applied: {counts['applied']}")
    click.echo(f"Interview: {counts['interview']}")
    click.echo(f"Rejected: {counts['rejected']}")


@cli.command()
@click.option("--all", "clear_all", is_flag=True, help="Also delete profile/CV and settings.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON result.")
def clear(clear_all: bool, yes: bool, as_json: bool) -> None:
    """Delete job positions, or ALL data with --all."""
    db.init_db()
    prompt = (
        "Delete ALL data (positions, profile/CV, settings)? This cannot be undone."
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
