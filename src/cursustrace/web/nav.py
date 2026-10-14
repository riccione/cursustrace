"""Shared page navigation helpers."""

from __future__ import annotations

from nicegui import ui


async def _scroll_to_top() -> None:
    await ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'})")


def scroll_to_top_button() -> None:
    """Bottom-of-page button that scrolls the viewport back to the top."""
    ui.button("↑ Go up", on_click=_scroll_to_top).props("flat").classes("self-center q-mt-md").mark(
        "scroll-to-top"
    )
