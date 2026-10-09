"""NiceGUI entry point for cursustrace."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from types import FrameType
from typing import Literal, TypedDict, TypeVar, cast
from urllib.parse import quote

from nicegui import app, events, ui
from nicegui.run import io_bound
from starlette.requests import Request

from cursustrace import backup, config, db, salary, scraper
from cursustrace.errors import PdfExportError, ScrapeError
from cursustrace.logsetup import setup_logging
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename, load_cv_styles
from cursustrace.validation import normalize_deadline, validate_required, validate_url
from cursustrace.web import state
from cursustrace.web.attention_ui import (
    _park_stale_positions,
    _render_attention_banner,
    _similar_summary,
)
from cursustrace.web.constants import (
    APP_TITLE,
    FUNNEL_STAGES,
    STAT_CARDS,
    STATUS_CHECKBOXES,
    STATUS_NAMES,
    STATUS_TABS,
    load_webapp_css,
)
from cursustrace.web.dialogs import (
    _clear_database,
    _confirm_delete_dialog,
    _delete_job_from_detail,
    _tag_manager_dialog,
)
from cursustrace.web.state import (
    ATTENTION_NOTICE_KEY,
    DARK_MODE_KEY,
    DARK_MODE_OPTIONS,
    SELECTED_PROFILE_KEY,
    SIMILAR_NOTICE_KEY,
    _apply_dark_mode,
    _attention_banner_enabled,
    _cv_style_path,
    _dark_mode_name,
    _dark_mode_value,
    _similar_notice_enabled,
)
from cursustrace.web.status_controls import (
    _render_comment_box,
    _render_delete_controls,
    _render_status_controls,
    _stage_comment,
)

logger = logging.getLogger(__name__)


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


def _deadline_text(deadline: str | None, today: date | None = None) -> str | None:
    """Describe a deadline relative to today, or None when it is unset or invalid."""
    if not deadline:
        return None
    try:
        parsed = datetime.fromisoformat(deadline).date()
    except ValueError:
        return None
    if today is None:
        now = time.localtime()
        today = date(now.tm_year, now.tm_mon, now.tm_mday)
    delta = (parsed - today).days
    if delta == 0:
        return "— is today"
    if delta == 1:
        return "— in 1 day"
    if delta == -1:
        return "— passed 1 day ago"
    if delta > 1:
        return f"— in {delta} days"
    return f"— passed {-delta} days ago"


def _render_job_card(
    job: db.Job,
    refresh: Callable[[], None],
    *,
    show_status: bool = False,
    tags: list[str] | None = None,
    on_tag_click: Callable[[str], None] | None = None,
) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    label = f"{title} — {company}"
    if show_status:
        label += f" [{db.job_status(job).title()}]"
    tag_names = db.job_tags(job["id"]) if tags is None else tags
    if tag_names:
        with ui.row().classes("w-full items-center gap-1").mark("job-tags"):
            for name in tag_names:
                if on_tag_click is None:
                    ui.chip(name)
                else:
                    chip = ui.chip(name)
                    chip.on_click(lambda tag=name: on_tag_click(tag))
    with ui.expansion(label).classes("w-full"):
        ui.markdown(f"**Location:** {job['location']}")
        ui.markdown(f"**Added:** {job['date_added']}")
        salary_text = salary.format_salary(job)
        if salary_text:
            ui.markdown(f"**Salary:** {salary_text}").mark("salary")
        deadline_text = _deadline_text(job["deadline"])
        if deadline_text is not None:
            ui.markdown(f"**Deadline:** {job['deadline']} {deadline_text}").mark("deadline")
        days_applied = _days_since_applied(job)
        if days_applied is not None:
            ui.label(f"Applied {days_applied} days ago").classes("text-caption").mark(
                "days-since-applied"
            )
        ui.link("Open job posting", job["job_url"], new_tab=True)
        ui.link("View full details", f"/job/{job['id']}")
        current = db.job_status(job)
        _render_status_controls(job, current, refresh)
        if current in ("applied", "interview", "rejected"):
            _render_comment_box(job, current)
        _render_delete_controls(job, refresh)


def _fill_months(months: list[tuple[str, int]]) -> tuple[list[str], list[int]]:
    """Expand sparse monthly counts into a zero-filled, contiguous series."""
    counts = dict(months)
    year, month = int(months[0][0][:4]), int(months[0][0][5:7])
    end_year, end_month = int(months[-1][0][:4]), int(months[-1][0][5:7])
    labels: list[str] = []
    while (year, month) <= (end_year, end_month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return labels, [counts.get(label, 0) for label in labels]


def _render_status_pie(counts: dict[str, int], dark: bool) -> None:
    with ui.card().classes("w-full"):
        ui.label("Status distribution").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "type": "pie",
                    "radius": ["45%", "70%"],
                    "label": {"formatter": "{b}: {c}"},
                    "data": [{"name": name, "value": counts[key]} for key, name in STATUS_NAMES],
                }
            ],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("status-chart")


def _render_funnel(dark: bool) -> None:
    funnel = db.pipeline_funnel()
    with ui.card().classes("w-full"):
        ui.label("Response funnel").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "series": [
                {
                    "type": "funnel",
                    "left": "10%",
                    "width": "80%",
                    "sort": "none",
                    "gap": 2,
                    "label": {"formatter": "{b}: {c}"},
                    "data": [{"name": name, "value": funnel[key]} for key, name in FUNNEL_STAGES],
                }
            ],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("funnel-chart")


def _render_timeline(months: list[tuple[str, int]], dark: bool) -> None:
    labels, values = _fill_months(months)
    with ui.card().classes("w-full"):
        ui.label("Applications per month").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value", "name": "Applications", "minInterval": 1},
            "series": [{"type": "bar", "name": "Applications", "data": values, "barMaxWidth": 40}],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("timeline-chart")


def _render_statistics(dark: bool) -> None:
    counts = db.job_counts()
    with ui.grid(columns=2).classes("w-full gap-4"):
        for key, label in STAT_CARDS:
            with ui.card().classes("w-full items-center"):
                ui.label(str(counts[key])).classes("text-h4").mark(f"stat-{key}")
                ui.label(label)

    if counts["total"]:
        with ui.grid(columns=2).classes("w-full gap-4"):
            _render_status_pie(counts, dark)
            _render_funnel(dark)
        months = db.applications_by_month()
        if months:
            _render_timeline(months, dark)
        else:
            ui.label("No applications recorded yet.").classes("text-caption")
    else:
        ui.label("No positions yet to display charts.").classes("text-caption")

    jobs = db.get_jobs()
    summary = salary.salary_summary(jobs)
    with_salary = sum(entry["count"] for entry in summary.values())
    ui.label(f"{with_salary} of {counts['total']} positions have salary data").classes(
        "text-caption"
    )
    if not summary:
        ui.label("Add salary to positions to see the distribution.").classes("text-caption")
        return

    currency = ui.toggle(list(salary.CURRENCIES), value=next(iter(summary))).classes("w-full")
    chart_container = ui.column().classes("w-full")

    def render_chart() -> None:
        chart_container.clear()
        labels, values = salary.salary_histogram(jobs, currency.value or "EUR")
        with chart_container:
            if not labels:
                ui.label("No salary data for this currency.").classes("text-caption")
                return
            options = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": labels},
                "yAxis": {"type": "value", "name": "Positions"},
                "series": [{"type": "bar", "name": "Positions", "data": values}],
            }
            ui.echart(options, theme="dark" if dark else None).classes("w-full").mark(
                "salary-chart"
            )

    currency.on_value_change(lambda _: render_chart())
    render_chart()


def _parse_urls(text: str | None) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines()]
    return list(dict.fromkeys(line for line in lines if line))


def _scan_url(url: str) -> tuple[str, str, list[db.Job]]:
    try:
        job = scraper.scrape_job(url)
    except ScrapeError as exc:
        logger.error("Scrape failed for %s: %s", url, exc)
        return ("error", f"{url}: {exc}", [])

    if db.check_duplicate(url):
        return ("duplicate", url, [])
    job_id = db.add_job(
        url,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
        deadline=job["deadline"],
    )
    if job_id is None:
        return ("duplicate", url, [])
    matches = db.find_similar(job["company"], job["title"], job["description"], exclude_id=job_id)
    return ("saved", url, matches)


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
    urls_input: ui.textarea,
    refresh: Callable[[], None],
    status_label: ui.label,
    button: ui.button,
) -> None:
    urls = _parse_urls(urls_input.value)
    if not urls:
        ui.notify("Please enter at least one job URL.", type="warning")
        return

    button.enabled = False
    saved = duplicates = errors = 0
    cancelled = False
    failures: list[str] = []
    failed_urls: list[str] = []
    similar: list[list[db.Job]] = []
    try:
        for index, url in enumerate(urls, start=1):
            status_label.set_text(f"Scanning {index}/{len(urls)}: {url}")
            matches: list[db.Job]
            outcome = await io_bound(_scan_url, url)
            if outcome is None:
                cancelled = True
                kind, message, matches = "error", f"{url}: cancelled", []
            else:
                kind, message, matches = outcome
            if kind == "saved":
                saved += 1
                if matches:
                    similar.append(matches)
            elif kind == "duplicate":
                duplicates += 1
            else:
                errors += 1
                failures.append(message)
                failed_urls.append(url)
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
    if similar and _similar_notice_enabled():
        flat = [job for matches in similar for job in matches]
        ui.notify(
            f"Added {saved} — {len(similar)} look similar: {_similar_summary(flat)}",
            type="info",
        )
    if cancelled:
        return
    urls_input.value = "\n".join(failed_urls) if errors else ""


def _render_settings(refresh: Callable[[], None], dark: ui.dark_mode) -> None:
    ui.label("Appearance").classes("text-h6")
    theme = ui.toggle(DARK_MODE_OPTIONS, value=_dark_mode_name(dark.value)).classes("w-full")

    def update_theme(event: events.ValueChangeEventArguments[str | None]) -> None:
        name = event.value or "system"
        dark.value = _dark_mode_value(name)
        db.set_setting(DARK_MODE_KEY, name)
        refresh()

    theme.on_value_change(update_theme)

    ui.separator()
    ui.label("Notifications").classes("text-h6")
    similar_toggle = ui.switch(
        "Notify about similar positions", value=_similar_notice_enabled()
    ).classes("w-full")

    def update_similar(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_setting(SIMILAR_NOTICE_KEY, "on" if event.value else "off")

    similar_toggle.on_value_change(update_similar)

    attention_toggle = (
        ui.switch("Show needs-attention banner", value=_attention_banner_enabled())
        .classes("w-full")
        .mark("attention-banner-toggle")
    )

    def update_attention(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_setting(ATTENTION_NOTICE_KEY, "on" if event.value else "off")
        refresh()

    attention_toggle.on_value_change(update_attention)

    ui.separator()
    ui.label("🏷️ Tags").classes("text-h6")
    tag_manager = _tag_manager_dialog(refresh)
    ui.button("Manage tags", on_click=tag_manager.open).mark("manage-tags")

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


@dataclass(frozen=True)
class JobFormValues:
    """Values collected from the manual job form."""

    url: str
    title: str
    company: str
    location: str
    description: str
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = ""
    salary_note: str = ""
    deadline: str = ""
    applied_comment: str = ""
    interview_comment: str = ""
    rejected_comment: str = ""
    tags: list[str] = field(default_factory=list)


class SalaryKwargs(TypedDict):
    """Keyword arguments passed through to the database salary columns."""

    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: str | None
    salary_note: str | None


def _salary_kwargs(values: JobFormValues) -> SalaryKwargs:
    has_amount = values.salary_min is not None or values.salary_max is not None
    return SalaryKwargs(
        salary_min=values.salary_min if has_amount else None,
        salary_max=values.salary_max if has_amount else None,
        salary_currency=(values.salary_currency or None) if has_amount else None,
        salary_period=(values.salary_period or None) if has_amount else None,
        salary_note=values.salary_note or None,
    )


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
    salary_min = values["salary_min"] if values else None
    salary_max = values["salary_max"] if values else None
    salary_currency = (values["salary_currency"] or "") if values else "EUR"
    salary_period = (values["salary_period"] or "") if values else "year"
    salary_note = (values["salary_note"] or "") if values else ""
    deadline_value = (values["deadline"] or "") if values else ""
    current_tags = db.job_tags(values["id"]) if values is not None else []

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
        with ui.row().classes("w-full gap-2"):
            salary_min_input = ui.number(
                "Salary min", value=salary_min, min=0, step=1000, format="%.0f"
            ).classes("flex-1")
            salary_max_input = ui.number(
                "Salary max", value=salary_max, min=0, step=1000, format="%.0f"
            ).classes("flex-1")
        with ui.row().classes("w-full gap-2"):
            salary_currency_input = ui.select(
                list(salary.CURRENCIES), value=salary_currency or None, label="Currency"
            ).classes("flex-1")
            salary_period_input = ui.select(
                list(salary.PERIODS), value=salary_period or None, label="Period"
            ).classes("flex-1")
        salary_note_input = ui.input("Salary note", value=salary_note).classes("w-full")
        deadline_input = (
            ui.input("Deadline", value=deadline_value, placeholder="2026-12-31")
            .classes("w-full")
            .mark("deadline-field")
        )
        tags_input = (
            ui.select(
                db.list_tags(),
                value=current_tags,
                label="Tags",
                multiple=True,
                clearable=True,
                new_value_mode="add-unique",
            )
            .classes("w-full")
            .mark("tags-field")
        )
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
                if stage == "outdated":
                    continue
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
            low, high = salary_min_input.value, salary_max_input.value
            if low is not None and high is not None and low > high:
                ui.notify("Salary min cannot exceed salary max.", type="warning")
                return
            if (low is not None or high is not None) and not (
                salary_currency_input.value and salary_period_input.value
            ):
                ui.notify("Choose a currency and period for the salary.", type="warning")
                return
            cleaned_deadline = (deadline_input.value or "").strip()
            if cleaned_deadline and normalize_deadline(cleaned_deadline) is None:
                ui.notify("Deadline must be an ISO date like 2026-12-31.", type="warning")
                return
            result = on_save(
                JobFormValues(
                    url=(url.value or "").strip(),
                    title=(title_input.value or "").strip(),
                    company=(company_input.value or "").strip(),
                    location=(location_input.value or "").strip(),
                    description=(description_input.value or "").strip(),
                    salary_min=int(low) if low is not None else None,
                    salary_max=int(high) if high is not None else None,
                    salary_currency=salary_currency_input.value or "",
                    salary_period=salary_period_input.value or "",
                    salary_note=(salary_note_input.value or "").strip(),
                    deadline=normalize_deadline(cleaned_deadline) or "",
                    applied_comment=(comment_inputs["applied"].value or "")
                    if with_comments
                    else "",
                    interview_comment=(comment_inputs["interview"].value or "")
                    if with_comments
                    else "",
                    rejected_comment=(comment_inputs["rejected"].value or "")
                    if with_comments
                    else "",
                    tags=[str(name) for name in tags_input.value or []],
                )
            )
            if result:
                dialog.close()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("color=primary")
    return dialog


def _add_job_manually(values: JobFormValues, refresh: Callable[[], None]) -> bool:
    if db.check_duplicate(values.url):
        logger.warning("Rejected duplicate manual add: %s", values.url)
        ui.notify("A position with the same URL already exists.", type="warning")
        return False
    job_id = db.add_job(
        values.url,
        values.title,
        values.company,
        values.location or None,
        values.description,
        deadline=values.deadline or None,
        **_salary_kwargs(values),
    )
    if job_id is None:
        logger.warning("Rejected duplicate manual add: %s", values.url)
        ui.notify("A position with the same URL already exists.", type="warning")
        return False
    db.set_job_tags(job_id, values.tags)
    ui.notify("Position added.", type="positive")
    matches = db.find_similar(values.company, values.title, values.description, exclude_id=job_id)
    if matches and _similar_notice_enabled():
        ui.notify(f"Looks similar to {_similar_summary(matches)}", type="info")
    refresh()
    return True


def _update_job_fields(job_id: int, values: JobFormValues) -> bool:
    if db.check_duplicate(values.url, exclude_id=job_id) or not db.update_job(
        job_id,
        values.url,
        values.title,
        values.company,
        values.location or None,
        values.description,
        deadline=values.deadline or None,
        **_salary_kwargs(values),
    ):
        logger.warning("Rejected duplicate update: %s", values.url)
        ui.notify("A position with the same URL already exists.", type="warning")
        return False
    db.update_job_comments(
        job_id,
        values.applied_comment,
        values.interview_comment,
        values.rejected_comment,
    )
    db.set_job_tags(job_id, values.tags)
    ui.notify("Position updated.", type="positive")
    ui.navigate.to(f"/job/{job_id}")
    return True


_FieldT = TypeVar("_FieldT", bound=ui.input)


def _attach_copy_button(field: _FieldT, label: str, marker: str) -> _FieldT:
    """Add an in-field copy-to-clipboard button to an input or textarea."""

    def copy() -> None:
        if not field.value:
            ui.notify(f"{label} is empty", type="warning")
            return
        ui.clipboard.write(field.value)
        ui.notify(f"Copied {label}!", type="positive")

    with field.add_slot("append"):
        ui.button(icon="content_copy", on_click=copy).props("flat dense").mark(marker)
    return field


def _render_profile_editor() -> None:
    ui.label("👤 Profile & CV Configuration").classes("text-h5")

    profiles = db.list_profiles()
    known_ids = {profile["id"] for profile in profiles}
    stored = db.get_setting(SELECTED_PROFILE_KEY)
    stored_id = int(stored) if stored is not None and stored.isdigit() else None
    selected: dict[str, int | None] = {
        "id": stored_id if stored_id in known_ids else (min(known_ids) if known_ids else None)
    }

    with ui.dialog() as add_dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Add profile").classes("text-h6")
        new_name_input = ui.input("Profile name").classes("w-full").mark("new-profile-name")
        with ui.row():
            ui.button("Cancel", on_click=add_dialog.close).props("flat")
            ui.button("Add", on_click=lambda: confirm_add()).props("color=primary").mark(
                "confirm-add-profile"
            )

    with ui.dialog() as delete_dialog, ui.card():
        ui.label("Delete this profile?").classes("text-h6")
        delete_target = ui.label().classes("font-bold")
        ui.label("This action cannot be undone.").classes("text-negative")
        delete_confirm_input = (
            ui.input("Type 'DELETE' to confirm", placeholder="DELETE")
            .classes("w-full")
            .mark("delete-profile-confirm")
        )
        delete_button = (
            ui.button("Delete profile", on_click=lambda: confirm_delete())
            .props("color=negative")
            .mark("confirm-delete-profile")
        )
        delete_button.enabled = False

        def update_delete_button(event: events.ValueChangeEventArguments[str | None]) -> None:
            delete_button.enabled = event.value == "DELETE"

        delete_confirm_input.on_value_change(update_delete_button)

    container = ui.column().classes("w-full")
    with container:
        topbar = ui.row().classes("w-full items-center gap-4")
        body = ui.column().classes("w-full")

    def persist_selection() -> None:
        if selected["id"] is not None:
            db.set_setting(SELECTED_PROFILE_KEY, str(selected["id"]))

    def render_topbar() -> None:
        current_profiles = db.list_profiles()
        ids = {profile["id"] for profile in current_profiles}
        if selected["id"] not in ids:
            selected["id"] = min(ids) if ids else None
        topbar.clear()
        with topbar:
            if len(current_profiles) >= 2:
                selector = (
                    ui.select(
                        options={profile["id"]: profile["name"] for profile in current_profiles},
                        value=selected["id"],
                        label="Profile",
                    )
                    .classes("w-64")
                    .mark("profile-select")
                )
                selector.on_value_change(switch_profile)
            ui.button("➕ Add Profile", on_click=add_dialog.open).mark("add-profile")
            if selected["id"] is not None:
                ui.button(
                    "🗑 Delete Profile",
                    on_click=open_delete,
                ).props("color=negative").mark("delete-profile")

    def switch_profile(event: events.ValueChangeEventArguments[int | None]) -> None:
        if event.value is None:
            return
        selected["id"] = event.value
        persist_selection()
        render_form()

    def confirm_add() -> None:
        name = (new_name_input.value or "").strip()
        if not name:
            ui.notify("Enter a profile name.", type="warning")
            return
        profile_id = db.create_profile(name)
        if profile_id is None:
            ui.notify(f"Profile '{name}' already exists.", type="warning")
            return
        new_name_input.value = ""
        add_dialog.close()
        selected["id"] = profile_id
        persist_selection()
        rerender_all()
        ui.notify(f"Profile '{name}' created.", type="positive")

    def open_delete() -> None:
        if selected["id"] is None:
            return
        delete_target.set_text(f"Profile '{db.get_profile(selected['id'])['name']}'")
        delete_confirm_input.value = ""
        delete_button.enabled = False
        delete_dialog.open()

    def confirm_delete() -> None:
        if selected["id"] is None:
            return
        db.delete_profile(selected["id"])
        delete_dialog.close()
        remaining = db.list_profiles()
        selected["id"] = min((profile["id"] for profile in remaining), default=None)
        if selected["id"] is not None:
            persist_selection()
        rerender_all()
        ui.notify("Profile deleted.", type="positive")

    def render_form() -> None:
        body.clear()
        with body:
            profiles_now = db.list_profiles()
            ids = {profile["id"] for profile in profiles_now}
            if selected["id"] not in ids:
                selected["id"] = min(ids) if ids else None
            if selected["id"] is None:
                ui.label("No profiles yet. Add a profile to create your CV.").mark("no-profiles")
                return
            _render_profile_fields(db.get_profile(selected["id"]))

    def rerender_all() -> None:
        render_topbar()
        render_form()

    def _copyable_input(label: str, value: str, marker: str) -> ui.input:
        """Profile contact field with an in-field copy-to-clipboard button."""
        return _attach_copy_button(ui.input(label, value=value), label, marker)

    def _copyable_textarea(label: str, value: str, marker: str) -> ui.textarea:
        """CV markdown section with an in-field copy-to-clipboard button."""
        return _attach_copy_button(
            ui.textarea(label, value=value)
            .classes("w-full")
            .props('autogrow input-style="min-height: 140px"'),
            label,
            marker,
        )

    def _render_profile_fields(profile: db.Profile) -> None:
        with ui.row().classes("w-full gap-8"):
            with ui.column().classes("flex-1 gap-2"):
                name = _copyable_input("Full Name", profile["full_name"], "copy-full-name")
                email = _copyable_input("Email", profile["email"], "copy-email")
                linkedin = _copyable_input("LinkedIn URL", profile["linkedin_url"], "copy-linkedin")
            with ui.column().classes("flex-1 gap-2"):
                location = ui.input("Location", value=profile["location"])
                phone = _copyable_input("Phone Number", profile["phone"], "copy-phone")
                github = _copyable_input("GitHub URL", profile["github_url"], "copy-github")

        def combined_markdown() -> str:
            sections = (
                summary.value or "",
                work_history.value or "",
                education.value or "",
                skills.value or "",
            )
            return "\n\n".join(section.strip() for section in sections if section.strip())

        ui.label("Markdown CV").classes("text-h6")
        with ui.row().classes("w-full gap-4"):
            with ui.column().classes("flex-1 gap-4"):
                summary = _copyable_textarea("Summary", profile["summary"], "copy-summary")
                work_history = _copyable_textarea(
                    "Work History", profile["work_history"], "copy-work-history"
                )
                education = _copyable_textarea("Education", profile["education"], "copy-education")
                skills = _copyable_textarea("Skills", profile["skills"], "copy-skills")
            with ui.column().classes("flex-1"):
                ui.label("Live preview").classes("font-bold")
                preview = ui.markdown(combined_markdown() or "_Nothing to preview yet._")

        def update_preview(_event: events.ValueChangeEventArguments[str | None]) -> None:
            preview.set_content(combined_markdown() or "_Nothing to preview yet._")

        for section in (summary, work_history, education, skills):
            section.on_value_change(update_preview)

        def save() -> None:
            db.save_profile(
                {
                    "id": profile["id"],
                    "name": profile["name"],
                    "full_name": name.value or "",
                    "location": location.value or "",
                    "phone": phone.value or "",
                    "email": email.value or "",
                    "linkedin_url": linkedin.value or "",
                    "github_url": github.value or "",
                    "summary": summary.value or "",
                    "work_history": work_history.value or "",
                    "education": education.value or "",
                    "skills": skills.value or "",
                    "date_updated": None,
                }
            )
            ui.notify("Profile and CV saved successfully!", type="positive")

        ui.button("💾 Save Profile & CV", on_click=save).props("color=primary")

        ui.separator()

        def export() -> None:
            current = db.get_profile(profile["id"])
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

    rerender_all()


@ui.page("/")
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


def _render_copyable_field(label: str, value: str, field_marker: str, copy_marker: str) -> None:
    """Readonly contact field with an in-field copy-to-clipboard button."""
    field_input = (
        ui.input(label, value=value).props("readonly").classes("w-full").mark(field_marker)
    )
    _attach_copy_button(field_input, label, copy_marker)


def _render_profile_summary() -> None:
    """Render each profile's contact fields; render nothing when no profiles exist."""
    profiles = db.list_profiles()
    if not profiles:
        return
    ui.separator()
    with ui.column().classes("w-full gap-2").mark("profile-summary"):
        ui.label("Your profile").classes("text-h6")
        for profile in profiles:
            if len(profiles) > 1:
                ui.label(profile["name"]).classes("text-subtitle1")
            with ui.row().classes("w-full gap-8"):
                with ui.column().classes("flex-1 gap-2"):
                    _render_copyable_field(
                        "Full Name",
                        profile["full_name"],
                        "detail-field-name",
                        "detail-copy-name",
                    )
                    _render_copyable_field(
                        "Email", profile["email"], "detail-field-email", "detail-copy-email"
                    )
                    _render_copyable_field(
                        "Phone", profile["phone"], "detail-field-phone", "detail-copy-phone"
                    )
                with ui.column().classes("flex-1 gap-2"):
                    _render_copyable_field(
                        "LinkedIn URL",
                        profile["linkedin_url"],
                        "detail-field-linkedin",
                        "detail-copy-linkedin",
                    )
                    _render_copyable_field(
                        "GitHub URL",
                        profile["github_url"],
                        "detail-field-github",
                        "detail-copy-github",
                    )


