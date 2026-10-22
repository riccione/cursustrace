"""Shared page navigation helpers."""

from __future__ import annotations

from nicegui import ui

from cursustrace.version import app_version
from cursustrace.web.constants import APP_NAME


async def _scroll_to_top() -> None:
    await ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'})")


def scroll_to_top_button() -> None:
    """Bottom-of-page button that scrolls the viewport back to the top."""
    ui.button("Go up", icon="arrow_upward", on_click=_scroll_to_top).props("flat").classes(
        "self-center q-mt-md"
    ).mark("scroll-to-top")


def app_footer() -> None:
    """Inline footer at the bottom of the page content (seen when scrolled to)."""
    with ui.column().classes("w-full gap-0 q-mt-lg").mark("app-footer"):
        ui.separator()
        with ui.row().classes("w-full justify-center q-py-sm"):
            ui.label(f"{APP_NAME} v{app_version()}").classes("text-caption ct-text-secondary")
