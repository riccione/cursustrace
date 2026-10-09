"""Page route registration; called from run() on every entry path."""

from __future__ import annotations

from nicegui import ui

from cursustrace.web.dashboard import dashboard_page
from cursustrace.web.detail import job_detail_page


def register_pages() -> None:
    """Register every page route; NiceGUI replaces routes registered earlier."""
    ui.page("/")(dashboard_page)
    ui.page("/job/{job_id}")(job_detail_page)