@ui.page("/job/{job_id}")
def job_detail_page(job_id: int) -> None:
    ui.page_title(APP_TITLE)
    _apply_dark_mode()
    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-2"):

        @ui.refreshable
        def render_detail() -> None:
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

            current = db.job_status(job)
            ui.label(job["title"] or "Untitled position").classes("text-h5")
            ui.markdown(f"**Company:** {job['company'] or 'Unknown company'}")
            ui.markdown(f"**Location:** {job['location'] or 'Not Specified'}")
            ui.markdown(f"**Added:** {job['date_added']}")
            detail_deadline_text = _deadline_text(job["deadline"])
            if detail_deadline_text is not None:
                ui.markdown(f"**Deadline:** {job['deadline']} {detail_deadline_text}").mark(
                    "deadline"
                )
            ui.markdown(f"**Status:** {current.title()}")
            _render_status_controls(job, current, refresh_detail)
            detail_tags = db.job_tags(job["id"])
            if detail_tags:
                with ui.row().classes("items-center gap-1").mark("job-tags"):
                    for name in detail_tags:
                        ui.chip(
                            name,
                            on_click=lambda tag=name: ui.navigate.to(f"/?tag={quote(tag)}"),
                        )
            salary_text = salary.format_salary(job)
            if salary_text:
                ui.markdown(f"**Salary:** {salary_text}")
            if job["date_applied"]:
                ui.markdown(f"**Applied:** {job['date_applied']}")
            if job["date_interview"]:
                ui.markdown(f"**Interview:** {job['date_interview']}")
            if job["date_rejected"]:
                ui.markdown(f"**Rejected:** {job['date_rejected']}")
            if job["date_outdated"]:
                ui.markdown(f"**Outdated:** {job['date_outdated']}")
            for stage, label in STATUS_CHECKBOXES:
                comment = _stage_comment(job, stage)
                if comment:
                    ui.markdown(f"**{label} comment:** {comment}")
            ui.link("Open original posting", job["job_url"], new_tab=True)

            _render_profile_summary()

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
            ui.markdown(job["description"] or "_No description captured._").classes(
                "job-description w-full min-w-0"
            ).mark("job-description")

            delete_dialog = _confirm_delete_dialog(
                job, lambda dialog: _delete_job_from_detail(dialog, job["id"])
            )
            ui.button("🗑️ Delete", on_click=delete_dialog.open).props("flat color=negative")

        def refresh_detail() -> None:
            render_detail.refresh()

        render_detail()


