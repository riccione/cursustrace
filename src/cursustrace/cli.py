"""Command-line interface for headless job ingestion."""

from __future__ import annotations

import json as json_module
from typing import TextIO, cast

import click

from cursustrace import db, scraper
from cursustrace.config import load_settings
from cursustrace.errors import ConfigError, ScrapeError
from cursustrace.validation import required_error, validate_url

STATUSES = ["unapplied", "applied", "interview", "rejected"]


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
    duplicate, _reason = db.check_duplicate(url, title, company, location or None, description)
    if duplicate:
        return {"status": "duplicate", "url": url}

    job_id = db.add_job(url, title, company, location or None, description)
    if job_id is None:
        return {"status": "duplicate", "url": url}

    if status != "unapplied":
        db.set_job_status(job_id, cast("db.JobStatus", status))
    return {"status": "added", "id": job_id, "url": url}


def _ingest_item(item: object, index: int) -> tuple[str, dict[str, str] | None]:
    """Validate, scrape if needed, and ingest one imported JSON item."""
    if not isinstance(item, dict):
        return ("error", {"item": str(index), "error": "expected an object"})

    url = str(item.get("url") or item.get("job_url") or "").strip()
    url_error = validate_url(url)
    if url_error is not None:
        return ("error", {"item": str(index), "error": url_error})

    title = item.get("title")
    company = item.get("company")
    location = item.get("location")
    description = item.get("description")
    if not (title and company and description):
        try:
            scraped = scraper.scrape_job(url)
        except ScrapeError as exc:
            return ("error", {"url": url, "error": str(exc)})
        title = title or scraped["title"]
        company = company or scraped["company"]
        location = location or scraped["location"]
        description = description or scraped["description"]

    result = _ingest(url, title, company, location, description)
    return ("skipped" if result["status"] == "duplicate" else "added", None)


def _report_summary(added: int, skipped: int, errors: list[dict[str, str]], as_json: bool) -> None:
    """Print the batch result and exit non-zero when any item errored."""
    if as_json:
        click.echo(json_module.dumps({"added": added, "skipped": skipped, "errors": errors}))
    else:
        for error in errors:
            label = error.get("url", error.get("item", "?"))
            click.echo(f"Error ({label}): {error['error']}", err=True)
        click.echo(f"Added {added}, skipped {skipped}, errors {len(errors)}.")
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
@click.pass_context
def cli(ctx: click.Context) -> None:
    """CursusTrace — job application tracker."""
    if ctx.invoked_subcommand is None:
        from cursustrace.app import run

        run()


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
def run_command(
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


@cli.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
def scan(urls: tuple[str, ...], as_json: bool) -> None:
    """Scrape one or more URLs and add the positions."""
    db.init_db()
    added = skipped = 0
    errors: list[dict[str, str]] = []
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
    _report_summary(added, skipped, errors, as_json)


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
    for index, item in enumerate(items, start=1):
        outcome, error = _ingest_item(item, index)
        if outcome == "added":
            added += 1
        elif outcome == "skipped":
            skipped += 1
        elif error is not None:
            errors.append(error)

    _report_summary(added, skipped, errors, as_json)


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
