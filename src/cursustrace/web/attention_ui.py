"""Attention badge counts, stale-position parking, and job label helpers."""

from __future__ import annotations

from cursustrace import attention, db
from cursustrace.web.session import current_user_id
from cursustrace.web.state import _attention_notice_enabled, _attention_thresholds


def _job_label(job: db.Job) -> str:
    return f"{job['title'] or 'Untitled position'} — {job['company'] or 'Unknown company'}"


def attention_reasons(job: db.Job) -> list[str]:
    """Reasons this position needs attention; empty when it is fine or disabled."""
    if not _attention_notice_enabled():
        return []
    return attention.reasons_for(job, _attention_thresholds())


def most_urgent(reasons: list[str]) -> str:
    """Return the most pressing reason, preferring an imminent deadline."""
    for reason in reasons:
        if reason.startswith("deadline"):
            return reason
    return reasons[0]


def attention_first(jobs: list[db.Job]) -> list[db.Job]:
    """Put the positions needing attention first, keeping the order of each group."""
    flagged: list[db.Job] = []
    rest: list[db.Job] = []
    for job in jobs:
        (flagged if attention_reasons(job) else rest).append(job)
    return flagged + rest


def attention_counts() -> dict[db.JobStatus, int]:
    """Count the positions needing attention in each pipeline stage.

    Only unapplied and applied positions can qualify, so the other stages are
    never present in the result.
    """
    if not _attention_notice_enabled():
        return {}
    thresholds = _attention_thresholds()
    counts: dict[db.JobStatus, int] = {}
    for job in db.get_jobs(user_id=current_user_id()):
        if not attention.reasons_for(job, thresholds):
            continue
        status = db.job_status(job)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _park_stale_positions() -> None:
    """Park stale unapplied/applied positions, at most once per job.

    The graduated rule in attention.is_outdated requires both the stage's
    staleness warning and outdated_after_days, so the badge always gets
    to warn first. A job that ever carried an outdated event is skipped:
    once the user pulls a position out of the parked stage it stays out.
    """
    thresholds = _attention_thresholds()
    user_id = current_user_id()
    parked = db.job_ids_with_status_event("outdated", user_id=user_id)
    for job in db.get_jobs(user_id=user_id):
        if job["id"] in parked:
            continue
        if attention.is_outdated(job, thresholds):
            db.set_job_status(job["id"], "outdated", user_id=user_id)


def _similar_summary(matches: list[db.Job], shown: int = 2) -> str:
    parts = [f"'{_job_label(job)}' ({job['job_url']})" for job in matches[:shown]]
    text = "; ".join(parts)
    if len(matches) > shown:
        text += f" (+{len(matches) - shown} more)"
    return text
