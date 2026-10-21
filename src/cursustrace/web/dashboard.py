"""Dashboard page: status tabs, filters, scan, statistics, settings, and profile."""

from __future__ import annotations

from typing import cast

from nicegui import events, ui
from starlette.requests import Request

from cursustrace import db, salary
from cursustrace.web.attention_ui import _park_stale_positions, attention_counts, attention_first
from cursustrace.web.auth_pages import _change_password_dialog, _multi_user_enabled, logout
from cursustrace.web.constants import APP_TITLE, PRIMARY_STATUSES, SECONDARY_STATUSES, STATUS_TABS
from cursustrace.web.discover import _render_discover
from cursustrace.web.job_card import _render_job_card
from cursustrace.web.job_form import _add_job_manually, _job_form_dialog
from cursustrace.web.nav import app_footer, scroll_to_top_button
from cursustrace.web.profile import _render_profile_editor
from cursustrace.web.scan import _add_job_dialog
from cursustrace.web.session import current_email, current_user_id
from cursustrace.web.settings_drawer import _render_settings
from cursustrace.web.state import _apply_dark_mode
from cursustrace.web.statistics import _render_statistics


def _stage_label(status: db.JobStatus) -> str:
    """Label of a pipeline stage, e.g. '✅ Applied Positions'."""
    return next(label for stage, label, _message in STATUS_TABS if stage == status)


