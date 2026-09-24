"""NiceGUI entry point for cursustrace."""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import FrameType
from typing import Literal

from nicegui import app, events, ui
from nicegui.run import io_bound

from cursustrace import config, db, scraper
from cursustrace.errors import PdfExportError, ScrapeError
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename, load_cv_styles
from cursustrace.validation import validate_required, validate_url

APP_TITLE = "CursusTrace — Job Application Tracker"

_settings: config.Settings | None = None


def _cv_style_path() -> Path | None:
    return _settings.cv_style_path if _settings is not None else None


STATUS_TABS: tuple[tuple[db.JobStatus, str, str], ...] = (
    ("unapplied", "⏳ Unapplied Positions", "No unapplied positions yet."),
    ("applied", "✅ Applied Positions", "No applied positions yet."),
    ("interview", "🗣️ Interview Positions", "No interview positions yet."),
    ("rejected", "❌ Rejected Positions", "No rejected positions yet."),
)

STATUS_CHECKBOXES: tuple[tuple[db.JobFlag, str], ...] = (
    ("applied", "Applied"),
    ("interview", "Interview"),
    ("rejected", "Rejected"),
)

STAT_CARDS: tuple[tuple[str, str], ...] = (
    ("total", "📋 Total positions"),
    ("unapplied", "⏳ Unapplied"),
    ("applied", "✅ Applied"),
    ("interview", "🗣️ Interview"),
    ("rejected", "❌ Rejected"),
)

GLOBAL_CSS = """
html { font-size: 22px; }
body { font-size: 22px; }
.q-btn, .q-field, .q-field__label, .q-tab__label, .q-item, .q-checkbox,
.q-notification { font-size: inherit; }
body.body--dark .q-drawer { background: #1d1d1d; }
.q-textarea .q-field__native { line-height: 1.7; }
"""

DARK_MODE_KEY = "dark_mode"
DARK_MODE_OPTIONS: dict[str, str] = {
    "light": "☀️ Light",
    "dark": "🌙 Dark",
    "system": "🖥️ System",
}


def _dark_mode_value(name: str | None) -> bool | None:
    if name == "dark":
        return True
    if name == "light":
        return False
    return None


def _dark_mode_name(value: bool | None) -> str:
    if value is None:
        return "system"
    return "dark" if value else "light"


def _apply_dark_mode() -> ui.dark_mode:
    name = db.get_setting(DARK_MODE_KEY, "system")
    return ui.dark_mode(value=_dark_mode_value(name))


_DISABLED_CHECKBOXES: dict[db.JobStatus, frozenset[db.JobFlag]] = {
    "unapplied": frozenset(),
    "applied": frozenset({"applied"}),
    "interview": frozenset({"applied", "interview"}),
    "rejected": frozenset({"applied", "interview"}),
}


def _checked_flags(job: db.Job) -> frozenset[db.JobFlag]:
    status = db.job_status(job)
    if status == "applied":
        return frozenset({"applied"})
    if status == "interview":
        return frozenset({"applied", "interview"})
    if status == "rejected":
        flags: set[db.JobFlag] = {"applied", "rejected"}
        if job["date_interview"]:
            flags.add("interview")
        return frozenset(flags)
    return frozenset()


def _stage_comment(job: db.Job, stage: db.JobFlag) -> str:
    if stage == "applied":
        return job["applied_comment"] or ""
    if stage == "interview":
        return job["interview_comment"] or ""
    return job["rejected_comment"] or ""