def _print_config_source(settings: config.Settings) -> None:
    """Tell the shell which configuration source the app resolved from."""
    if settings.config_file is not None:
        print(f"Config: using config file {settings.config_file}", flush=True)
    else:
        print("Config: no config file found, using hardcoded default values", flush=True)


def _backup_on_startup(settings: config.Settings) -> None:
    """Write a startup copy of the database; failures are logged, never fatal."""
    try:
        result = backup.backup_database(settings.backup_dir, keep=settings.backup_keep)
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Database backup failed")
        print(f"Backup failed: {exc}", flush=True)
        return
    if result is None:
        print("Backup skipped: no database file yet", flush=True)
        return
    logger.info("Database backup written to %s", result.path)
    pruned = ""
    if result.pruned:
        count = len(result.pruned)
        pruned = f" (pruned {count} old backup{'s' if count != 1 else ''})"
    print(f"Backup completed successfully to {result.path}{pruned}", flush=True)


def run(settings: config.Settings | None = None) -> None:
    """CLI entrypoint that launches the CursusTrace NiceGUI dashboard."""
    current = settings or config.load_settings()
    state._settings = current
    _print_config_source(current)
    setup_logging(current.log_level, retention_days=current.log_retention_days)
    try:
        db.init_db()
    except Exception:
        logger.exception("Failed to initialize the database")
        raise
    if current.backup_on_start:
        _backup_on_startup(current)
    else:
        print("Backup skipped: backup_on_start is disabled", flush=True)
    ui.add_css(load_webapp_css(), shared=True)
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
            host=current.host,
            port=current.port,
            show=current.show,
            reload=current.reload,
        )
    except KeyboardInterrupt:
        shutdown_signal = signal.SIGINT
    if shutdown_signal is not None:
        print("\nCursusTrace stopped.")
        raise SystemExit(128 + shutdown_signal)


if __name__ in {"__main__", "__mp_main__"}:
    run()
