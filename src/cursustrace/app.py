"""NiceGUI entry point for cursustrace."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from functools import lru_cache
from types import FrameType
from typing import Literal
from urllib.parse import urlparse

from nicegui import app, events, ui
from nicegui.run import io_bound

from cursustrace import db, scraper
from cursustrace.errors import PdfExportError, ScrapeError
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename, load_cv_styles

APP_TITLE = "CursusTrace — Job Application Tracker"

_shutdown_signal: int | None = None

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

GLOBAL_CSS = """
html { font-size: 22px; }
body { font-size: 22px; }
.q-btn, .q-field, .q-field__label, .q-tab__label, .q-item, .q-checkbox,
.q-notification { font-size: inherit; }
body.body--dark .q-drawer { background: #1d1d1d; }
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


@lru_cache(maxsize=16)
def _cv_pdf_bytes(
    full_name: str,
    location: str,
    phone: str,
    email: str,
    linkedin_url: str,
    github_url: str,
    cv_markdown: str,
    styles: str,
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
    return generate_cv_pdf(profile, styles)


def _cached_pdf(profile: db.Profile) -> bytes:
    return _cv_pdf_bytes(
        profile["full_name"],
        profile["location"],
        profile["phone"],
        profile["email"],
        profile["linkedin_url"],
        profile["github_url"],
        profile["cv_markdown"],
        load_cv_styles(),
    )


def _job_status(job: db.Job) -> db.JobStatus:
    if job["interview"]:
        return "interview"
    if job["rejected"]:
        return "rejected"
    if job["applied"]:
        return "applied"
    return "unapplied"


_DISABLED_CHECKBOXES: dict[db.JobStatus, frozenset[db.JobFlag]] = {
    "unapplied": frozenset(),
    "applied": frozenset({"applied"}),
    "interview": frozenset({"applied", "interview"}),
    "rejected": frozenset({"applied", "interview"}),
}


def _checked_flags(job: db.Job) -> frozenset[db.JobFlag]:
    status = _job_status(job)
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
        current = _job_status(job)
        checked = _checked_flags(job)
        for status, label in STATUS_CHECKBOXES:
            checkbox = ui.checkbox(
                label,
                value=status in checked,
                on_change=_status_handler(job["id"], status, refresh),
            )
            checkbox.enabled = status not in _DISABLED_CHECKBOXES[current]

        with ui.dialog() as dialog, ui.card():
            dialog.mark(f"delete-dialog-{job['id']}")
            ui.label("Delete this position?")
            ui.label(f"{title} — {company}").classes("font-bold")
            ui.label("This action cannot be undone.").classes("text-negative")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete",
                    on_click=lambda: _delete_job(dialog, job["id"], refresh),
                ).props("color=negative").mark(f"delete-confirm-{job['id']}")
        ui.button("🗑️ Delete", on_click=dialog.open).props("flat color=negative")


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
    if duplicate or not db.add_job(
        url,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    ):
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


def _validate_url(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return "Job URL is required."
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Enter a full URL starting with http:// or https://."
    return None


def _validate_required(label: str) -> Callable[[str | None], str | None]:
    def check(value: str | None) -> str | None:
        return None if (value or "").strip() else f"{label} is required."

    return check


def _job_form_dialog(
    values: db.Job | None,
    on_save: Callable[[str, str, str, str, str], bool],
) -> ui.dialog:
    job_url = values["job_url"] if values else ""
    title = (values["title"] or "") if values else ""
    company = (values["company"] or "") if values else ""
    location = (values["location"] or "") if values else ""
    description = (values["description"] or "") if values else ""

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
        dialog.mark("job-form")
        url = ui.input("Job URL", value=job_url, validation=_validate_url).classes("w-full")
        title_input = ui.input(
            "Title", value=title, validation=_validate_required("Title")
        ).classes("w-full")
        company_input = ui.input(
            "Company", value=company, validation=_validate_required("Company")
        ).classes("w-full")
        location_input = ui.input("Location", value=location).classes("w-full")
        description_input = (
            ui.textarea(
                "Description",
                value=description,
                validation=_validate_required("Description"),
            )
            .classes("w-full")
            .props('autogrow input-style="min-height: 120px"')
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
            if on_save(
                (url.value or "").strip(),
                (title_input.value or "").strip(),
                (company_input.value or "").strip(),
                (location_input.value or "").strip(),
                (description_input.value or "").strip(),
            ):
                dialog.close()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("color=primary")
    return dialog


def _add_job_manually(
    url: str,
    title: str,
    company: str,
    location: str,
    description: str,
    refresh: Callable[[], None],
) -> bool:
    duplicate, _reason = db.check_duplicate(url, title, company, location or None, description)
    if duplicate or not db.add_job(url, title, company, location or None, description):
        ui.notify(
            "A position with the same URL or fingerprint already exists.", type="warning"
        )
        return False
    ui.notify("Position added.", type="positive")
    refresh()
    return True


def _update_job_fields(
    job_id: int, url: str, title: str, company: str, location: str, description: str
) -> bool:
    duplicate, _reason = db.check_duplicate(
        url, title, company, location or None, description, exclude_id=job_id
    )
    if duplicate or not db.update_job(
        job_id, url, title, company, location or None, description
    ):
        ui.notify(
            "A position with the same URL or fingerprint already exists.", type="warning"
        )
        return False
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
    dark = _apply_dark_mode()
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

    with ui.right_drawer(value=False).props("width=480") as drawer:
        settings_button.on_click(lambda: drawer.toggle())
        _render_settings(refresh, dark)

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
        with ui.column().classes("w-full gap-2"):
            urls_input = ui.textarea(
                "Job URLs (one per line)",
                placeholder="https://example.com/job/1\nhttps://example.com/job/2",
            ).classes("w-full").props('autogrow input-style="min-height: 80px"')
            with ui.row().classes("w-full items-center gap-3"):
                scan_button = ui.button("Scan & Save Positions")
                status_label = ui.label()
            add_dialog = _job_form_dialog(
                None,
                lambda u, t, c, loc, desc: _add_job_manually(u, t, c, loc, desc, refresh),
            )
            scan_button.on_click(
                lambda: _handle_scan(urls_input.value, refresh, status_label, scan_button)
            )
            ui.button("➕ Add Manually", on_click=add_dialog.open).props("flat color=primary")

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
    _apply_dark_mode()
    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-2"):
        job = _resolve_job(job_id)
        with ui.row().classes("items-center gap-2"):
            ui.button("← Back to list", on_click=lambda: ui.navigate.to("/"))
            if job is not None:
                edit_dialog = _job_form_dialog(
                    job,
                    lambda u, t, c, loc, desc: _update_job_fields(
                        job["id"], u, t, c, loc, desc
                    ),
                )
                ui.button("✏️ Edit", on_click=edit_dialog.open).props("flat color=primary")
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

        with ui.dialog() as dialog, ui.card():
            dialog.mark(f"delete-dialog-{job['id']}")
            ui.label("Delete this position?")
            ui.label(f"{job['title'] or 'Untitled position'} — {job['company'] or 'Unknown company'}").classes(
                "font-bold"
            )
            ui.label("This action cannot be undone.").classes("text-negative")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete",
                    on_click=lambda: _delete_job_from_detail(dialog, job["id"]),
                ).props("color=negative").mark(f"delete-confirm-{job['id']}")
        ui.button("🗑️ Delete", on_click=dialog.open).props("flat color=negative")


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
    if _shutdown_signal is not None:
        print("\nCursusTrace stopped.")
        raise SystemExit(128 + _shutdown_signal)


if __name__ in {"__main__", "__mp_main__"}:
    run()
