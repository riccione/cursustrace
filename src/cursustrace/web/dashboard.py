"""Dashboard page: status tabs, filters, scan, statistics, settings, and profile."""

from __future__ import annotations

from typing import cast

from nicegui import events, ui
from starlette.requests import Request

from cursustrace import db, salary
from cursustrace.web.attention_ui import _park_stale_positions, _render_attention_banner
from cursustrace.web.constants import APP_TITLE, STATUS_TABS
from cursustrace.web.job_card import _render_job_card
from cursustrace.web.job_form import _add_job_manually, _job_form_dialog
from cursustrace.web.profile import _render_profile_editor
from cursustrace.web.scan import _handle_scan
from cursustrace.web.settings_drawer import _render_settings
from cursustrace.web.state import _apply_dark_mode
from cursustrace.web.statistics import _render_statistics


def dashboard_page(request: Request) -> None:
    ui.page_title(APP_TITLE)
    dark = _apply_dark_mode()
    containers: dict[db.JobStatus, ui.column] = {}
    stats_container: ui.column | None = None
    results_container: ui.column | None = None
    attention_container: ui.column | None = None
    status_tabs: ui.tabs | None = None
    status_panels: ui.tab_panels | None = None
    advanced_bar: ui.expansion | None = None
    tag_filter_select: ui.select | None = None
    sort_select: ui.select | None = None
    pagination: ui.pagination | None = None
    page_label: ui.label | None = None
    query = ""
    salary_currency = ""
    salary_min: int | None = None
    salary_max: int | None = None
    sort_order: db.JobSort = "newest"
    page_size = 25
    pages: dict[str, int] = {}
    requested_tag = (request.query_params.get("tag") or "").strip()
    canonical_tag = next(
        (
            name
            for name in db.list_tags()
            if requested_tag and name.casefold() == requested_tag.casefold()
        ),
        "",
    )
    tag_filter: list[str] = [canonical_tag] if canonical_tag else []

    def active_context() -> str:
        if query:
            return "search"
        value = status_tabs.value if status_tabs is not None else None
        return value if isinstance(value, str) else "unapplied"

    def render_page(
        container: ui.column,
        jobs: list[db.Job],
        empty_message: str,
        context: str,
        *,
        show_status: bool = False,
    ) -> tuple[int, int, int]:
        """Render one page of jobs; return (page_count, page, total)."""
        container.clear()
        with container:
            if not jobs:
                ui.label(empty_message)
                return (1, 1, 0)
            if page_size > 0:
                page_count = (len(jobs) + page_size - 1) // page_size
                page = min(pages.get(context, 1), page_count)
                pages[context] = page
                start = (page - 1) * page_size
                page_jobs = jobs[start : start + page_size]
            else:
                page_count, page, page_jobs = 1, 1, jobs
            if page_jobs:
                grouped = db.tags_for_jobs([entry["id"] for entry in page_jobs])
            else:
                grouped = {}
            for job in page_jobs:
                _render_job_card(
                    job,
                    refresh,
                    show_status=show_status,
                    tags=grouped.get(job["id"], []),
                    on_tag_click=apply_tag_filter,
                )
        return (page_count, page, len(jobs))

    def refresh() -> None:
        _park_stale_positions()
        if tag_filter_select is not None:
            names = db.list_tags()
            tag_filter_select.options = names
            current = [str(name) for name in tag_filter_select.value or []]
            kept = [name for name in current if name in names]
            if kept != current:
                # Pruning fires update_tag_filter, which re-enters refresh() to render.
                tag_filter_select.set_value(kept)
                return
            tag_filter_select.update()
        searching = bool(query)
        if status_tabs is not None:
            status_tabs.set_visibility(not searching)
        if status_panels is not None:
            status_panels.set_visibility(not searching)
        if advanced_bar is not None:
            advanced_bar.set_visibility(not searching)
            salary_active = (
                salary_min is not None or salary_max is not None or bool(salary_currency)
            )
            active = int(salary_active) + int(bool(tag_filter))
            advanced_bar.text = (
                f"Advanced filters ({active} active)" if active else "Advanced filters"
            )
        if sort_select is not None:
            sort_select.set_visibility(not searching)
        for status, _label, _empty in STATUS_TABS:
            container = containers.get(status)
            if container is not None:
                container.clear()
        if results_container is not None:
            results_container.clear()
            results_container.set_visibility(searching)
        context = "search" if searching else active_context()
        page_count, page, total = 1, 1, 0
        if searching and results_container is not None:
            page_count, page, total = render_page(
                results_container,
                db.search_jobs(query),
                f'No positions match "{query}".',
                "search",
                show_status=True,
            )
        if not searching:
            for status, _label, empty_message in STATUS_TABS:
                container = containers.get(status)
                if container is None:
                    continue
                jobs = salary.filter_by_salary(
                    db.search_jobs(query, status=status),
                    min_annual=salary_min,
                    max_annual=salary_max,
                    currency=salary_currency or None,
                )
                jobs = db.filter_by_tags(jobs, tag_filter)
                jobs = db.sort_jobs(jobs, sort_order)
                rendered = render_page(container, jobs, empty_message, status)
                if status == context:
                    page_count, page, total = rendered
        if pagination is not None and page_label is not None:
            pagination.max = max(page_count, 1)
            pagination.set_visibility(page_size > 0 and total > 0 and page_count > 1)
            if pagination.value != page:
                pagination.value = page
            page_label.set_visibility(total > 0)
            if page_size <= 0:
                page_label.set_text(f"Showing all {total}")
            else:
                first = (page - 1) * page_size + 1
                last = min(page * page_size, total)
                page_label.set_text(f"Showing {first}-{last} of {total}")
        if stats_container is not None:
            stats_container.clear()
            with stats_container:
                _render_statistics(dark.value is True)
        if attention_container is not None:
            _render_attention_banner(attention_container)

    with ui.header().classes("items-center flex-nowrap"):
        ui.label(APP_TITLE).classes("text-h6 flex-1 truncate min-w-0")
        with (
            ui.tabs()
            .classes("header-tabs text-grey-4")
            .props('active-color="white" indicator-color="white"') as main_tabs
        ):
            dashboard_tab = ui.tab("📋 Dashboard")
            stats_tab = ui.tab("📊 Statistics")
            profile_tab = ui.tab("👤 Profile & CV Editor")
        with ui.element("div").classes("flex-1 flex justify-end min-w-0"):
            settings_button = (
                ui.button(icon="settings").props("flat color=white").mark("settings-button")
            )

    with ui.right_drawer(value=False).props("width=480") as drawer:
        settings_button.on_click(lambda: drawer.toggle())
        _render_settings(refresh, dark)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.column().classes("w-full gap-2"):
            urls_input = (
                ui.textarea(
                    "Job URLs (one per line)",
                    placeholder="https://example.com/job/1\nhttps://example.com/job/2",
                )
                .classes("w-full")
                .props('autogrow input-style="min-height: 80px"')
                .mark("scan-urls")
            )
            with ui.row().classes("w-full items-center gap-3"):
                scan_button = ui.button("Scan & Save Positions")
                status_label = ui.label()
            add_dialog = _job_form_dialog(
                None,
                lambda values: _add_job_manually(values, refresh),
            )
            scan_button.on_click(
                lambda: _handle_scan(urls_input, refresh, status_label, scan_button)
            )
            ui.button("➕ Add Manually", on_click=add_dialog.open).props("flat color=primary")

        with ui.tab_panels(main_tabs, value=dashboard_tab).classes("w-full"):
            with ui.tab_panel(dashboard_tab):
                attention_container = ui.column().classes("w-full")
                search_input = (
                    ui.input("Search company", placeholder="e.g. Adapty")
                    .props("clearable debounce=300")
                    .classes("w-full")
                )

                def on_search(event: events.ValueChangeEventArguments[str | None]) -> None:
                    nonlocal query
                    query = event.value or ""
                    pages.clear()
                    refresh()

                search_input.on_value_change(on_search)
                advanced_bar = (
                    ui.expansion(
                        "Advanced filters",
                        icon="tune",
                        value=bool(tag_filter),
                    )
                    .classes("w-full")
                    .mark("advanced-filters")
                )
                with advanced_bar:
                    with ui.row().classes("w-full items-center gap-2"):
                        currency_filter = ui.select(
                            ["", *salary.CURRENCIES], value="", label="Salary currency"
                        ).classes("w-48")
                        min_filter = ui.number("Min salary", min=0, step=1000).classes("w-40")
                        max_filter = ui.number("Max salary", min=0, step=1000).classes("w-40")

                    def update_salary_filter() -> None:
                        nonlocal salary_currency, salary_min, salary_max
                        salary_currency = currency_filter.value or ""
                        salary_min = int(min_filter.value) if min_filter.value is not None else None
                        salary_max = int(max_filter.value) if max_filter.value is not None else None
                        pages.clear()
                        refresh()

                    currency_filter.on_value_change(lambda _: update_salary_filter())
                    min_filter.on_value_change(lambda _: update_salary_filter())
                    max_filter.on_value_change(lambda _: update_salary_filter())

                    with ui.row().classes("w-full items-center gap-2").mark("tag-filter-row"):
                        tag_filter_select = (
                            ui.select(
                                db.list_tags(),
                                value=tag_filter,
                                label="Filter by tag",
                                multiple=True,
                                clearable=True,
                            )
                            .classes("w-96")
                            .mark("tag-filter")
                        )

                    def update_tag_filter() -> None:
                        nonlocal tag_filter
                        if tag_filter_select is None:
                            return
                        new = [str(name) for name in tag_filter_select.value or []]
                        if new == tag_filter:
                            return
                        tag_filter = new
                        pages.clear()
                        refresh()

                    def apply_tag_filter(name: str) -> None:
                        nonlocal query
                        if query:
                            query = ""
                            search_input.value = ""
                        if advanced_bar is not None:
                            advanced_bar.open()
                        if tag_filter_select is not None:
                            tag_filter_select.set_value([name])

                    tag_filter_select.on_value_change(lambda _: update_tag_filter())

                def update_sort(event: events.ValueChangeEventArguments[str | None]) -> None:
                    nonlocal sort_order
                    sort_order = cast("db.JobSort", event.value or "newest")
                    pages.clear()
                    refresh()

                def update_page_size(event: events.ValueChangeEventArguments[int | None]) -> None:
                    nonlocal page_size
                    if event.value is not None:
                        page_size = int(event.value)
                    pages.clear()
                    refresh()

                def on_page_change(event: events.ValueChangeEventArguments[int | None]) -> None:
                    context = active_context()
                    page = event.value or 1
                    if pages.get(context, 1) == page:
                        return
                    pages[context] = page
                    refresh()

                with ui.row().classes("w-full items-center gap-3"):
                    sort_select = (
                        ui.select(
                            {
                                "newest": "Newest first",
                                "oldest": "Oldest first",
                                "company": "Company A-Z",
                                "company_desc": "Company Z-A",
                                "status": "Status",
                            },
                            value="newest",
                            label="Sort by",
                        )
                        .classes("w-56")
                        .mark("sort-select")
                    )
                    page_size_select = (
                        ui.select(
                            {10: "10 per page", 25: "25 per page", 50: "50 per page", 0: "All"},
                            value=25,
                            label="Rows per page",
                        )
                        .classes("w-48")
                        .mark("page-size")
                    )
                    pagination = ui.pagination(min=1, max=1, direction_links=True).mark(
                        "pagination"
                    )
                    page_label = ui.label().mark("page-info")
                sort_select.on_value_change(update_sort)
                page_size_select.on_value_change(update_page_size)
                pagination.on_value_change(on_page_change)
                with ui.tabs().classes("w-full") as status_tabs:
                    for status, label, _message in STATUS_TABS:
                        ui.tab(status, label=label)
                with ui.tab_panels(status_tabs, value="unapplied").classes(
                    "w-full"
                ) as status_panels:
                    for status, _label, _message in STATUS_TABS:
                        with ui.tab_panel(status):
                            containers[status] = ui.column().classes("w-full")
                results_container = ui.column().classes("w-full")
                status_tabs.on_value_change(lambda _: refresh())
            with ui.tab_panel(stats_tab):
                stats_container = ui.column().classes("w-full")
            with ui.tab_panel(profile_tab):
                _render_profile_editor()

        refresh()
