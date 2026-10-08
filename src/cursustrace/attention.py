"""Detect positions needing attention: stale pipeline stages and imminent deadlines."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime

from cursustrace import config, db


@dataclass(frozen=True)
class Thresholds:
    """Day-count thresholds for deciding when a position needs attention."""

    stale_applied_days: int
    stale_unapplied_days: int
    deadline_warning_days: int

    @classmethod
    def from_settings(cls, settings: config.Settings) -> Thresholds:
        """Build thresholds from resolved runtime settings."""
        return cls(
            stale_applied_days=settings.stale_applied_days,
            stale_unapplied_days=settings.stale_unapplied_days,
            deadline_warning_days=settings.deadline_warning_days,
        )


def _today() -> date:
    now = time.localtime()
    return date(now.tm_year, now.tm_mon, now.tm_mday)


def _parse_day(value: str | None) -> date | None:
    """Parse a stored YYYY-MM-DD HH:MM:SS timestamp, or None when missing or invalid."""
    if not value:
        return None
    try:
        parsed = time.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)


def days_until_deadline(deadline: str | None, today: date | None = None) -> int | None:
    """Whole days from today until the deadline (negative when past), or None."""
    if not deadline:
        return None
    try:
        parsed = datetime.fromisoformat(deadline).date()
    except ValueError:
        return None
    reference = today if today is not None else _today()
    return (parsed - reference).days


def _deadline_reason(remaining: int) -> str:
    if remaining > 1:
        return f"deadline in {remaining} days"
    if remaining == 1:
        return "deadline in 1 day"
    if remaining == 0:
        return "deadline is today"
    if remaining == -1:
        return "deadline passed 1 day ago"
    return f"deadline passed {-remaining} days ago"


def reasons_for(job: db.Job, thresholds: Thresholds, *, today: date | None = None) -> list[str]:
    """Return one short reason per attention rule this position triggers.

    Only unapplied and applied positions can ever match: interview and rejected
    mean the employer already responded, which stops both staleness clocks, and
    a deadline no longer matters once the pipeline moved on.
    """
    reference = today if today is not None else _today()
    status = db.job_status(job)
    reasons: list[str] = []

    if status == "applied":
        applied = _parse_day(job["date_applied"])
        if applied is not None:
            days = (reference - applied).days
            if days >= thresholds.stale_applied_days:
                reasons.append(f"no response in {days} days")
    elif status == "unapplied":
        added = _parse_day(job["date_added"])
        if added is not None:
            days = (reference - added).days
            if days >= thresholds.stale_unapplied_days:
                reasons.append(f"added {days} days ago, not applied")

    if status in ("unapplied", "applied"):
        remaining = days_until_deadline(job["deadline"], reference)
        if remaining is not None and remaining <= thresholds.deadline_warning_days:
            reasons.append(_deadline_reason(remaining))

    return reasons
