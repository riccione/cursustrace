"""Tests for attention detection: stale stages and imminent deadlines."""

from __future__ import annotations

from datetime import date
from typing import cast

from cursustrace import attention, config, db

TODAY = date(2026, 10, 8)
THRESHOLDS = attention.Thresholds(
    stale_applied_days=21,
    stale_unapplied_days=10,
    deadline_warning_days=7,
)


def _job(**overrides: object) -> db.Job:
    """Partial job row holding only the columns the attention rules read."""
    row: dict[str, object] = {
        "applied": 0,
        "interview": 0,
        "rejected": 0,
        "outdated": 0,
        "date_added": "2026-10-01 09:00:00",
        "date_applied": None,
        "date_interview": None,
        "date_rejected": None,
        "deadline": None,
    }
    row.update(overrides)
    return cast(db.Job, row)


def test_from_settings_maps_each_threshold() -> None:
    settings = config.Settings(
        stale_applied_days=14,
        stale_unapplied_days=5,
        deadline_warning_days=3,
        outdated_after_days=45,
    )

    thresholds = attention.Thresholds.from_settings(settings)

    assert thresholds == attention.Thresholds(14, 5, 3, 45)


def test_applied_staleness_boundary() -> None:
    quiet_for_20_days = _job(applied=1, date_applied="2026-09-18 10:00:00")
    quiet_for_21_days = _job(applied=1, date_applied="2026-09-17 10:00:00")

    assert attention.reasons_for(quiet_for_20_days, THRESHOLDS, today=TODAY) == []
    assert attention.reasons_for(quiet_for_21_days, THRESHOLDS, today=TODAY) == [
        "no response in 21 days"
    ]


def test_unapplied_staleness_boundary() -> None:
    added_9_days_ago = _job(date_added="2026-09-29 09:00:00")
    added_10_days_ago = _job(date_added="2026-09-28 09:00:00")

    assert attention.reasons_for(added_9_days_ago, THRESHOLDS, today=TODAY) == []
    assert attention.reasons_for(added_10_days_ago, THRESHOLDS, today=TODAY) == [
        "added 10 days ago, not applied"
    ]


def test_interview_and_rejected_never_match() -> None:
    old = "2020-01-01 09:00:00"
    in_interview = _job(
        applied=1, interview=1, date_applied=old, date_interview=old, deadline="2020-01-05"
    )
    rejected = _job(
        applied=1, rejected=1, date_applied=old, date_rejected=old, deadline="2020-01-05"
    )

    assert attention.reasons_for(in_interview, THRESHOLDS, today=TODAY) == []
    assert attention.reasons_for(rejected, THRESHOLDS, today=TODAY) == []


def test_deadline_phrases_cover_remaining_days() -> None:
    cases = [
        ("2026-10-16", None),  # a day past the window: not attention-worthy
        ("2026-10-15", "deadline in 7 days"),
        ("2026-10-14", "deadline in 6 days"),
        ("2026-10-09", "deadline in 1 day"),
        ("2026-10-08", "deadline is today"),
        ("2026-10-07", "deadline passed 1 day ago"),
        ("2026-10-03", "deadline passed 5 days ago"),
    ]
    for deadline, expected in cases:
        job = _job(deadline=deadline)
        reasons = attention.reasons_for(job, THRESHOLDS, today=TODAY)
        assert reasons == ([] if expected is None else [expected]), deadline


def test_deadline_window_is_inclusive() -> None:
    at_boundary = _job(deadline="2026-10-15")  # exactly seven days out
    outside = _job(deadline="2026-10-16")

    assert attention.reasons_for(at_boundary, THRESHOLDS, today=TODAY) == ["deadline in 7 days"]
    assert attention.reasons_for(outside, THRESHOLDS, today=TODAY) == []


def test_stale_and_deadline_reasons_combine() -> None:
    job = _job(date_added="2026-09-20 09:00:00", deadline="2026-10-11")

    assert attention.reasons_for(job, THRESHOLDS, today=TODAY) == [
        "added 18 days ago, not applied",
        "deadline in 3 days",
    ]


def test_applied_job_with_early_deadline_keeps_both_rules() -> None:
    job = _job(applied=1, date_applied="2026-09-17 10:00:00", deadline="2026-10-14")

    assert attention.reasons_for(job, THRESHOLDS, today=TODAY) == [
        "no response in 21 days",
        "deadline in 6 days",
    ]


