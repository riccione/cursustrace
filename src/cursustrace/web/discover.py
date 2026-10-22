"""Discover tab: search a job source for listings and add the ones you pick."""

from __future__ import annotations

import logging
from collections.abc import Callable

from nicegui import events, ui
from nicegui.run import io_bound

from cursustrace import discovery
from cursustrace.errors import ScrapeError
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

    board_options = {
        str(source.site): _board_label(source)
        for source in discovery.list_sources()
        if source.site is not None
    }

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
                "Limit",
                value=discovery.DEFAULT_LIMIT,
                min=1,
                max=discovery.MAX_LIMIT,
                step=10,
            )
            .classes("w-28")
            .mark("discover-limit")
        )

    with ui.row().classes("w-full items-center gap-3"):
        run_button = ui.button("Discover").mark("discover-run")
        add_button = ui.button("Add selected").props("color=primary").mark("discover-add-selected")
        add_button.enabled = False
        clear_button = ui.button("Clear").props("flat").mark("discover-clear")
        status_label = ui.label()

    results = ui.column().classes("w-full gap-1")
    checkboxes: dict[str, ui.checkbox] = {}

    def choose_board(event: events.ValueChangeEventArguments[str | None]) -> None:
        if event.value:
            source_input.value = event.value

    board_select.on_value_change(choose_board)

    def render_results(result: discovery.DiscoveryResult) -> None:
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

    async def run_discover() -> None:
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
        chosen = [url for url, box in checkboxes.items() if box.value]
        if not chosen:
            ui.notify("Select at least one candidate to add.", type="warning")
            return
        await run_scan(chosen, refresh, status_label, add_button)
        await run_discover()

    def clear() -> None:
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
