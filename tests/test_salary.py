"""Tests for salary annualization, formatting, filtering, and charting helpers."""

from __future__ import annotations

from typing import cast

from cursustrace import db, salary


def _job(**overrides: object) -> db.Job:
    base: dict[str, object] = {
        "id": 1,
        "job_url": "https://example.com/1",
        "title": "Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "body",
        "applied": 0,
        "interview": 0,
        "rejected": 0,
        "date_added": "2026-01-01 00:00:00",
        "date_applied": None,
        "date_interview": None,
        "date_rejected": None,
        "applied_comment": None,
        "interview_comment": None,
        "rejected_comment": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "salary_note": None,
        "fingerprint": "x",
    }
    base.update(overrides)
    return cast("db.Job", base)


def test_annual_bounds_yearly() -> None:
    job = _job(salary_min=60000, salary_max=80000, salary_currency="EUR", salary_period="year")
    assert salary.annual_bounds(job) == (60000, 80000)


def test_annual_bounds_monthly_is_annualized() -> None:
    job = _job(salary_min=3000, salary_max=4000, salary_currency="EUR", salary_period="month")
    assert salary.annual_bounds(job) == (36000, 48000)


def test_annual_bounds_single_ended() -> None:
    job = _job(salary_min=5000, salary_currency="USD", salary_period="month")
    assert salary.annual_bounds(job) == (60000, 60000)


def test_annual_bounds_none_without_amounts_or_period() -> None:
    assert salary.annual_bounds(_job()) is None
    assert salary.annual_bounds(_job(salary_min=1000)) is None


def test_format_salary_range() -> None:
    job = _job(salary_min=60000, salary_max=80000, salary_currency="EUR", salary_period="year")
    assert salary.format_salary(job) == "€60,000 – €80,000 / year"


def test_format_salary_note_only() -> None:
    assert salary.format_salary(_job(salary_note="recruiter: 3k-4k net")) == "recruiter: 3k-4k net"


def test_format_salary_none() -> None:
    assert salary.format_salary(_job()) is None


def test_filter_by_salary_range_and_currency() -> None:
    jobs = [
        _job(id=1, salary_min=60000, salary_currency="EUR", salary_period="year"),
        _job(id=2, salary_min=3000, salary_currency="EUR", salary_period="month"),
        _job(id=3, salary_min=90000, salary_currency="USD", salary_period="year"),
        _job(id=4),
    ]
    matched = salary.filter_by_salary(jobs, min_annual=35000, currency="EUR")
    assert [job["id"] for job in matched] == [1, 2]


def test_filter_by_salary_without_filters_returns_all() -> None:
    jobs = [_job(id=1), _job(id=2)]
    assert salary.filter_by_salary(jobs) == jobs


def test_salary_histogram_buckets() -> None:
    jobs = [
        _job(id=1, salary_min=25000, salary_currency="EUR", salary_period="year"),
        _job(id=2, salary_min=45000, salary_currency="EUR", salary_period="year"),
        _job(id=3, salary_min=50000, salary_currency="USD", salary_period="year"),
    ]
    labels, counts = salary.salary_histogram(jobs, "EUR", step=10000)
    assert labels == ["20k–30k", "30k–40k", "40k–50k"]
    assert counts == [1, 0, 1]


def test_salary_histogram_empty() -> None:
    assert salary.salary_histogram([_job()], "EUR") == ([], [])


def test_salary_summary_per_currency() -> None:
    jobs = [
        _job(salary_min=60000, salary_currency="EUR", salary_period="year"),
        _job(salary_min=80000, salary_currency="EUR", salary_period="year"),
        _job(salary_min=90000, salary_currency="USD", salary_period="year"),
        _job(),
    ]
    summary = salary.salary_summary(jobs)
    assert summary["EUR"] == {"count": 2, "minimum": 60000, "median": 70000, "maximum": 80000}
    assert summary["USD"]["count"] == 1
