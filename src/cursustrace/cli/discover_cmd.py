"""discover command: list candidate links and optionally ingest them."""

from __future__ import annotations

import json as json_module
import logging
from typing import cast

import click

from cursustrace import config, db, discovery, jsearch, scraper
from cursustrace.cli.common import _ingest, _report_summary
from cursustrace.cli.group import cli
from cursustrace.errors import JSearchError, ScrapeError

logger = logging.getLogger(__name__)

_JSEARCH_ONLY_OPTIONS = {
    "results": "--results",
    "country": "--country",
    "date_posted": "--date-posted",
    "remote_only": "--remote",
    "force": "--force",
}

_ALL_ONLY_OPTIONS = {
    "per_board": "--per-board",
    "total": "--total",
    "include_blocked": "--include-blocked",
}


def _country_code(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().lower()
    if code not in jsearch.country_options():
        raise click.BadParameter(f"unknown country code '{value}'")
    return code


@cli.command()
@click.argument("source_arg", metavar="[SOURCE]", required=False)
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary.")
@click.option("--add", "add_new", is_flag=True, help="Add every new position found.")
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
@click.option("--query", "-q", "query", default=None, help="Keyword to search the source for.")
@click.option(
    "--only-live",
    is_flag=True,
    help="Skip named sources that are not currently live in job-sources/.",
)
@click.option(
    "--jsearch",
    "jsearch_query",
    metavar="QUERY",
    default=None,
    help="Search JSearch instead of a job source.",
)
@click.option(
    "--country",
    callback=_country_code,
    default=None,
    help="JSearch country code, e.g. rs (default: us).",
)
@click.option(
    "--date-posted",
    type=click.Choice(jsearch.DATE_POSTED_OPTIONS),
    default=None,
    help="JSearch posting-age filter.",
)
@click.option("--remote", "remote_only", is_flag=True, help="JSearch: remote jobs only.")
@click.option(
    "--results",
    type=click.IntRange(1, 50),
    default=10,
    show_default=True,
    help="JSearch results to fetch (one request per 10).",
)
@click.option("--force", is_flag=True, help="JSearch --add: include flagged results.")
@click.option(
    "--all",
    "all_boards",
    is_flag=True,
    help="Search every board in job-sources/ and merge the results.",
)
@click.option(
    "--per-board",
    type=click.IntRange(1, 50),
    default=discovery.BATCH_PER_BOARD,
    show_default=True,
    help="Maximum links to take from each board with --all.",
)
@click.option(
    "--total",
    type=click.IntRange(1, 1000),
    default=discovery.BATCH_TOTAL,
    show_default=True,
    help="Stop after this many new links with --all.",
)
@click.option(
    "--include-blocked",
    is_flag=True,
    help="Include boards marked blocked/login/dead with --all.",
)
@click.pass_context
def discover(
    ctx: click.Context,
    source_arg: str | None,
    as_json: bool,
    add_new: bool,
    tags: tuple[str, ...],
    limit: int,
    query: str | None,
    only_live: bool,
    jsearch_query: str | None,
    country: str | None,
    date_posted: str | None,
    remote_only: bool,
    results: int,
    force: bool,
    all_boards: bool,
    per_board: int,
    total: int,
    include_blocked: bool,
) -> None:
    """Discover links from a job source, JSearch, or every known board."""
    chosen = sum((source_arg is not None, jsearch_query is not None, all_boards))
    if chosen != 1:
        raise click.UsageError("Pass exactly one of SOURCE, --jsearch QUERY, or --all.")
    if all_boards:
        if ctx.get_parameter_source("limit") == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError("--limit applies to board sources; use --per-board with --all.")
        if ctx.get_parameter_source("results") == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError("--results applies only with --jsearch.")
        for name, option in _JSEARCH_ONLY_OPTIONS.items():
            if name == "results":
                continue
            if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                raise click.UsageError(f"{option} applies only with --jsearch.")
        _discover_all(
            query,
            as_json,
            add_new,
            tags,
            per_board,
            total,
            include_blocked,
        )
        return
    if jsearch_query is not None:
        if query is not None:
            raise click.UsageError(
                "--query is for board sources; pass the term to --jsearch instead."
            )
        if only_live:
            raise click.UsageError("--only-live applies to board sources only.")
        if ctx.get_parameter_source("limit") == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError("--limit applies to board sources; use --results instead.")
        for name, option in _ALL_ONLY_OPTIONS.items():
            if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                raise click.UsageError(f"{option} applies only with --all.")
        _discover_jsearch(
            ctx,
            jsearch_query,
            as_json,
            add_new,
            tags,
            country,
            date_posted,
            remote_only,
            results,
            force,
        )
        return
    if source_arg is None:
        raise click.UsageError("Pass a SOURCE, --jsearch QUERY, or --all.")
    for name, option in _JSEARCH_ONLY_OPTIONS.items():
        if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError(f"{option} applies only with --jsearch.")
    for name, option in _ALL_ONLY_OPTIONS.items():
        if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError(f"{option} applies only with --all.")

    db.init_db()
    try:
        source = discovery.resolve_source(source_arg)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        result = discovery.discover(source, limit=limit, query=query, only_live=only_live)
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


def _discover_all(
    query: str | None,
    as_json: bool,
    add_new: bool,
    tags: tuple[str, ...],
    per_board: int,
    total: int,
    include_blocked: bool,
) -> None:
    db.init_db()
    batch = discovery.discover_all(
        discovery.list_sources(),
        per_board=per_board,
        total=total,
        query=query,
        only_live=not include_blocked,
    )
    added = skipped = 0
    errors: list[dict[str, str]] = []
    similar: list[dict[str, object]] = []
    if add_new:
        for url in batch.new_urls:
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
    _report_all(batch, added, skipped, errors, similar, as_json, add_new)


def _report_all(
    batch: discovery.DiscoveryBatch,
    added: int,
    skipped: int,
    errors: list[dict[str, str]],
    similar: list[dict[str, object]],
    as_json: bool,
    add_new: bool,
) -> None:
    board_errors = [
        {"site": item.source.site, "url": item.source.url, "error": item.error}
        for item in batch.errors
    ]
    if as_json:
        for error in errors:
            logger.error("Discovery ingest error (%s): %s", error["url"], error["error"])
        for match in similar:
            logger.info("Added position looks similar to '%s' (%s)", match["title"], match["url"])
        payload: dict[str, object] = {
            "source": {
                "kind": "all",
                "boards": batch.checked,
                "skipped_not_live": batch.skipped_not_live,
            },
            "extracted": batch.extracted,
            "new_count": batch.new_count,
            "known_count": batch.known_count,
            "truncated": batch.truncated,
            "cancelled": batch.cancelled,
            "candidates": [
                {"url": str(item["url"]), "site": item.get("site")} for item in batch.candidates
            ],
            "board_errors": board_errors,
        }
        if batch.query is not None:
            payload["query"] = batch.query
        if batch.empty:
            payload["empty"] = batch.empty
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
    click.echo("Source: all known boards")
    if batch.query is not None:
        click.echo(f"Query: {batch.query}")
    click.echo(
        f"Checked {batch.checked} boards · {batch.skipped_not_live} skipped (not live)"
        f" · {batch.extracted} links · {batch.new_count} new"
        f" · {batch.known_count} already in database"
        + (f" · {batch.empty} without listings" if batch.empty else "")
    )
    if batch.truncated:
        click.echo(f"Stopped after {batch.new_count} new links.")
    if batch.cancelled:
        click.echo("Cancelled before every board was checked.")
    for candidate in batch.candidates:
        site = f" [{candidate['site']}]" if candidate.get("site") else ""
        title = candidate.get("title")
        label = f"{title} — {candidate['url']}" if title else str(candidate["url"])
        click.echo(f"  {label}{site}")
    if board_errors:
        click.echo(f"Failed boards ({len(board_errors)}):")
        for item in board_errors:
            board_label = item["site"] or item["url"]
            click.echo(f"  {board_label}: {item['error']}")
    if add_new:
        _report_summary(added, skipped, errors, False, similar)
    elif batch.new_count:
        click.echo("Tip: add --add to store the new positions, or --json for the full list.")


def _discover_jsearch(
    ctx: click.Context,
    query: str,
    as_json: bool,
    add_new: bool,
    tags: tuple[str, ...],
    country: str | None,
    date_posted: str | None,
    remote_only: bool,
    results: int,
    force: bool,
) -> None:
    db.init_db()
    settings = cast("config.Settings | None", ctx.obj)
    resolved = jsearch.api_key(settings)
    if resolved is None:
        raise click.ClickException(
            "No JSearch API key configured. Set CURSUS_JSEARCH_API_KEY, add "
            "jsearch_api_key to cursustrace.toml, or save one in the Discover tab."
        )
    key, _source = resolved
    try:
        page = jsearch.search_many(
            query,
            api_key=key,
            total=results,
            country=country,
            date_posted=date_posted,
            remote_only=remote_only,
        )
    except JSearchError as exc:
        if as_json:
            click.echo(
                json_module.dumps(
                    {"source": {"kind": "jsearch", "query": query}, "error": str(exc)}
                )
            )
            raise click.exceptions.Exit(1)
        raise click.ClickException(str(exc)) from exc

    new, tracked = jsearch.partition(page.jobs)
    flagged = tuple(job for job in new if job.flags)
    addable = new if force else tuple(job for job in new if not job.flags)
    added = skipped = 0
    similar: list[dict[str, object]] = []
    if add_new:
        for job in addable:
            outcome = _ingest(
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
                tags=tags,
            )
            if outcome["status"] == "duplicate":
                skipped += 1
            else:
                added += 1
                similar.extend(cast("list[dict[str, object]]", outcome.get("similar", [])))
    _report_jsearch(
        query,
        country,
        date_posted,
        remote_only,
        page,
        new,
        tracked,
        flagged,
        added,
        skipped,
        similar,
        as_json,
        add_new,
        force,
    )


def _jsearch_payload(job: jsearch.JSearchJob) -> dict[str, object]:
    return {
        "url": job.url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "salary_period": job.salary_period,
        "posted_at": job.posted_at,
        "publisher": job.publisher,
        "flags": list(job.flags),
    }


def _jsearch_line(job: jsearch.JSearchJob) -> str:
    title = job.title or "(untitled)"
    company = job.company or "?"
    location = f" ({job.location})" if job.location else ""
    return f"{title} — {company}{location}"


def _jsearch_candidates(
    page: jsearch.JSearchPage,
    new: tuple[jsearch.JSearchJob, ...],
    tracked: tuple[jsearch.KnownJob, ...],
) -> list[dict[str, object]]:
    new_urls = {job.url for job in new}
    known = {item.job.url: item for item in tracked}
    candidates: list[dict[str, object]] = []
    for job in page.jobs:
        if job.url in new_urls:
            candidates.append({**_jsearch_payload(job), "known": False})
        elif job.url in known:
            item = known[job.url]
            candidates.append(
                {
                    **_jsearch_payload(job),
                    "known": True,
                    "id": item.id,
                    "status": item.status,
                }
            )
    return candidates


def _report_jsearch(
    query: str,
    country: str | None,
    date_posted: str | None,
    remote_only: bool,
    page: jsearch.JSearchPage,
    new: tuple[jsearch.JSearchJob, ...],
    tracked: tuple[jsearch.KnownJob, ...],
    flagged: tuple[jsearch.JSearchJob, ...],
    added: int,
    skipped: int,
    similar: list[dict[str, object]],
    as_json: bool,
    add_new: bool,
    force: bool,
) -> None:
    used = jsearch.requests_this_month()
    if as_json:
        for match in similar:
            logger.info("Added position looks similar to '%s' (%s)", match["title"], match["url"])
        payload: dict[str, object] = {
            "source": {
                "kind": "jsearch",
                "query": query,
                "country": country,
                "date_posted": date_posted,
                "remote": remote_only,
            },
            "requests": page.requests,
            "usage": {
                "used": used,
                "limit": jsearch.MONTHLY_LIMIT,
                "month": jsearch.month_key(),
            },
            "extracted": len(page.jobs),
            "new_count": len(new),
            "known_count": len(tracked),
            "flagged_count": len(flagged),
            "candidates": _jsearch_candidates(page, new, tracked),
        }
        if add_new:
            payload["added"] = added
            payload["skipped"] = skipped
            payload["errors"] = []
            payload["flagged"] = [_jsearch_payload(job) for job in flagged]
            if similar:
                payload["similar"] = similar
        click.echo(json_module.dumps(payload))
        return
    click.echo(f"Source: JSearch · query '{query}'")
    filters = [
        f"country {country}" if country else "",
        f"posted {date_posted}" if date_posted and date_posted != "all" else "",
        "remote only" if remote_only else "",
    ]
    active = [item for item in filters if item]
    if active:
        click.echo(f"Filters: {', '.join(active)}")
    click.echo(f"Requests: {page.requests} · {used}/{jsearch.MONTHLY_LIMIT} used this month")
    click.echo(f"Extracted {len(page.jobs)} · {len(new)} new · {len(tracked)} already in database")
    for job in new:
        click.echo(f"  {_jsearch_line(job)}")
        click.echo(f"    {job.url}")
    if flagged and not force:
        click.echo(f"Flagged ({len(flagged)}) — pass --force to add:")
        for job in flagged:
            click.echo(f"  {_jsearch_line(job)} — {'; '.join(job.flags)}")
    if add_new:
        _report_summary(added, skipped, [], False, similar)
    elif new:
        click.echo("Tip: add --add to store the new positions, or --json for the full list.")


def _status_breakdown(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    order = db.JOB_STATUSES
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
        if result.query is not None:
            payload["query"] = result.query
        if result.fetched_url is not None and result.fetched_url != source.url:
            payload["fetched_url"] = result.fetched_url
        warning = discovery.source_warning(source)
        if warning is not None:
            payload["warning"] = warning
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
    if result.query is not None:
        click.echo(f"Query: {result.query} → {result.fetched_url}")
    warning = discovery.source_warning(source)
    if warning is not None:
        click.echo(f"Warning: {warning}")
    breakdown = _status_breakdown(result.status_counts)
    click.echo(
        f"Extracted {len(result.candidates)} links · {result.new_count} new"
        f" · {result.known_count} already in database{breakdown}"
    )
    for candidate in result.candidates:
        if candidate.get("known"):
            continue
        title = candidate.get("title")
        url = str(candidate["url"])
        click.echo(f"  {title} — {url}" if title else f"  {url}")
    if add_new:
        _report_summary(added, skipped, errors, False, similar)
    elif result.new_count:
        click.echo(
            "Tip: add --add to ingest all new positions, or pass URLs to `cursustrace scan`."
        )
