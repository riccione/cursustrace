"""Statistics tab: stage counts, status/funnel/timeline charts, and salary histogram."""

from __future__ import annotations

from nicegui import ui

from cursustrace import db, salary
from cursustrace.web.constants import FUNNEL_STAGES, STAT_CARDS, STATUS_NAMES


def _fill_months(months: list[tuple[str, int]]) -> tuple[list[str], list[int]]:
    """Expand sparse monthly counts into a zero-filled, contiguous series."""
    counts = dict(months)
    year, month = int(months[0][0][:4]), int(months[0][0][5:7])
    end_year, end_month = int(months[-1][0][:4]), int(months[-1][0][5:7])
    labels: list[str] = []
    while (year, month) <= (end_year, end_month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return labels, [counts.get(label, 0) for label in labels]


def _render_status_pie(counts: dict[str, int], dark: bool) -> None:
    with ui.card().classes("w-full"):
        ui.label("Status distribution").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "type": "pie",
                    "radius": ["45%", "70%"],
                    "label": {"formatter": "{b}: {c}"},
                    "data": [{"name": name, "value": counts[key]} for key, name in STATUS_NAMES],
                }
            ],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("status-chart")


def _render_funnel(dark: bool) -> None:
    funnel = db.pipeline_funnel()
    with ui.card().classes("w-full"):
        ui.label("Response funnel").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "series": [
                {
                    "type": "funnel",
                    "left": "10%",
                    "width": "80%",
                    "sort": "none",
                    "gap": 2,
                    "label": {"formatter": "{b}: {c}"},
                    "data": [{"name": name, "value": funnel[key]} for key, name in FUNNEL_STAGES],
                }
            ],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("funnel-chart")


def _render_timeline(months: list[tuple[str, int]], dark: bool) -> None:
    labels, values = _fill_months(months)
    with ui.card().classes("w-full"):
        ui.label("Applications per month").classes("text-subtitle1")
        options = {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value", "name": "Applications", "minInterval": 1},
            "series": [{"type": "bar", "name": "Applications", "data": values, "barMaxWidth": 40}],
        }
        ui.echart(options, theme="dark" if dark else None).classes("w-full").mark("timeline-chart")


def _render_statistics(dark: bool) -> None:
    counts = db.job_counts()
    with ui.grid(columns=2).classes("w-full gap-4"):
        for key, label in STAT_CARDS:
            with ui.card().classes("w-full items-center"):
                ui.label(str(counts[key])).classes("text-h4").mark(f"stat-{key}")
                ui.label(label)

    if counts["total"]:
        with ui.grid(columns=2).classes("w-full gap-4"):
            _render_status_pie(counts, dark)
            _render_funnel(dark)
        months = db.applications_by_month()
        if months:
            _render_timeline(months, dark)
        else:
            ui.label("No applications recorded yet.").classes("text-caption")
    else:
        ui.label("No positions yet to display charts.").classes("text-caption")

    jobs = db.get_jobs()
    summary = salary.salary_summary(jobs)
    with_salary = sum(entry["count"] for entry in summary.values())
    ui.label(f"{with_salary} of {counts['total']} positions have salary data").classes(
        "text-caption"
    )
    if not summary:
        ui.label("Add salary to positions to see the distribution.").classes("text-caption")
        return

    currency = ui.toggle(list(salary.CURRENCIES), value=next(iter(summary))).classes("w-full")
    chart_container = ui.column().classes("w-full")

    def render_chart() -> None:
        chart_container.clear()
        labels, values = salary.salary_histogram(jobs, currency.value or "EUR")
        with chart_container:
            if not labels:
                ui.label("No salary data for this currency.").classes("text-caption")
                return
            options = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": labels},
                "yAxis": {"type": "value", "name": "Positions"},
                "series": [{"type": "bar", "name": "Positions", "data": values}],
            }
            ui.echart(options, theme="dark" if dark else None).classes("w-full").mark(
                "salary-chart"
            )

    currency.on_value_change(lambda _: render_chart())
    render_chart()