def test_malformed_dates_are_skipped() -> None:
    applied = _job(applied=1, date_applied="not-a-date", deadline="soon")
    added = _job(date_added="not-a-date")

    assert attention.reasons_for(applied, THRESHOLDS, today=TODAY) == []
    assert attention.reasons_for(added, THRESHOLDS, today=TODAY) == []


def test_missing_and_malformed_deadlines_are_ignored() -> None:
    assert attention.reasons_for(_job(deadline=None), THRESHOLDS, today=TODAY) == []
    assert attention.reasons_for(_job(deadline="soon"), THRESHOLDS, today=TODAY) == []


def test_zero_threshold_flags_immediately() -> None:
    applied_today = _job(applied=1, date_applied="2026-10-08 09:00:00")
    deadline_today = _job(deadline="2026-10-08")

    assert attention.reasons_for(applied_today, attention.Thresholds(0, 10, 7), today=TODAY) == [
        "no response in 0 days"
    ]
    assert attention.reasons_for(deadline_today, attention.Thresholds(21, 10, 0), today=TODAY) == [
        "deadline is today"
    ]


def test_future_stage_dates_do_not_flag() -> None:
    job = _job(applied=1, date_applied="2026-10-10 09:00:00")

    assert attention.reasons_for(job, THRESHOLDS, today=TODAY) == []


def test_days_until_deadline() -> None:
    assert attention.days_until_deadline(None, TODAY) is None
    assert attention.days_until_deadline("", TODAY) is None
    assert attention.days_until_deadline("soon", TODAY) is None
    assert attention.days_until_deadline("2026-10-14", TODAY) == 6
    assert attention.days_until_deadline("2026-10-08T12:00:00", TODAY) == 0
    assert attention.days_until_deadline("2026-10-03", TODAY) == -5


def test_parse_day_handles_missing_and_bad_values() -> None:
    assert attention._parse_day(None) is None
    assert attention._parse_day("") is None
    assert attention._parse_day("not-a-date") is None
    assert attention._parse_day("2026-10-08 09:30:00") == TODAY


def test_is_outdated_applied_is_graduated() -> None:
    warned_only = _job(applied=1, date_applied="2026-09-13 10:00:00")  # 25 days
    parked = _job(applied=1, date_applied="2026-09-08 10:00:00")  # 30 days

    assert attention.reasons_for(warned_only, THRESHOLDS, today=TODAY)
    assert not attention.is_outdated(warned_only, THRESHOLDS, today=TODAY)
    assert attention.is_outdated(parked, THRESHOLDS, today=TODAY)


def test_is_outdated_unapplied_boundary() -> None:
    added_29_days_ago = _job(date_added="2026-09-09 09:00:00")
    added_30_days_ago = _job(date_added="2026-09-08 09:00:00")

    assert not attention.is_outdated(added_29_days_ago, THRESHOLDS, today=TODAY)
    assert attention.is_outdated(added_30_days_ago, THRESHOLDS, today=TODAY)


def test_is_outdated_waits_for_the_stage_threshold_too() -> None:
    thresholds = attention.Thresholds(45, 10, 7, outdated_after_days=30)
    stale_but_young = _job(applied=1, date_applied="2026-08-29 10:00:00")  # 40 days
    past_both = _job(applied=1, date_applied="2026-08-24 10:00:00")  # 45 days

    assert not attention.is_outdated(stale_but_young, thresholds, today=TODAY)
    assert attention.is_outdated(past_both, thresholds, today=TODAY)


def test_is_outdated_skips_other_stages() -> None:
    old = "2020-01-01 09:00:00"
    in_interview = _job(applied=1, interview=1, date_applied=old, date_interview=old)
    rejected = _job(applied=1, rejected=1, date_applied=old, date_rejected=old)
    parked = _job(outdated=1)

    assert not attention.is_outdated(in_interview, THRESHOLDS, today=TODAY)
    assert not attention.is_outdated(rejected, THRESHOLDS, today=TODAY)
    assert not attention.is_outdated(parked, THRESHOLDS, today=TODAY)


def test_is_outdated_handles_missing_and_bad_dates() -> None:
    no_date = _job(applied=1, date_applied=None)
    bad_date = _job(applied=1, date_applied="not-a-date")

    assert not attention.is_outdated(no_date, THRESHOLDS, today=TODAY)
    assert not attention.is_outdated(bad_date, THRESHOLDS, today=TODAY)
