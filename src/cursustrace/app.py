"""NiceGUI entry point for cursustrace."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from functools import lru_cache
from types import FrameType

from nicegui import app, events, ui

from cursustrace import db, scraper
from cursustrace.errors import PdfExportError, ScrapeError
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename

APP_TITLE = "CursusTrace — Job Application Tracker"

_shutdown_signal: int | None = None

STATUS_TABS: tuple[tuple[db.JobStatus, str, str], ...] = (
    ("unapplied", "⏳ Unapplied Positions", "No unapplied positions yet."),
    ("applied", "✅ Applied Positions", "No applied positions yet."),
    ("interview", "🗣️ Interview Positions", "No interview positions yet."),
    ("rejected", "❌ Rejected Positions", "No rejected positions yet."),
)

STATUS_CHECKBOXES: tuple[tuple[db.JobFlag, str], ...] = (
    ("applied", "Mark as Applied"),
    ("interview", "Interview"),
    ("rejected", "Rejected"),
)

GLOBAL_CSS = """
html { font-size: 22px; }
body { font-size: 22px; }
.q-btn, .q-field, .q-field__label, .q-tab__label, .q-item, .q-checkbox,
.q-notification { font-size: inherit; }
"""


@lru_cache(maxsize=16)
def _cv_pdf_bytes(
    full_name: str,
    location: str,
    phone: str,
    email: str,
    linkedin_url: str,
    github_url: str,
    cv_markdown: str,
) -> bytes:
    profile: db.Profile = {
        "full_name": full_name,
        "location": location,
        "phone": phone,
        "email": email,
        "linkedin_url": linkedin_url,
        "github_url": github_url,
        "cv_markdown": cv_markdown,
        "date_updated": None,
    }
    return generate_cv_pdf(profile)


def _cached_pdf(profile: db.Profile) -> bytes:
    return _cv_pdf_bytes(
        profile["full_name"],
        profile["location"],
        profile["phone"],
        profile["email"],
        profile["linkedin_url"],
        profile["github_url"],
        profile["cv_markdown"],
    )


def _job_status(job: db.Job) -> db.JobStatus:
    if job["interview"]:
        return "interview"
    if job["rejected"]:
        return "rejected"
    if job["applied"]:
        return "applied"
    return "unapplied"


def _resolve_job(job_id: int) -> db.Job | None:
    return next((job for job in db.get_jobs() if job["id"] == job_id), None)


def _status_handler(
    job_id: int,
    status: db.JobFlag,
    refresh: Callable[[], None],
) -> Callable[[events.ValueChangeEventArguments[bool | None]], None]:
    def handler(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_job_status(job_id, status if event.value else "unapplied")
        refresh()

    return handler


def _render_job_card(job: db.Job, refresh: Callable[[], None]) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with ui.expansion(f"{title} — {company}").classes("w-full"):
        ui.markdown(f"**Location:** {job['location']}")
        ui.markdown(f"**Added:** {job['date_added']}")
        ui.link("Open job posting", job["job_url"], new_tab=True)
        ui.link("View full details", f"/job/{job['id']}")
        for status, label in STATUS_CHECKBOXES:
            ui.checkbox(
                label,
                value=bool(job[status]),
                on_change=_status_handler(job["id"], status, refresh),
            )


def _render_job_list(
    jobs: list[db.Job],
    empty_message: str,
    refresh: Callable[[], None],
) -> None:
    if not jobs:
        ui.label(empty_message)
        return
    for job in jobs:
        _render_job_card(job, refresh)


def _handle_scan(url: str | None, refresh: Callable[[], None]) -> None:
    candidate = (url or "").strip()
    if not candidate:
        ui.notify("Please enter a job URL.", type="warning")
        return

    try:
        job = scraper.scrape_job(candidate)
    except ScrapeError as exc:
        ui.notify(f"Could not scan that URL: {exc}", type="negative")
        return

    duplicate, _reason = db.check_duplicate(
        candidate,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    if duplicate:
        ui.notify(
            "⚠️ Position already exists in database (Matched by title/company fingerprint).",
            type="warning",
        )
        return

    added = db.add_job(
        candidate,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    if added:
        ui.notify("Position saved.", type="positive")
        refresh()
    else:
        ui.notify(
            "⚠️ Position already exists in database (Matched by title/company fingerprint).",
            type="warning",
        )


def _render_settings(refresh: Callable[[], None]) -> None:
    ui.label("⚙️ Settings & Maintenance").classes("text-h6")
    with ui.expansion("⚠️ Danger Zone: Clear Database"):
        ui.label(
            "⚠️ This action cannot be undone. All tracked job postings and "
            "application history will be permanently erased."
        ).classes("text-negative")
        confirm = ui.input("Type 'DELETE' to confirm", placeholder="DELETE")
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
            cv = ui.textarea(
                "Edit your CV in Markdown format",
                value=profile["cv_markdown"],
            ).classes("w-full").props('autogrow input-style="min-height: 400px; line-height: 1.7"')
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
            pdf_bytes = _cached_pdf(current)
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
    containers: dict[db.JobStatus, ui.column] = {}

    def refresh() -> None:
        for status, _label, empty_message in STATUS_TABS:
            container = containers[status]
            container.clear()
            with container:
                _render_job_list(db.get_jobs(status=status), empty_message, refresh)

    with ui.header().classes("items-center justify-between"):
        ui.label(APP_TITLE).classes("text-h6")
        settings_button = ui.button(icon="settings").props("flat color=white")

    with ui.right_drawer(value=False).classes("bg-grey-1") as drawer:
        settings_button.on_click(lambda: drawer.toggle())
        _render_settings(refresh)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-end"):
            url_input = ui.input("Job URL").classes("flex-grow")
            ui.button(
                "Scan & Save Position",
                on_click=lambda: _handle_scan(url_input.value, refresh),
            )

        with ui.tabs().classes("w-full") as main_tabs:
            dashboard_tab = ui.tab("📋 Dashboard")
            profile_tab = ui.tab("👤 Profile & CV Editor")

        with ui.tab_panels(main_tabs, value=dashboard_tab).classes("w-full"):
            with ui.tab_panel(dashboard_tab):
                with ui.tabs().classes("w-full") as status_tabs:
                    for status, label, _message in STATUS_TABS:
                        ui.tab(status, label=label)
                with ui.tab_panels(status_tabs, value="unapplied").classes("w-full"):
                    for status, _label, _message in STATUS_TABS:
                        with ui.tab_panel(status):
                            containers[status] = ui.column().classes("w-full")
            with ui.tab_panel(profile_tab):
                _render_profile_editor()

        refresh()


@ui.page("/job/{job_id}")
def job_detail_page(job_id: int) -> None:
    ui.page_title(APP_TITLE)
    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-2"):
        ui.button("← Back to list", on_click=lambda: ui.navigate.to("/"))

        job = _resolve_job(job_id)
        if job is None:
            ui.label("Position not found.").classes("text-warning")
            return

        ui.label(job["title"] or "Untitled position").classes("text-h5")
        ui.markdown(f"**Company:** {job['company'] or 'Unknown company'}")
        ui.markdown(f"**Location:** {job['location'] or 'Not Specified'}")
        ui.markdown(f"**Added:** {job['date_added']}")
        ui.markdown(f"**Status:** {_job_status(job).title()}")
        if job["date_applied"]:
            ui.markdown(f"**Applied:** {job['date_applied']}")
        if job["date_interview"]:
            ui.markdown(f"**Interview:** {job['date_interview']}")
        if job["date_rejected"]:
            ui.markdown(f"**Rejected:** {job['date_rejected']}")
        ui.link("Open original posting", job["job_url"], new_tab=True)

        ui.separator()
        ui.markdown(job["description"] or "_No description captured._")


def _request_shutdown(signum: int, _frame: FrameType | None) -> None:
    global _shutdown_signal
    _shutdown_signal = signum
    app.shutdown()


def _install_signal_handlers() -> None:
    if os.environ.get("NICEGUI_USER_SIMULATION") == "true":
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_shutdown)
        except ValueError:
            pass


def run() -> None:
    """CLI entrypoint that launches the CursusTrace NiceGUI dashboard."""
    global _shutdown_signal
    db.init_db()
    ui.add_css(GLOBAL_CSS, shared=True)
    app.on_startup(_install_signal_handlers)
    try:
        ui.run(
            title=APP_TITLE,
            favicon="📋",
            show=False,
            reload=False,
            port=8080,
        )
    except KeyboardInterrupt:
        _shutdown_signal = signal.SIGINT
    print("\nCursusTrace stopped.")
    if _shutdown_signal is not None:
        raise SystemExit(128 + _shutdown_signal)


if __name__ in {"__main__", "__mp_main__"}:
    run()