def dashboard_page(request: Request) -> None:
    ui.page_title(APP_TITLE)
    dark = _apply_dark_mode()
    containers: dict[db.JobStatus, ui.column] = {}
    stats_container: ui.column | None = None
    results_container: ui.column | None = None
    attention_badges: dict[db.JobStatus, ui.badge] = {}
    status_tab_elements: dict[db.JobStatus, ui.tab] = {}
    secondary_views: dict[db.JobStatus, ui.column] = {}
    more_container: ui.column | None = None
    more_button: ui.button | None = None
    more_menu: ui.menu | None = None
    status_tabs: ui.tabs | None = None
    status_panels: ui.tab_panels | None = None
    tag_filter_select: ui.select | None = None
    sort_select: ui.select | None = None
    page_size_select: ui.select | None = None
    pagination: ui.pagination | None = None
    page_label: ui.label | None = None
    filters_badge: ui.badge | None = None
    query = ""
    salary_currency = ""
    salary_min: int | None = None
    salary_max: int | None = None
    sort_order: db.JobSort = "newest"
    page_size = 25
    pages: dict[str, int] = {}
    more_status: db.JobStatus | None = None
    requested_tag = (request.query_params.get("tag") or "").strip()
    canonical_tag = next(
        (
            name
            for name in db.list_tags(user_id=current_user_id())
            if requested_tag and name.casefold() == requested_tag.casefold()
        ),
        "",
    )
    tag_filter: list[str] = [canonical_tag] if canonical_tag else []

    def active_context() -> str:
        if query:
            return "search"
        if more_status is not None:
            return more_status
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
            jobs = attention_first(jobs)
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

    def select_secondary(status: db.JobStatus) -> None:
        """Show a stage that lives behind the More menu."""
        nonlocal more_status
        more_status = status
        if more_menu is not None:
            more_menu.close()
        refresh()

    def show_primary() -> None:
        """Return to the primary stage tabs."""
        nonlocal more_status
        more_status = None
        refresh()

    def on_primary_tab_click() -> None:
        """Leave the secondary view when a primary tab is clicked."""
        nonlocal more_status
        if more_status is None:
            # A normal tab switch; the value change handler refreshes the list.
            return
        more_status = None
        refresh()

    def refresh() -> None:
        _park_stale_positions()
        if tag_filter_select is not None:
            names = db.list_tags(user_id=current_user_id())
            tag_filter_select.options = names
            current = [str(name) for name in tag_filter_select.value or []]
            kept = [name for name in current if name in names]
            if kept != current:
                # Pruning fires update_tag_filter, which re-enters refresh() to render.
                tag_filter_select.set_value(kept)
                return
            tag_filter_select.update()
        searching = bool(query)
        show_panels = not searching and more_status is None
        if status_tabs is not None:
            status_tabs.set_visibility(not searching)
        if status_panels is not None:
            status_panels.set_visibility(show_panels)
        if more_container is not None:
            more_container.set_visibility(not searching and more_status is not None)
        for status, view in secondary_views.items():
            view.set_visibility(status == more_status)
        if more_button is not None:
            more_button.set_text("More" if more_status is None else _stage_label(more_status))
        stage_counts = db.job_counts(user_id=current_user_id())
        for status, tab in status_tab_elements.items():
            tab.props["label"] = f"{_stage_label(status)} ({stage_counts.get(status, 0)})"
        active = active_filter_count()
        if filters_badge is not None:
            filters_badge.set_text(str(active))
            filters_badge.set_visibility(active > 0)
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
                db.search_jobs(query, user_id=current_user_id()),
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
                    db.search_jobs(query, status=status, user_id=current_user_id()),
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
        counts = attention_counts()
        for status, badge in attention_badges.items():
            count = counts.get(status, 0)
            badge.set_text(str(count))
            badge.set_visibility(count > 0)

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
            discover_tab = ui.tab("🔍 Discover")
        with ui.element("div").classes("flex-1 flex justify-end min-w-0 items-center gap-2"):
            if _multi_user_enabled():
                email = current_email()
                if email:
                    ui.label(email).classes("text-caption text-grey-4 truncate max-w-48").mark(
                        "header-email"
                    )
                change_password_dialog = _change_password_dialog()
                ui.button("Change password", on_click=change_password_dialog.open).props(
                    "flat color=white"
                ).mark("change-password-button")
                ui.button("Logout", on_click=logout).props("flat color=white").mark("logout-button")
            settings_button = (
                ui.button(icon="settings").props("flat color=white").mark("settings-button")
            )

    with ui.right_drawer(value=False).props("width=480") as drawer:
        settings_button.on_click(lambda: drawer.toggle())
        _render_settings(refresh, dark)

    with ui.left_drawer(value=False).props("width=360") as filters_drawer:
        filters_drawer.mark("filters-drawer")
        ui.label("Filters").classes("text-h6")
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
                    db.list_tags(user_id=current_user_id()),
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

        tag_filter_select.on_value_change(lambda _: update_tag_filter())

        ui.separator()
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

    def active_filter_count() -> int:
        """Number of filters narrowing the list (salary and tags)."""
        return (
            int(bool(salary_currency))
            + int(salary_min is not None)
            + int(salary_max is not None)
            + int(bool(tag_filter))
        )

    if tag_filter:
        # A deep-linked tag filter opens the panel so the applied filter is visible.
        filters_drawer.set_value(True)

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

    sort_select.on_value_change(update_sort)
    page_size_select.on_value_change(update_page_size)

    add_dialog = _job_form_dialog(
        None,
        lambda values: _add_job_manually(values, refresh),
    )
    add_job_dialog = _add_job_dialog(refresh, add_dialog)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center gap-3"):
            ui.button("➕ Add Job", on_click=add_job_dialog.open).props("color=primary").mark(
                "add-job"
            )
            search_input = (
                ui.input("Search company", placeholder="e.g. Adapty")
                .props("clearable debounce=300")
                .classes("flex-1 min-w-0")
            )
            filters_button = ui.button("Filters", icon="tune").props("flat").mark("filters-button")
            with filters_button:
                filters_badge = ui.badge("").props("color=negative")
                filters_badge.set_visibility(False)
                filters_badge.mark("filters-badge")

            def on_search(event: events.ValueChangeEventArguments[str | None]) -> None:
                nonlocal query
                query = event.value or ""
                pages.clear()
                refresh()

            search_input.on_value_change(on_search)
            filters_button.on_click(lambda: filters_drawer.toggle())

            def apply_tag_filter(name: str) -> None:
                nonlocal query
                if query:
                    query = ""
                    search_input.value = ""
                filters_drawer.set_value(True)
                if tag_filter_select is not None:
                    tag_filter_select.set_value([name])

        with ui.tab_panels(main_tabs, value=dashboard_tab).classes("w-full"):
            with ui.tab_panel(dashboard_tab):
                with ui.row().classes("w-full items-center gap-2"):
                    with ui.tabs().classes("flex-1 min-w-0") as status_tabs:
                        for status, label, _message in STATUS_TABS:
                            if status not in PRIMARY_STATUSES:
                                continue
                            tab = ui.tab(status, label=label)
                            tab.mark(f"status-tab-{status}")
                            tab.on("click", on_primary_tab_click)
                            status_tab_elements[status] = tab
                            with tab:
                                badge = ui.badge("").props("color=negative")
                                badge.set_visibility(False)
                                badge.mark(f"attention-badge-{status}")
                                attention_badges[status] = badge
                    more_button = (
                        ui.button("More", icon="more_horiz").props("flat").mark("more-stages")
                    )
                    with more_button:
                        # The menu must live inside its trigger: Quasar opens a QMenu
                        # when its parent is clicked and anchors it to that parent.
                        more_menu = ui.menu().props("auto-close")
                        more_menu.mark("more-menu")
                        with more_menu:
                            for status, label, _message in STATUS_TABS:
                                if status in SECONDARY_STATUSES:
                                    item = ui.item(
                                        label, on_click=lambda s=status: select_secondary(s)
                                    )
                                    item.mark(f"more-stage-{status}")
                with ui.tab_panels(status_tabs, value="unapplied").classes(
                    "w-full"
                ) as status_panels:
                    for status, _label, _message in STATUS_TABS:
                        if status not in PRIMARY_STATUSES:
                            continue
                        with ui.tab_panel(status):
                            containers[status] = ui.column().classes("w-full")
                with ui.column().classes("w-full") as more_container:
                    more_container.set_visibility(False)
                    more_container.mark("secondary-view")
                    for status, label, _message in STATUS_TABS:
                        if status not in SECONDARY_STATUSES:
                            continue
                        with ui.column().classes("w-full gap-2") as view:
                            view.set_visibility(False)
                            view.mark(f"secondary-view-{status}")
                            secondary_views[status] = view
                            ui.label(label).classes("text-h6")
                            containers[status] = ui.column().classes("w-full")
                results_container = ui.column().classes("w-full")
                with ui.row().classes("w-full items-center gap-3"):
                    pagination = ui.pagination(min=1, max=1, direction_links=True).mark(
                        "pagination"
                    )
                    page_label = ui.label().mark("page-info")
                pagination.on_value_change(on_page_change)
                status_tabs.on_value_change(lambda _: show_primary())
            with ui.tab_panel(stats_tab):
                stats_container = ui.column().classes("w-full")
            with ui.tab_panel(profile_tab):
                _render_profile_editor()
            with ui.tab_panel(discover_tab):
                _render_discover(refresh)

        scroll_to_top_button()

    app_footer()

    refresh()
