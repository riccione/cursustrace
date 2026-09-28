"""Serialize stored positions into portable JSON and CSV payloads."""

from __future__ import annotations

import csv
import io
import json as json_module

from cursustrace import db

EXPORT_FIELDS = (
    "url",
    "title",
    "company",
    "location",
    "description",
    "status",
    "date_added",
    "date_applied",
    "date_interview",
    "date_rejected",
    "applied_comment",
    "interview_comment",
    "rejected_comment",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_note",
    "tags",
)


def job_record(job: db.Job, tags: list[str] | None = None) -> dict[str, object]:
    """Flatten one stored position into the portable export field set."""
    return {
        "url": job["job_url"],
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "description": job["description"],
        "status": db.job_status(job),
        "date_added": job["date_added"],
        "date_applied": job["date_applied"],
        "date_interview": job["date_interview"],
        "date_rejected": job["date_rejected"],
        "applied_comment": job["applied_comment"],
        "interview_comment": job["interview_comment"],
        "rejected_comment": job["rejected_comment"],
        "salary_min": job["salary_min"],
        "salary_max": job["salary_max"],
        "salary_currency": job["salary_currency"],
        "salary_period": job["salary_period"],
        "salary_note": job["salary_note"],
        "tags": tags or [],
    }


def render_json(jobs: list[db.Job]) -> str:
    """Render positions as a JSON array matching the importer's shape."""
    grouped = db.tags_for_jobs([job["id"] for job in jobs])
    return json_module.dumps(
        [job_record(job, grouped.get(job["id"], [])) for job in jobs],
        ensure_ascii=False,
        indent=2,
    )


def render_csv(jobs: list[db.Job]) -> str:
    """Render positions as CSV with a fixed header row."""
    grouped = db.tags_for_jobs([job["id"] for job in jobs])
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(EXPORT_FIELDS), extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for job in jobs:
        record = {
            key: ("" if value is None else value)
            for key, value in job_record(job, grouped.get(job["id"], [])).items()
        }
        tags = record.get("tags")
        if isinstance(tags, list):
            record["tags"] = ";".join(str(name) for name in tags)
        writer.writerow(record)
    return buffer.getvalue().rstrip("\n")
