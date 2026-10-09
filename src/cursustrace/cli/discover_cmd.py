"""discover command: list candidate links and optionally ingest them."""

from __future__ import annotations

import json as json_module
import logging
from typing import cast

import click

from cursustrace import db, discovery, scraper
from cursustrace.cli.common import _ingest, _report_summary
from cursustrace.cli.group import cli
from cursustrace.errors import ScrapeError

logger = logging.getLogger(__name__)


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
                deadline=job["deadline"],
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
    order = ("unapplied", "applied", "interview", "rejected", "outdated")
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
