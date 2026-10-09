"""list, export, and stats commands."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import cast

import click

from cursustrace import attention, db, exporters, salary
from cursustrace.cli.common import SORT_ORDERS, STATUSES, _filtered_jobs
from cursustrace.cli.group import cli
from cursustrace.config import load_settings


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
    "--attention",
    "needs_attention",
    is_flag=True,
    help="Only positions needing attention: stale stage or deadline within the warning window.",
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
    needs_attention: bool,
    sort_order: str | None,
    limit: int | None,
    offset: int,
    as_json: bool,
) -> None:
    """List stored positions."""
    db.init_db()
    jobs = _filtered_jobs(status, search, min_salary, max_salary, salary_currency, tags)
    thresholds: attention.Thresholds | None = None
    if needs_attention:
        thresholds = attention.Thresholds.from_settings(load_settings())
        jobs = [job for job in jobs if attention.reasons_for(job, thresholds)]
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
        click.echo("Nothing needs attention." if needs_attention else "No positions.")
        return
    for job in jobs:
        title = job["title"] or "Untitled position"
        company = job["company"] or "Unknown company"
        line = f"{job['id']}. {title} — {company} [{db.job_status(job)}]"
        if job["deadline"]:
            line += f" ⏰ {job['deadline']}"
        if thresholds is not None:
            reasons = attention.reasons_for(job, thresholds)
            if reasons:
                line += f" · {'; '.join(reasons)}"
        click.echo(line)


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
    click.echo(f"Outdated: {counts['outdated']}")
    if not summary:
        return
    for currency, entry in summary.items():
        click.echo(
            f"Salary ({currency}, annualized): {entry['count']} positions, "
            f"min {entry['minimum']:,}, median {entry['median']:,}, max {entry['maximum']:,}"
        )
