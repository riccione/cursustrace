"""Position cards: relative deadline text, days-since-applied, and the dashboard card."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime

from nicegui import ui

from cursustrace import db, salary
from cursustrace.web.attention_ui import attention_reasons, most_urgent
from cursustrace.web.constants import STATUS_NAMES
from cursustrace.web.status_controls import (
    _render_comment_box,
    _render_delete_controls,
    _render_status_controls,
)

STAGE_LABELS: dict[db.JobStatus, str] = dict(STATUS_NAMES)

# A click on a chip must not reach the card header, which would toggle the
# expansion. NiceGUI passes props whose name starts with a colon to the client
# as JavaScript, so this installs a listener that stops the propagation while
# the chip's own handler still runs.
_STOP_PROPAGATION_PROP = ":onClick"
_STOP_PROPAGATION_JS = "[(event) => { event.stopPropagation(); }]"


def _keep_click_inside(element: ui.element) -> None:
    """Stop a click on ``element`` from bubbling up to its ancestors."""
    element.props[_STOP_PROPAGATION_PROP] = _STOP_PROPAGATION_JS


def _today() -> date:
    """Return the current calendar day."""
    now = time.localtime()
    return date(now.tm_year, now.tm_mon, now.tm_mday)


def _parse_day(stamp: str | None) -> date | None:
    """Parse a stored timestamp into a calendar day, or None when it is unusable."""
    if not stamp:
        return None
    try:
        parsed = time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)


def _days_since_applied(job: db.Job) -> int | None:
    """Return whole days since the job was marked applied, or None when it was not."""
    applied = _parse_day(job["date_applied"])
    if applied is None:
        return None
    return (_today() - applied).days


def _days_between(start: str | None, end: str | None) -> int | None:
    """Return whole days between two stored timestamps, or None when either is unusable."""
    first = _parse_day(start)
    second = _parse_day(end)
    if first is None or second is None:
        return None
    return (second - first).days


def _deadline_text(deadline: str | None, today: date | None = None) -> str | None:
    """Describe a deadline relative to today, or None when it is unset or invalid."""
    if not deadline:
        return None
    try:
        parsed = datetime.fromisoformat(deadline).date()
    except ValueError:
        return None
    if today is None:
        today = _today()
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


def _render_status_badge(status: db.JobStatus) -> None:
    """Render the pill that names the current pipeline stage."""
    ui.label(STAGE_LABELS[status]).classes(f"ct-badge ct-badge--{status}").mark("job-card-badge")


def _render_tracker(job: db.Job, status: db.JobStatus) -> None:
    """Render the linear stage tracker, or a flat note for a terminal stage."""
    if status not in db.PIPELINE_STAGES:
        _render_terminal_note(job, status)
        return
    current = db.PIPELINE_STAGES.index(status)
    with ui.row().classes("ct-tracker").props("aria-hidden=true").mark("job-card-tracker"):
        for index, stage in enumerate(db.PIPELINE_STAGES):
            if index < current:
                state = "is-done"
            elif index == current:
                state = "is-current"
            else:
                state = "is-todo"
            with ui.column().classes(f"ct-tracker__step {state}"):
                ui.element("span").classes("ct-tracker__dot")
                ui.label(STAGE_LABELS[stage]).classes("ct-tracker__label")


def _render_terminal_note(job: db.Job, status: db.JobStatus) -> None:
    """Render the flat note that replaces the tracker for a terminal stage."""
    if status == "rejected":
        days = _days_between(job["date_applied"], job["date_rejected"])
        text = (
            f"Application rejected after {days} days"
            if days is not None
            else "Application rejected"
        )
        icon = "cancel"
    else:
        text = "Listing is outdated"
        icon = "inventory_2"
    with ui.row().classes(f"ct-note ct-note--{status}"):
        ui.icon(icon).classes("ct-note__icon")
        ui.label(text).mark("job-card-terminal")


def _render_card_chips(
    job: db.Job,
    tag_names: list[str],
    on_tag_click: Callable[[str], None] | None,
) -> None:
    """Render the tag, CV and attention chips that stay visible on the card."""
    reasons = attention_reasons(job)
    if not tag_names and not job["profile_name"] and not reasons:
        return
    with ui.row().classes("ct-card__chips items-center gap-1 flex-wrap").mark("job-tags"):
        for name in tag_names:
            if on_tag_click is None:
                ui.chip(name)
            else:
                # The click must stay on the chip: stopping the propagation keeps
                # it from reaching the card header and toggling the expansion.
                chip = ui.chip(name, on_click=lambda tag=name: on_tag_click(tag))
                _keep_click_inside(chip)
        if job["profile_name"]:
            ui.chip(f"CV: {job['profile_name']}").mark("cv-used-chip")
        if reasons:
            ui.chip(most_urgent(reasons), icon="warning").props("color=warning").mark(
                "attention-chip"
            )


def _render_ident(title: str, company: str, status: db.JobStatus) -> None:
    """Render the title, company and status badge that head the card."""
    with ui.row().classes("ct-card__ident items-center gap-2 min-w-0 w-full"):
        ui.label(title).classes("ct-card__title").mark("job-card-title")
        ui.label("•").classes("ct-card__sep").props("aria-hidden=true")
        ui.label(company).classes("ct-card__company").mark("job-card-company")
        _render_status_badge(status)


def _render_job_card(
    job: db.Job,
    refresh: Callable[[], None],
    *,
    tags: list[str] | None = None,
    on_tag_click: Callable[[str], None] | None = None,
) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    current = db.job_status(job)
    tag_names = db.job_tags(job["id"]) if tags is None else tags
    expansion = ui.expansion().classes("w-full ct-card").props("header-class=ct-card__header")
    with (
        expansion.add_slot("header"),
        ui.row().classes("ct-card__head flex-1 min-w-0 items-start justify-between gap-3"),
        ui.column().classes("ct-card__main grow gap-2 min-w-0"),
    ):
        _render_ident(title, company, current)
        _render_tracker(job, current)
        _render_card_chips(job, tag_names, on_tag_click)
    with expansion:
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
        _render_status_controls(job, current, refresh)
        if current in db.COMMENT_STAGES:
            _render_comment_box(job, current)
        _render_delete_controls(job, refresh)
