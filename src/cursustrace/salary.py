"""Optional salary/compensation helpers: annualization, formatting, filtering, charting."""

from __future__ import annotations

from statistics import median
from typing import TypedDict

from cursustrace.db import Job

CURRENCIES = ("EUR", "USD")
PERIODS = ("year", "month")
ANNUAL_MULTIPLIERS = {"year": 1, "month": 12}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$"}
PERIOD_LABELS = {"year": "year", "month": "month"}
HISTOGRAM_STEP = 10_000


class CurrencyStats(TypedDict):
    """Annualized salary statistics for a single currency."""

    count: int
    minimum: int
    median: int
    maximum: int


def annual_bounds(job: Job) -> tuple[int, int] | None:
    """Return the annualized (min, max) salary, or None when no usable amount is set."""
    period = job["salary_period"]
    if period not in ANNUAL_MULTIPLIERS:
        return None
    values = [value for value in (job["salary_min"], job["salary_max"]) if value is not None]
    if not values:
        return None
    multiplier = ANNUAL_MULTIPLIERS[period]
    return (min(values) * multiplier, max(values) * multiplier)


def format_salary(job: Job) -> str | None:
    """Render the raw salary range plus note, or None when nothing is set."""
    values = [value for value in (job["salary_min"], job["salary_max"]) if value is not None]
    parts: list[str] = []
    if values:
        symbol = CURRENCY_SYMBOLS.get(job["salary_currency"] or "", "")
        low, high = min(values), max(values)
        amount = f"{symbol}{low:,} – {symbol}{high:,}" if low != high else f"{symbol}{low:,}"
        period = PERIOD_LABELS.get(job["salary_period"] or "")
        parts.append(f"{amount} / {period}" if period else amount)
    note = job["salary_note"]
    if note:
        parts.append(note)
    return " · ".join(parts) if parts else None


def filter_by_salary(
    jobs: list[Job],
    *,
    min_annual: int | None = None,
    max_annual: int | None = None,
    currency: str | None = None,
) -> list[Job]:
    """Filter jobs by annualized salary range and currency; jobs without salary are dropped."""
    if currency is None and min_annual is None and max_annual is None:
        return list(jobs)
    result: list[Job] = []
    for job in jobs:
        if currency is not None and job["salary_currency"] != currency:
            continue
        bounds = annual_bounds(job)
        if bounds is None:
            continue
        low, high = bounds
        if min_annual is not None and high < min_annual:
            continue
        if max_annual is not None and low > max_annual:
            continue
        result.append(job)
    return result


def salary_histogram(
    jobs: list[Job], currency: str, step: int = HISTOGRAM_STEP
) -> tuple[list[str], list[int]]:
    """Bucket annualized salaries for one currency into labels and counts."""
    values = []
    for job in jobs:
        if job["salary_currency"] != currency:
            continue
        bounds = annual_bounds(job)
        if bounds is not None:
            values.append((bounds[0] + bounds[1]) // 2)
    if not values:
        return ([], [])
    labels: list[str] = []
    counts: list[int] = []
    edge = (min(values) // step) * step
    highest = (max(values) // step) * step
    while edge <= highest:
        labels.append(f"{edge // 1000}k–{(edge + step) // 1000}k")
        counts.append(sum(1 for value in values if edge <= value < edge + step))
        edge += step
    return (labels, counts)


def salary_summary(jobs: list[Job]) -> dict[str, CurrencyStats]:
    """Return annualized count/min/median/max per currency that has salary data."""
    summary: dict[str, CurrencyStats] = {}
    for currency in CURRENCIES:
        values = []
        for job in jobs:
            if job["salary_currency"] != currency:
                continue
            bounds = annual_bounds(job)
            if bounds is not None:
                values.append((bounds[0] + bounds[1]) // 2)
        if values:
            summary[currency] = CurrencyStats(
                count=len(values),
                minimum=min(values),
                median=int(median(values)),
                maximum=max(values),
            )
    return summary
