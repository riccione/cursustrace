"""add, scan, and import commands."""

from __future__ import annotations

import json as json_module
import logging
from typing import TextIO, cast

import click

from cursustrace import db, salary, scraper
from cursustrace.cli.common import (
    STATUSES,
    _check_fields,
    _ingest,
    _ingest_item,
    _report_summary,
)
from cursustrace.cli.group import cli
from cursustrace.errors import ScrapeError
from cursustrace.validation import normalize_deadline

logger = logging.getLogger(__name__)


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
    "--deadline",
    default=None,
    help="Application deadline as an ISO date or datetime (e.g. 2026-12-31).",
)
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
    deadline: str | None,
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
    normalized_deadline: str | None = None
    if deadline is not None and deadline.strip():
        normalized_deadline = normalize_deadline(deadline)
        if normalized_deadline is None:
            raise click.ClickException(
                f"Invalid deadline {deadline!r}: expected an ISO date like 2026-12-31."
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
        deadline=normalized_deadline,
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
            url,
            job["title"],
            job["company"],
            job["location"],
            job["description"],
            deadline=job["deadline"],
            tags=tags,
        )
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
