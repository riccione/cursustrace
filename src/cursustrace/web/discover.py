"""Discover tab: search a job source for listings and add the ones you pick."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace

from nicegui import events, ui
from nicegui.run import io_bound

from cursustrace import discovery
from cursustrace.errors import ScrapeError
from cursustrace.web.jsearch_ui import _render_jsearch
from cursustrace.web.scan import run_scan
from cursustrace.web.session import current_user_id

logger = logging.getLogger(__name__)


def _board_label(source: discovery.SourceRef) -> str:
    return f"{source.site} ({source.site_status})" if source.site_status else str(source.site)


def _render_discover(refresh: Callable[[], None]) -> None:
    """Render the discover form, candidate list, and add-selected action."""
    with ui.row().classes("items-center gap-2"):
        ui.icon("search").classes("ct-heading-icon")
        ui.label("Discover positions").classes("text-h5")
    ui.label(
        "Search a job board or aggregator for listings, then pick the ones to add. "
        "Enter a full URL or a board name from job-sources/."
    ).classes("text-caption")

    all_sources = discovery.list_sources()
    live_count = sum(1 for source in all_sources if source.site_status == "live")
    board_options = {
        discovery.ALL_SOURCES: f"All known boards ({live_count} live / {len(all_sources)})"
    }
    board_options.update(
        {
            str(source.site): _board_label(source)
            for source in all_sources
            if source.site is not None
        }
    )

    with ui.row().classes("w-full items-center gap-3"):
        source_input = (
            ui.input("Source URL or board name").classes("flex-1 min-w-64").mark("discover-source")
        )
        board_select = (
            ui.select(board_options, value=None, label="Known board", clearable=True)
            .classes("w-64")
            .mark("discover-board")
        )
        query_input = ui.input("Query (optional)").classes("w-48").mark("discover-query")
        only_live = ui.checkbox("Only live", value=False).mark("discover-only-live")
        limit_input = (
            ui.number(
                "Limit / per board",
                value=discovery.DEFAULT_LIMIT,
                min=1,
                max=discovery.MAX_LIMIT,
                step=10,
            )
            .classes("w-28")
            .mark("discover-limit")
        )

    ui.label(
        "All boards fetches each selected board (one request per board) and stops at "
        "300 new links. Keyword search only filters server-side for boards that declare "
        "a Search template."
    ).classes("text-caption")

    with ui.row().classes("w-full items-center gap-3"):
        run_button = ui.button("Discover").mark("discover-run")
        add_button = ui.button("Add selected").props("color=primary").mark("discover-add-selected")
        add_button.enabled = False
        clear_button = ui.button("Clear").props("flat").mark("discover-clear")
        cancel_button = ui.button("Cancel").props("flat").mark("discover-cancel")
        cancel_button.set_visibility(False)
        status_label = ui.label()

    results = ui.column().classes("w-full gap-1")
    checkboxes: dict[str, ui.checkbox] = {}
    current: discovery.DiscoveryResult | discovery.DiscoveryBatch | None = None
    cancel_event: dict[str, threading.Event | None] = {"value": None}
    progress_state = {"done": 0, "total": 0, "site": ""}

    def choose_board(event: events.ValueChangeEventArguments[str | None]) -> None:
        if event.value == discovery.ALL_SOURCES:
            source_input.value = ""
            source_input.disable()
            only_live.value = True
            limit_input.value = discovery.BATCH_PER_BOARD
            return
        source_input.enable()
        if event.value:
            source_input.value = event.value

    board_select.on_value_change(choose_board)

    def poll_progress() -> None:
        if progress_state["total"]:
            status_label.set_text(
                f"Checking {progress_state['done']}/{progress_state['total']}: "
                f"{progress_state['site']}"
            )

    progress_timer = ui.timer(0.3, poll_progress, active=False)

    def cancel_discover() -> None:
        event = cancel_event["value"]
        if event is not None:
            event.set()
            status_label.set_text("Cancelling…")

    cancel_button.on_click(cancel_discover)

    def render_results(result: discovery.DiscoveryResult) -> None:
        nonlocal current
        current = result
        results.clear()
        checkboxes.clear()
        with results:
            warning = discovery.source_warning(result.source)
            if warning is not None:
                with ui.row().classes("text-warning items-center gap-1"):
                    ui.icon("warning")
                    ui.label(warning)
            label = result.source.site or result.fetched_url or result.source.url
            ui.label(f"{label} · {result.new_count} new · {result.known_count} known").classes(
                "font-bold"
            ).mark("discover-summary")
            if result.query:
                ui.label(f"Query: {result.query} → {result.fetched_url}").classes("text-caption")
            if not result.candidates:
                ui.label("No candidates found.").mark("discover-empty")
            for candidate in result.candidates:
                url = str(candidate["url"])
                if candidate.get("known"):
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.icon("check_circle").classes("ct-text-secondary")
                        ui.label(url).classes("text-caption ct-text-secondary truncate flex-1")
                        ui.chip(str(candidate.get("status", ""))).props("dense color=grey")
                else:
                    box = (
                        ui.checkbox(url, value=True)
                        .classes("text-caption w-full")
                        .mark("discover-candidate")
                    )
                    checkboxes[url] = box
        add_button.enabled = bool(checkboxes)

    def render_batch(batch: discovery.DiscoveryBatch) -> None:
        nonlocal current
        current = batch
        results.clear()
        checkboxes.clear()
        add_button.enabled = False
        with results:
            summary = (
                f"{batch.checked} boards · {batch.new_count} new"
                f" · {batch.known_count} already tracked"
            )
            if batch.skipped_not_live:
                summary += f" · {batch.skipped_not_live} not live"
            if batch.errors:
                summary += f" · {len(batch.errors)} failed"
            if batch.truncated:
                summary += " · stopped at the cap"
            if batch.cancelled:
                summary += " · cancelled"
            ui.label(summary).classes("font-bold").mark("discover-summary")
            if batch.query:
                ui.label(f"Query: {batch.query}").classes("text-caption")
            if not batch.candidates:
                ui.label("No new positions found.").mark("discover-empty")
            for candidate in batch.candidates:
                url = str(candidate["url"])
                with ui.row().classes("w-full items-center gap-2"):
                    box = (
                        ui.checkbox(url, value=True)
                        .classes("text-caption w-full")
                        .mark("discover-candidate")
                    )
                    checkboxes[url] = box
                    site = str(candidate.get("site") or "")
                    if site:
                        ui.chip(site).props("dense color=grey").mark("discover-site")
            if batch.errors:
                with (
                    ui.expansion(f"Failed boards ({len(batch.errors)})", icon="warning")
                    .classes("w-full")
                    .mark("discover-errors")
                ):
                    for item in batch.errors:
                        label = item.source.site or item.source.url
                        ui.label(f"{label}: {item.error}").classes("text-caption")
        add_button.enabled = bool(checkboxes)

    async def run_discover_all() -> None:
        sources = discovery.list_sources()
        event = threading.Event()
        cancel_event["value"] = event
        progress_state.update(done=0, total=len(sources), site="")
        run_button.enabled = False
        cancel_button.set_visibility(True)
        progress_timer.activate()
        status_label.set_text("Checking the known boards…")

        def progress(done: int, total: int, site: str) -> None:
            progress_state.update(done=done, total=total, site=site)

        try:
            batch = await io_bound(
                discovery.discover_all,
                sources,
                per_board=min(max(int(limit_input.value or discovery.BATCH_PER_BOARD), 1), 50),
                total=discovery.BATCH_TOTAL,
                query=(query_input.value or None),
                only_live=bool(only_live.value),
                user_id=current_user_id(),
                progress=progress,
                cancelled=event.is_set,
            )
        finally:
            progress_timer.deactivate()
            cancel_button.set_visibility(False)
            cancel_event["value"] = None
            run_button.enabled = True
            status_label.set_text("")
        if batch is None:
            ui.notify("Discovery was cancelled.", type="warning")
            return
        render_batch(batch)

    async def run_discover() -> None:
        if board_select.value == discovery.ALL_SOURCES:
            await run_discover_all()
            return
        raw = (source_input.value or "").strip() or (board_select.value or "")
        if not raw:
            ui.notify("Enter a source URL or pick a board.", type="warning")
            return
        try:
            source = discovery.resolve_source(raw)
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return
        run_button.enabled = False
        status_label.set_text("Discovering…")
        try:
            result = await io_bound(
                discovery.discover,
                source,
                limit=int(limit_input.value or discovery.DEFAULT_LIMIT),
                query=(query_input.value or None),
                only_live=bool(only_live.value),
                user_id=current_user_id(),
            )
        except ScrapeError as exc:
            ui.notify(str(exc), type="negative")
            return
        finally:
            run_button.enabled = True
            status_label.set_text("")
        if result is None:
            ui.notify("Discovery was cancelled.", type="warning")
            return
        render_results(result)

    async def add_selected() -> None:
        nonlocal current
        chosen = [url for url, box in checkboxes.items() if box.value]
        if not chosen:
            ui.notify("Select at least one candidate to add.", type="warning")
            return
        failed = set(await run_scan(chosen, refresh, status_label, add_button))
        if isinstance(current, discovery.DiscoveryBatch):
            kept = tuple(
                candidate for candidate in current.candidates if str(candidate["url"]) in failed
            )
            added = len(chosen) - len(failed)
            current = replace(
                current,
                candidates=kept,
                new_urls=tuple(str(candidate["url"]) for candidate in kept),
                known_count=current.known_count + added,
            )
            render_batch(current)
        else:
            await run_discover()

    def clear() -> None:
        source_input.enable()
        source_input.value = ""
        board_select.value = None
        query_input.value = ""
        only_live.value = False
        limit_input.value = discovery.DEFAULT_LIMIT
        results.clear()
        checkboxes.clear()
        add_button.enabled = False

    run_button.on_click(run_discover)
    add_button.on_click(add_selected)
    clear_button.on_click(clear)

    ui.separator()
    _render_jsearch(refresh)
