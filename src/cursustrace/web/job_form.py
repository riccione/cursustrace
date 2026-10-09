"""Manual add/edit job dialog, its value types, and the database writes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from nicegui import ui

from cursustrace import db, salary
from cursustrace.validation import normalize_deadline, validate_required, validate_url
from cursustrace.web.attention_ui import _similar_summary
from cursustrace.web.constants import STATUS_CHECKBOXES
from cursustrace.web.state import _similar_notice_enabled
from cursustrace.web.status_controls import _stage_comment

logger = logging.getLogger(__name__)


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
