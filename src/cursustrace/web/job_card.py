"""Position cards: relative deadline text, days-since-applied, and the dashboard card."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime

from nicegui import ui

from cursustrace import db, salary
from cursustrace.web.status_controls import (
    _render_comment_box,
    _render_delete_controls,
    _render_status_controls,
)


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