def _status_handler(
    job_id: int,
    status: db.JobFlag,
    refresh: Callable[[], None],
) -> Callable[[events.ValueChangeEventArguments[bool | None]], None]:
    def handler(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_job_status(job_id, status if event.value else "unapplied")
        refresh()

    return handler


def _render_status_controls(
    job: db.Job, current: db.JobStatus, refresh: Callable[[], None]
) -> None:
    checked = _checked_flags(job)
    for status, label in STATUS_CHECKBOXES:
        checkbox = ui.checkbox(
            label,
            value=status in checked,
            on_change=_status_handler(job["id"], status, refresh),
        )
        checkbox.enabled = status not in _DISABLED_CHECKBOXES[current]


def _render_comment_box(job: db.Job, current: db.JobFlag) -> None:
    comment_input = (
        ui.textarea(f"{current.title()} comment", value=_stage_comment(job, current))
        .classes("w-full")
        .props('autogrow input-style="min-height: 80px"')
    )

    def save_comment() -> None:
        db.set_job_comment(job["id"], current, comment_input.value or "")
        ui.notify("Comment saved.", type="positive")

    ui.button("💾 Save comment", on_click=save_comment).props("flat color=primary")


def _render_delete_controls(job: db.Job, refresh: Callable[[], None]) -> None:
    delete_dialog = _confirm_delete_dialog(
        job, lambda dialog: _delete_job(dialog, job["id"], refresh)
    )
    ui.button("🗑️ Delete", on_click=delete_dialog.open).props("flat color=negative")


def _days_since_applied(job: db.Job) -> int | None:
    """Return whole days since the job was marked applied, or None when it was not."""
    if not job["date_applied"]:
        return None
    try:
        parsed = time.strptime(job["date_applied"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    applied = date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    now = time.localtime()
    return (date(now.tm_year, now.tm_mon, now.tm_mday) - applied).days


def _render_job_card(
    job: db.Job, refresh: Callable[[], None], *, show_status: bool = False
) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    label = f"{title} — {company}"
    if show_status:
        label += f" [{db.job_status(job).title()}]"
    with ui.expansion(label).classes("w-full"):
        ui.markdown(f"**Location:** {job['location']}")
        ui.markdown(f"**Added:** {job['date_added']}")
        days_applied = _days_since_applied(job)
        if days_applied is not None:
            ui.label(f"Applied {days_applied} days ago").classes("text-caption").mark(
                "days-since-applied"
            )
        ui.link("Open job posting", job["job_url"], new_tab=True)
        ui.link("View full details", f"/job/{job['id']}")
        current = db.job_status(job)
        _render_status_controls(job, current, refresh)
        if current != "unapplied":
            _render_comment_box(job, current)
        _render_delete_controls(job, refresh)


def _render_job_list(
    jobs: list[db.Job],
    empty_message: str,
    refresh: Callable[[], None],
    *,
    show_status: bool = False,
) -> None:
    if not jobs:
        ui.label(empty_message)
        return
    for job in jobs:
        _render_job_card(job, refresh, show_status=show_status)


def _render_statistics() -> None:
    counts = db.job_counts()
    with ui.grid(columns=2).classes("w-full gap-4"):
        for key, label in STAT_CARDS:
            with ui.card().classes("w-full items-center"):
                ui.label(str(counts[key])).classes("text-h4").mark(f"stat-{key}")
                ui.label(label)


def _parse_urls(text: str | None) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines()]
    return list(dict.fromkeys(line for line in lines if line))


def _scan_url(url: str) -> tuple[str, str]:
    try:
        job = scraper.scrape_job(url)
    except ScrapeError as exc:
        return ("error", f"{url}: {exc}")

    duplicate, _reason = db.check_duplicate(
        url,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    job_id = db.add_job(
        url,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    if duplicate or job_id is None:
        return ("duplicate", url)
    return ("saved", url)


def _scan_summary(saved: int, duplicates: int, errors: int, failures: list[str]) -> str:
    parts: list[str] = []
    if saved:
        parts.append(f"Saved {saved}")
    if duplicates:
        parts.append(f"Duplicates {duplicates}")
    if errors:
        parts.append(f"Failed {errors}: {'; '.join(failures)}")
    return " · ".join(parts) if parts else "Nothing saved."


async def _handle_scan(
    text: str | None,
    refresh: Callable[[], None],
    status_label: ui.label,
    button: ui.button,
) -> None:
    urls = _parse_urls(text)
    if not urls:
        ui.notify("Please enter at least one job URL.", type="warning")
        return

    button.enabled = False
    saved = duplicates = errors = 0
    failures: list[str] = []
    try:
        for index, url in enumerate(urls, start=1):
            status_label.set_text(f"Scanning {index}/{len(urls)}: {url}")
            outcome = await io_bound(_scan_url, url)
            kind, message = outcome if outcome is not None else ("error", f"{url}: cancelled")
            if kind == "saved":
                saved += 1
            elif kind == "duplicate":
                duplicates += 1
            else:
                errors += 1
                failures.append(message)
    finally:
        status_label.set_text("")
        button.enabled = True

    refresh()
    notify_type: Literal["positive", "negative", "warning"] = (
        "negative" if errors else ("warning" if duplicates else "positive")
    )
    ui.notify(
        _scan_summary(saved, duplicates, errors, failures),
        type=notify_type,
    )


def _render_settings(refresh: Callable[[], None], dark: ui.dark_mode) -> None:
    ui.label("Appearance").classes("text-h6")
    theme = ui.toggle(DARK_MODE_OPTIONS, value=_dark_mode_name(dark.value)).classes("w-full")

    def update_theme(event: events.ValueChangeEventArguments[str | None]) -> None:
        name = event.value or "system"
        dark.value = _dark_mode_value(name)
        db.set_setting(DARK_MODE_KEY, name)

    theme.on_value_change(update_theme)

    ui.separator()
    ui.label("⚙️ Settings & Maintenance").classes("text-h6")
    with ui.expansion("⚠️ Danger Zone: Clear Database"):
        ui.label(
            "⚠️ This action cannot be undone. All tracked job postings and "
            "application history will be permanently erased."
        ).classes("text-negative")
        confirm = ui.input("Type 'DELETE' to confirm", placeholder="DELETE").classes("w-full")
        button = ui.button(
            "Confirm & Clear All Data",
            on_click=lambda: _clear_database(refresh),
        ).props("color=negative")
        button.enabled = False

        def update_button(event: events.ValueChangeEventArguments[str | None]) -> None:
            button.enabled = event.value == "DELETE"

        confirm.on_value_change(update_button)


def _clear_database(refresh: Callable[[], None]) -> None:
    count = db.clear_all_jobs()
    ui.notify(f"Successfully cleared {count} positions.", type="positive")
    refresh()


def _delete_job(dialog: ui.dialog, job_id: int, refresh: Callable[[], None] | None = None) -> None:
    db.delete_job(job_id)
    dialog.close()
    ui.notify("Position deleted.", type="positive")
    if refresh is not None:
        refresh()


def _delete_job_from_detail(dialog: ui.dialog, job_id: int) -> None:
    _delete_job(dialog, job_id)
    ui.navigate.to("/")


def _confirm_delete_dialog(
    job: db.Job,
    on_confirm: Callable[[ui.dialog], None],
) -> ui.dialog:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with ui.dialog() as dialog, ui.card():
        dialog.mark(f"delete-dialog-{job['id']}")
        ui.label("Delete this position?")
        ui.label(f"{title} — {company}").classes("font-bold")
        ui.label("This action cannot be undone.").classes("text-negative")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=lambda: on_confirm(dialog)).props("color=negative").mark(
                f"delete-confirm-{job['id']}"
            )
    return dialog


@dataclass(frozen=True)
class JobFormValues:
    """Values collected from the manual job form."""

    url: str
    title: str
    company: str
    location: str
    description: str
    applied_comment: str = ""
    interview_comment: str = ""
    rejected_comment: str = ""


def _job_form_dialog(
    values: db.Job | None,
    on_save: Callable[[JobFormValues], bool],
    *,
    with_comments: bool = False,
) -> ui.dialog:
    job_url = values["job_url"] if values else ""
    title = (values["title"] or "") if values else ""
    company = (values["company"] or "") if values else ""
    location = (values["location"] or "") if values else ""
    description = (values["description"] or "") if values else ""

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
        dialog.mark("job-form")
        url = ui.input("Job URL", value=job_url, validation=validate_url).classes("w-full")
        title_input = ui.input("Title", value=title, validation=validate_required("Title")).classes(
            "w-full"
        )
        company_input = ui.input(
            "Company", value=company, validation=validate_required("Company")
        ).classes("w-full")
        location_input = ui.input("Location", value=location).classes("w-full")
        description_input = (
            ui.textarea(
                "Description",
                value=description,
                validation=validate_required("Description"),
            )
            .classes("w-full")
            .props('autogrow input-style="min-height: 120px"')
        )

        comment_inputs: dict[db.JobFlag, ui.textarea] = {}
        if with_comments:
            for stage, label in STATUS_CHECKBOXES:
                comment_inputs[stage] = (
                    ui.textarea(
                        f"{label} comment",
                        value=_stage_comment(values, stage) if values else "",
                    )
                    .classes("w-full")
                    .props('autogrow input-style="min-height: 80px"')
                )

        def save() -> None:
            valid = all(
                [
                    url.validate(),
                    title_input.validate(),
                    company_input.validate(),
                    description_input.validate(),
                ]
            )
            if not valid:
                return
            result = on_save(
                JobFormValues(
                    url=(url.value or "").strip(),
                    title=(title_input.value or "").strip(),
                    company=(company_input.value or "").strip(),
                    location=(location_input.value or "").strip(),
                    description=(description_input.value or "").strip(),
                    applied_comment=(comment_inputs["applied"].value or "")
                    if with_comments
                    else "",
                    interview_comment=(comment_inputs["interview"].value or "")
                    if with_comments
                    else "",
                    rejected_comment=(comment_inputs["rejected"].value or "")
                    if with_comments
                    else "",
                )
            )
            if result:
                dialog.close()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("color=primary")
    return dialog


def _add_job_manually(values: JobFormValues, refresh: Callable[[], None]) -> bool:
    duplicate, _reason = db.check_duplicate(
        values.url, values.title, values.company, values.location or None, values.description
    )
    job_id = db.add_job(
        values.url, values.title, values.company, values.location or None, values.description
    )
    if duplicate or job_id is None:
        ui.notify("A position with the same URL or fingerprint already exists.", type="warning")
        return False
    ui.notify("Position added.", type="positive")
    refresh()
    return True


def _update_job_fields(job_id: int, values: JobFormValues) -> bool:
    duplicate, _reason = db.check_duplicate(
        values.url,
        values.title,
        values.company,
        values.location or None,
        values.description,
        exclude_id=job_id,
    )
    if duplicate or not db.update_job(
        job_id,
        values.url,
        values.title,
        values.company,
        values.location or None,
        values.description,
    ):
        ui.notify("A position with the same URL or fingerprint already exists.", type="warning")
        return False
    db.update_job_comments(
        job_id,
        values.applied_comment,
        values.interview_comment,
        values.rejected_comment,
    )
    ui.notify("Position updated.", type="positive")
    ui.navigate.to(f"/job/{job_id}")
    return True


def _render_profile_editor() -> None:
    profile = db.get_profile()
    ui.label("👤 Profile & CV Configuration").classes("text-h5")

    with ui.row().classes("w-full gap-8"):
        with ui.column().classes("flex-1 gap-2"):
            name = ui.input("Full Name", value=profile["full_name"])
            email = ui.input("Email", value=profile["email"])
            linkedin = ui.input("LinkedIn URL", value=profile["linkedin_url"])
        with ui.column().classes("flex-1 gap-2"):
            location = ui.input("Location", value=profile["location"])
            phone = ui.input("Phone Number", value=profile["phone"])
            github = ui.input("GitHub URL", value=profile["github_url"])

    ui.label("Markdown CV").classes("text-h6")
    with ui.row().classes("w-full gap-4"):
        with ui.column().classes("flex-1"):
            cv = (
                ui.textarea(
                    "Edit your CV in Markdown format",
                    value=profile["cv_markdown"],
                )
                .classes("w-full")
                .props('autogrow input-style="min-height: 400px"')
            )
        with ui.column().classes("flex-1"):
            ui.label("Live preview").classes("font-bold")
            preview = ui.markdown(profile["cv_markdown"] or "_Nothing to preview yet._")

    def update_preview(event: events.ValueChangeEventArguments[str | None]) -> None:
        preview.set_content(event.value or "_Nothing to preview yet._")

    cv.on_value_change(update_preview)

    def save() -> None:
        db.save_profile(
            {
                "full_name": name.value or "",
                "location": location.value or "",
                "phone": phone.value or "",
                "email": email.value or "",
                "linkedin_url": linkedin.value or "",
                "github_url": github.value or "",
                "cv_markdown": cv.value or "",
                "date_updated": None,
            }
        )
        ui.notify("Profile and CV saved successfully!", type="positive")

    ui.button("💾 Save Profile & CV", on_click=save).props("color=primary")

    ui.separator()

    def export() -> None:
        current = db.get_profile()
        try:
            pdf_bytes = generate_cv_pdf(current, load_cv_styles(_cv_style_path()))
        except PdfExportError as exc:
            ui.notify(f"Could not generate PDF: {exc}", type="negative")
            return
        ui.download.content(
            pdf_bytes,
            get_pdf_filename(current["full_name"]),
            media_type="application/pdf",
        )

    ui.button("📄 Export to PDF", on_click=export)


@ui.page("/")
def dashboard_page() -> None:
    ui.page_title(APP_TITLE)
    dark = _apply_dark_mode()
    containers: dict[db.JobStatus, ui.column] = {}
    stats_container: ui.column | None = None
    results_container: ui.column | None = None
    status_tabs: ui.tabs | None = None
    status_panels: ui.tab_panels | None = None
    query = ""

    def refresh() -> None:
        searching = bool(query)
        if status_tabs is not None:
            status_tabs.set_visibility(not searching)
        if status_panels is not None:
            status_panels.set_visibility(not searching)
        for status, _label, _empty in STATUS_TABS:
            containers[status].clear()
        if results_container is not None:
            results_container.clear()
            results_container.set_visibility(searching)
            if searching:
                with results_container:
                    _render_job_list(
                        db.search_jobs(query),
                        f'No positions match "{query}".',
                        refresh,
                        show_status=True,
                    )
        if not searching:
            for status, _label, empty_message in STATUS_TABS:
                with containers[status]:
                    _render_job_list(db.search_jobs(query, status=status), empty_message, refresh)
        if stats_container is not None:
            stats_container.clear()
            with stats_container:
                _render_statistics()

    with ui.header().classes("items-center justify-between"):
        ui.label(APP_TITLE).classes("text-h6")
        settings_button = ui.button(icon="settings").props("flat color=white")

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
            )
            with ui.row().classes("w-full items-center gap-3"):
                scan_button = ui.button("Scan & Save Positions")
                status_label = ui.label()
            add_dialog = _job_form_dialog(
                None,
                lambda values: _add_job_manually(values, refresh),
            )
            scan_button.on_click(
                lambda: _handle_scan(urls_input.value, refresh, status_label, scan_button)
            )
            ui.button("➕ Add Manually", on_click=add_dialog.open).props("flat color=primary")

        with ui.tabs().classes("w-full") as main_tabs:
            dashboard_tab = ui.tab("📋 Dashboard")
            stats_tab = ui.tab("📊 Statistics")
            profile_tab = ui.tab("👤 Profile & CV Editor")

        with ui.tab_panels(main_tabs, value=dashboard_tab).classes("w-full"):
            with ui.tab_panel(dashboard_tab):
                search_input = (
                    ui.input("Search company", placeholder="e.g. Adapty")
                    .props("clearable debounce=300")
                    .classes("w-full")
                )

                def on_search(event: events.ValueChangeEventArguments[str | None]) -> None:
                    nonlocal query
                    query = event.value or ""
                    refresh()

                search_input.on_value_change(on_search)
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
            with ui.tab_panel(stats_tab):
                stats_container = ui.column().classes("w-full")
            with ui.tab_panel(profile_tab):
                _render_profile_editor()

        refresh()


@ui.page("/job/{job_id}")
def job_detail_page(job_id: int) -> None:
    ui.page_title(APP_TITLE)
    _apply_dark_mode()
    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-2"):
        job = db.get_job(job_id)
        with ui.row().classes("items-center gap-2"):
            ui.button("← Back to list", on_click=lambda: ui.navigate.to("/"))
            if job is not None:
                edit_dialog = _job_form_dialog(
                    job,
                    lambda values: _update_job_fields(job["id"], values),
                    with_comments=True,
                )
                ui.button("✏️ Edit", on_click=edit_dialog.open).props("flat color=primary")
        if job is None:
            ui.label("Position not found.").classes("text-warning")
            return

        ui.label(job["title"] or "Untitled position").classes("text-h5")
        ui.markdown(f"**Company:** {job['company'] or 'Unknown company'}")
        ui.markdown(f"**Location:** {job['location'] or 'Not Specified'}")
        ui.markdown(f"**Added:** {job['date_added']}")
        ui.markdown(f"**Status:** {db.job_status(job).title()}")
        if job["date_applied"]:
            ui.markdown(f"**Applied:** {job['date_applied']}")
        if job["date_interview"]:
            ui.markdown(f"**Interview:** {job['date_interview']}")
        if job["date_rejected"]:
            ui.markdown(f"**Rejected:** {job['date_rejected']}")
        for stage, label in STATUS_CHECKBOXES:
            comment = _stage_comment(job, stage)
            if comment:
                ui.markdown(f"**{label} comment:** {comment}")
        ui.link("Open original posting", job["job_url"], new_tab=True)

        events = db.get_events(job["id"])
        if events:
            ui.separator()
            ui.label("History").classes("text-h6")
            with ui.timeline(side="right"):
                for event in events:
                    ui.timeline_entry(
                        title=event["status"].title(),
                        subtitle=event["created_at"],
                    )

        ui.separator()
        ui.markdown(job["description"] or "_No description captured._")

        delete_dialog = _confirm_delete_dialog(
            job, lambda dialog: _delete_job_from_detail(dialog, job["id"])
        )
        ui.button("🗑️ Delete", on_click=delete_dialog.open).props("flat color=negative")


def run(settings: config.Settings | None = None) -> None:
    """CLI entrypoint that launches the CursusTrace NiceGUI dashboard."""
    global _settings
    _settings = settings or config.load_settings()
    db.init_db()
    ui.add_css(GLOBAL_CSS, shared=True)
    shutdown_signal: int | None = None

    def handle_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_signal
        shutdown_signal = signum
        app.shutdown()

    def install_handlers() -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handle_shutdown)
            except ValueError:
                pass

    # Register on startup so our handlers replace uvicorn's (and win).
    # The test simulator manages its own lifecycle and must not be intercepted.
    if os.environ.get("NICEGUI_USER_SIMULATION") != "true":
        app.on_startup(install_handlers)
    try:
        ui.run(
            title=APP_TITLE,
            favicon="📋",
            host=_settings.host,
            port=_settings.port,
            show=_settings.show,
            reload=_settings.reload,
        )
    except KeyboardInterrupt:
        shutdown_signal = signal.SIGINT
    if shutdown_signal is not None:
        print("\nCursusTrace stopped.")
        raise SystemExit(128 + shutdown_signal)


if __name__ in {"__main__", "__mp_main__"}:
    run()
