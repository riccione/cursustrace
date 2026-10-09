"""Attention banner rendering, stale-position parking, and job label helpers."""

from __future__ import annotations

from datetime import date

from nicegui import ui

from cursustrace import attention, db
from cursustrace.web.state import _attention_banner_enabled, _attention_thresholds


def _job_label(job: db.Job) -> str:
    return f"{job['title'] or 'Untitled position'} — {job['company'] or 'Unknown company'}"


def _attention_sort_key(
    entry: tuple[db.Job, list[str]], today: date | None
) -> tuple[int, int, str]:
    """Urgency: deadline positions first (soonest or most overdue), then longest silence."""
    job, reasons = entry
    remaining = attention.days_until_deadline(job["deadline"], today)
    if remaining is not None and any(reason.startswith("deadline") for reason in reasons):
        return (0, remaining, "")
    stage = job["date_applied"] if db.job_status(job) == "applied" else job["date_added"]
    return (1, 0, stage or "")


def _attention_items(
    jobs: list[db.Job], thresholds: attention.Thresholds, *, today: date | None = None
) -> list[tuple[db.Job, list[str]]]:
    """Flagged positions with their reasons, ordered most urgent first."""
    flagged = [
        (job, reasons)
        for job in jobs
        if (reasons := attention.reasons_for(job, thresholds, today=today))
    ]
    return sorted(flagged, key=lambda entry: _attention_sort_key(entry, today))


def _park_stale_positions() -> None:
    """Park stale unapplied/applied positions, at most once per job.

    The graduated rule in attention.is_outdated requires both the stage's
    staleness warning and outdated_after_days, so the banner always gets
    to warn first. A job that ever carried an outdated event is skipped:
    once the user pulls a position out of the parked stage it stays out.
    """
    thresholds = _attention_thresholds()
    parked = db.job_ids_with_status_event("outdated")
    for job in db.get_jobs():
        if job["id"] in parked:
            continue
        if attention.is_outdated(job, thresholds):
            db.set_job_status(job["id"], "outdated")


def _render_attention_banner(container: ui.column) -> None:
    container.clear()
    if not _attention_banner_enabled():
        return
    items = _attention_items(db.get_jobs(), _attention_thresholds())
    if not items:
        return
    count = len(items)
    with container, ui.card().classes("w-full").mark("attention-banner"):
        needs = "needs" if count == 1 else "need"
        plural = "" if count == 1 else "s"
        ui.label(f"⚠️ {count} position{plural} {needs} attention").classes("text-subtitle1")
        for job, reasons in items[:5]:
            with ui.row().classes("w-full items-center gap-2").mark("attention-item"):
                ui.link(_job_label(job), f"/job/{job['id']}")
                ui.label("; ".join(reasons)).classes("text-caption")
        if count > 5:
            ui.label(f"+{count - 5} more").classes("text-caption")


def _similar_summary(matches: list[db.Job], shown: int = 2) -> str:
    parts = [f"'{_job_label(job)}' ({job['job_url']})" for job in matches[:shown]]
    text = "; ".join(parts)
    if len(matches) > shown:
        text += f" (+{len(matches) - shown} more)"
    return text
