"""JSearch section of the Discover tab: key setup, search, and direct adds."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from nicegui import ui
from nicegui.run import io_bound

from cursustrace import config, db, jsearch
from cursustrace.errors import JSearchError
from cursustrace.utils import clean_url
from cursustrace.web import state
from cursustrace.web.attention_ui import _similar_summary
from cursustrace.web.session import current_user_id
from cursustrace.web.state import _similar_notice_enabled

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SearchParams:
    query: str
    country: str | None
    date_posted: str | None
    remote_only: bool


def _current_settings() -> config.Settings:
    return state._settings if state._settings is not None else config.load_settings()


def _salary_caption(job: jsearch.JSearchJob) -> str:
    if job.salary_note:
        return job.salary_note
    amounts = [value for value in (job.salary_min, job.salary_max) if value is not None]
    if not amounts:
        return ""
    low, high = min(amounts), max(amounts)
    amount = f"{low:,} – {high:,}" if low != high else f"{low:,}"
    currency = f"{job.salary_currency} " if job.salary_currency else ""
    period = f" / {job.salary_period}" if job.salary_period else ""
    return f"{currency}{amount}{period}"


def _job_caption(job: jsearch.JSearchJob) -> str:
    parts = [_salary_caption(job)]
    if job.posted_at:
        parts.append(f"posted {job.posted_at[:10]}")
    return " · ".join(part for part in parts if part)


def _render_jsearch(refresh: Callable[[], None]) -> None:
    """Render the experimental JSearch search inside the Discover tab."""
    with (
        ui.expansion("JSearch search", icon="travel_explore")
        .classes("w-full")
        .mark("jsearch-panel")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.label(
                "Search live job postings through JSearch and add them without scanning."
            ).classes("text-caption")
            ui.chip("experimental").props("dense color=orange")

        settings = _current_settings()
        user_id = current_user_id()
        can_edit = db.is_admin(user_id)
        pages: list[jsearch.JSearchPage] = []
        page_index = 0
        params: _SearchParams | None = None
        selected: set[str] = set()
        index: jsearch.KnownIndex | None = None

        key_container = ui.column().classes("w-full gap-1")
        usage_label = ui.label().classes("text-caption ct-text-secondary").mark("jsearch-usage")

        def update_usage() -> None:
            used = jsearch.requests_this_month()
            usage_label.set_text(f"{used} / {jsearch.MONTHLY_LIMIT} requests used this month")
            usage_label.classes(
                replace=(
                    "text-caption text-warning" if used >= 180 else "text-caption ct-text-secondary"
                )
            )

        def save_key(key_input: ui.input) -> None:
            try:
                jsearch.save_api_key(key_input.value or "")
            except JSearchError as exc:
                ui.notify(str(exc), type="negative")
                return
            ui.notify("JSearch API key saved.", type="positive")
            render_key()

        def remove_key() -> None:
            jsearch.remove_api_key()
            ui.notify("JSearch API key removed.", type="positive")
            render_key()

        def render_key() -> None:
            key_container.clear()
            resolved = jsearch.api_key(settings)
            with key_container:
                if resolved is not None:
                    key, source = resolved
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("lock").classes("ct-text-secondary")
                        ui.label(f"API key {jsearch.mask_key(key)} · {source}").mark(
                            "jsearch-key-status"
                        )
                    if source == "saved key" and can_edit:
                        ui.button("Remove key", on_click=remove_key).props("flat dense").mark(
                            "jsearch-key-remove"
                        )
                    return
                if not can_edit:
                    ui.label("Ask the administrator to configure a JSearch API key.").classes(
                        "text-caption"
                    )
                    return
                key_input = (
                    ui.input(
                        "JSearch API key",
                        password=True,
                        password_toggle_button=True,
                    )
                    .classes("w-full")
                    .mark("jsearch-key-input")
                )
                ui.button("Save key", on_click=lambda: save_key(key_input)).mark("jsearch-key-save")
                ui.label("Get a free key at openwebninja.com").classes("text-caption")

        with ui.row().classes("w-full items-center gap-3"):
            query_input = (
                ui.input("Query", placeholder="python developer in Belgrade")
                .classes("flex-1 min-w-64")
                .mark("jsearch-query")
            )
            country_input = (
                ui.select(
                    jsearch.country_options(),
                    value=jsearch.DEFAULT_COUNTRY,
                    label="Country",
                    with_input=True,
                )
                .classes("w-56")
                .mark("jsearch-country")
            )
            date_select = (
                ui.select(
                    list(jsearch.DATE_POSTED_OPTIONS),
                    value="all",
                    label="Posted",
                )
                .classes("w-32")
                .mark("jsearch-date")
            )
            remote_switch = ui.checkbox("Remote only").mark("jsearch-remote")

        with ui.row().classes("w-full items-center gap-3"):
            run_button = ui.button("Search JSearch").mark("jsearch-run")
            add_button = (
                ui.button("Add selected").props("color=primary").mark("jsearch-add-selected")
            )
            add_button.enabled = False
            prev_button = ui.button("Prev").props("flat").mark("jsearch-prev")
            page_label = ui.label().classes("text-caption").mark("jsearch-page")
            next_button = ui.button("Next page").props("flat").mark("jsearch-next")
            show_known = ui.switch("Show known", value=False).mark("jsearch-show-known")
            status_label = ui.label().classes("text-caption")
        prev_button.set_visibility(False)
        page_label.set_visibility(False)
        next_button.set_visibility(False)
        ui.label(
            f"{jsearch.RESULTS_PER_PAGE} per page · going back is free; "
            "each new page costs one request."
        ).classes("text-caption ct-text-secondary")

        summary = ui.label().classes("font-bold").mark("jsearch-summary")
        results = ui.column().classes("w-full gap-1")

        def update_add_button() -> None:
            count = len(selected)
            add_button.set_text(f"Add selected ({count})" if count else "Add selected")
            add_button.enabled = count > 0

        def toggle_selection(key: str, checked: bool) -> None:
            if checked:
                selected.add(key)
            else:
                selected.discard(key)
            update_add_button()

        def update_pager() -> None:
            has_pages = bool(pages)
            prev_button.set_visibility(has_pages)
            page_label.set_visibility(has_pages)
            next_button.set_visibility(has_pages)
            prev_button.enabled = has_pages and page_index > 0
            if not has_pages:
                page_label.set_text("")
                next_button.enabled = False
                return
            page_label.set_text(f"Page {page_index + 1}")
            cached_next = page_index + 1 < len(pages)
            can_fetch = pages[-1].cursor is not None
            next_button.enabled = cached_next or can_fetch
            if cached_next:
                next_button.set_text("Next page")
            elif can_fetch:
                next_button.set_text("Next page (1 request)")
            else:
                next_button.set_text("Next page")

        def store_page(page: jsearch.JSearchPage) -> None:
            pages.append(page)
            known = index if index is not None else jsearch.known_index(user_id)
            new, _tracked = jsearch.partition(page.jobs, index=known)
            for job in new:
                if not job.flags:
                    selected.add(clean_url(job.url))

        def render_results() -> None:
            results.clear()
            if not pages:
                summary.set_text("")
                with results:
                    ui.label("No results yet.").classes("text-caption").mark("jsearch-empty")
                update_add_button()
                update_pager()
                return
            current = pages[page_index].jobs
            new, tracked = jsearch.partition(current, index=index, user_id=user_id)
            summary.set_text(
                f"Page {page_index + 1} · {len(new)} new · {len(tracked)} already tracked"
            )
            with results:
                if not new:
                    ui.label("No new positions on this page.").classes("text-caption")
                for job in new:
                    key = clean_url(job.url)
                    with ui.row().classes("w-full items-start gap-2"):
                        box = ui.checkbox(value=key in selected).mark("jsearch-candidate")
                        box.on_value_change(
                            lambda event, job_key=key: toggle_selection(job_key, bool(event.value))
                        )
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(job.title or "(untitled)").classes("font-bold")
                                if job.flags:
                                    ui.icon("warning").classes("text-warning").tooltip(
                                        "; ".join(job.flags)
                                    )
                                ui.chip(job.publisher or "JSearch").props("dense color=grey")
                            details = " · ".join(
                                part for part in (job.company, job.location) if part
                            )
                            if details:
                                ui.label(details).classes("text-caption")
                            caption = _job_caption(job)
                            if caption:
                                ui.label(caption).classes("text-caption ct-text-secondary")
                            ui.link(job.url, job.url, new_tab=True).classes("text-caption truncate")
                if show_known.value and tracked:
                    for item in tracked:
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.icon("check_circle").classes("ct-text-secondary")
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(item.title or item.job.title or "(untitled)").classes(
                                    "text-caption"
                                )
                                ui.label(item.job.url).classes(
                                    "text-caption ct-text-secondary truncate"
                                )
                            ui.chip(item.status).props("dense color=grey").mark("jsearch-known")
            update_add_button()
            update_pager()

        async def run_search() -> None:
            nonlocal params, page_index, index
            resolved = jsearch.api_key(settings)
            if resolved is None:
                ui.notify("Configure a JSearch API key first.", type="warning")
                return
            term = (query_input.value or "").strip()
            if not term:
                ui.notify("Enter a search query.", type="warning")
                return
            search_params = _SearchParams(
                query=term,
                country=country_input.value or None,
                date_posted=date_select.value,
                remote_only=bool(remote_switch.value),
            )
            run_button.enabled = False
            status_label.set_text("Searching JSearch…")
            try:
                page = await io_bound(
                    jsearch.search,
                    term,
                    api_key=resolved[0],
                    country=search_params.country,
                    date_posted=search_params.date_posted,
                    remote_only=search_params.remote_only,
                )
            except JSearchError as exc:
                ui.notify(str(exc), type="negative")
                return
            finally:
                run_button.enabled = True
                status_label.set_text("")
                update_usage()
            if page is None:
                ui.notify("Search was cancelled.", type="warning")
                return
            params = search_params
            pages.clear()
            page_index = 0
            selected.clear()
            index = jsearch.known_index(user_id)
            store_page(page)
            render_results()

        async def go_next() -> None:
            nonlocal page_index
            if not pages:
                return
            if page_index + 1 < len(pages):
                page_index += 1
                render_results()
                return
            cursor = pages[-1].cursor
            if cursor is None or params is None:
                return
            resolved = jsearch.api_key(settings)
            if resolved is None:
                ui.notify("Configure a JSearch API key first.", type="warning")
                return
            next_button.enabled = False
            status_label.set_text("Loading next page…")
            try:
                page = await io_bound(
                    jsearch.search,
                    params.query,
                    api_key=resolved[0],
                    country=params.country,
                    date_posted=params.date_posted,
                    remote_only=params.remote_only,
                    cursor=cursor,
                )
            except JSearchError as exc:
                ui.notify(str(exc), type="negative")
                return
            finally:
                status_label.set_text("")
                update_usage()
                update_pager()
            if page is None:
                ui.notify("Loading the next page was cancelled.", type="warning")
                return
            store_page(page)
            page_index += 1
            render_results()

        def go_prev() -> None:
            nonlocal page_index
            if page_index > 0:
                page_index -= 1
                render_results()

        async def add_selected() -> None:
            nonlocal index
            by_key = {clean_url(job.url): job for page in pages for job in page.jobs}
            chosen = [by_key[key] for key in selected if key in by_key]
            if not chosen:
                ui.notify("Select at least one position to add.", type="warning")
                return
            add_button.enabled = False
            status_label.set_text("Adding…")
            try:
                outcome = await io_bound(jsearch.add_jobs, chosen, user_id=user_id)
            finally:
                status_label.set_text("")
                update_add_button()
            if outcome is None:
                ui.notify("Adding was cancelled.", type="warning")
                return
            saved, duplicates, similar = outcome
            refresh()
            for job in chosen:
                selected.discard(clean_url(job.url))
            index = jsearch.known_index(user_id)
            render_results()
            message = f"Added {saved}"
            if duplicates:
                message += f" · skipped {duplicates} duplicate{'s' if duplicates != 1 else ''}"
            ui.notify(message, type="positive" if saved else "warning")
            if similar and _similar_notice_enabled():
                flat = [job for matches in similar for job in matches]
                ui.notify(
                    f"{len(similar)} look similar: {_similar_summary(flat)}",
                    type="info",
                )

        run_button.on_click(run_search)
        add_button.on_click(add_selected)
        prev_button.on_click(go_prev)
        next_button.on_click(go_next)
        show_known.on_value_change(lambda _: render_results())
        render_key()
        update_usage()
        update_add_button()
        update_pager()
