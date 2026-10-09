"""Status checkbox controls, per-stage comment boxes, and delete buttons."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import events, ui

from cursustrace import db
from cursustrace.web.constants import STATUS_CHECKBOXES
from cursustrace.web.dialogs import _confirm_delete_dialog, _delete_job

_DISABLED_CHECKBOXES: dict[db.JobStatus, frozenset[db.JobStatus]] = {
    "unapplied": frozenset(),
    "applied": frozenset({"applied"}),
    "interview": frozenset({"applied", "interview"}),
    "rejected": frozenset({"applied", "interview"}),
    "outdated": frozenset({"outdated"}),
}


def _checked_flags(job: db.Job) -> frozenset[db.JobStatus]:
    status = db.job_status(job)
    if status == "applied":
        return frozenset({"applied"})
    if status == "interview":
        return frozenset({"applied", "interview"})
    if status == "rejected":
        flags: set[db.JobStatus] = {"applied", "rejected"}
        if job["date_interview"]:
            flags.add("interview")
        return frozenset(flags)
    if status == "outdated":
        return frozenset({"outdated"})
    return frozenset()


def _stage_comment(job: db.Job, stage: db.JobStatus) -> str:
    if stage == "outdated":
        return ""
    if stage == "applied":
        return job["applied_comment"] or ""
    if stage == "interview":
        return job["interview_comment"] or ""
    return job["rejected_comment"] or ""


def _status_handler(
    job_id: int,
    status: db.JobStatus,
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
