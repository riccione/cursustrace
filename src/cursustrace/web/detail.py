"""Job detail page: full record, status controls, comments, history, and delete."""

from __future__ import annotations

from urllib.parse import quote

from nicegui import ui

from cursustrace import db, salary
from cursustrace.web.constants import APP_TITLE, STATUS_CHECKBOXES
from cursustrace.web.dialogs import _confirm_delete_dialog, _delete_job_from_detail
from cursustrace.web.job_card import _deadline_text
from cursustrace.web.job_form import _job_form_dialog, _update_job_fields
from cursustrace.web.profile import _render_profile_summary
from cursustrace.web.state import _apply_dark_mode
from cursustrace.web.status_controls import _render_status_controls, _stage_comment


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
