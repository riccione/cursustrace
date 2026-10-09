"""tags, backup, delete, and clear commands."""

from __future__ import annotations

import json as json_module
import sqlite3
from pathlib import Path

import click

from cursustrace import backup, db, salary
from cursustrace.cli.common import STATUSES, _resolve_delete
from cursustrace.cli.group import cli
from cursustrace.config import load_settings
from cursustrace.errors import ConfigError


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
