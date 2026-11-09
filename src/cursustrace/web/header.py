"""Shared top navigation bar: brand, main sections, account controls, settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nicegui import ui

from cursustrace.web.auth_pages import _change_password_dialog, _multi_user_enabled, logout
from cursustrace.web.constants import APP_NAME
from cursustrace.web.session import current_email
from cursustrace.web.settings_drawer import _render_settings


@dataclass(frozen=True)
class Section:
    slug: str
    label: str
    icon: str


SECTIONS = (
    Section("dashboard", "Dashboard", "assignment"),
    Section("statistics", "Statistics", "bar_chart"),
    Section("profile", "Profile & CV Editor", "person"),
    Section("discover", "Discover", "search"),
)
SECTION_SLUGS = {section.slug: section.label for section in SECTIONS}


def _go_to_section(slug: str) -> Callable[[], None]:
    return lambda: ui.navigate.to(f"/?tab={slug}")


def app_header(
    refresh: Callable[[], None],
    dark: ui.dark_mode,
    *,
    tabs: bool = True,
) -> ui.tabs | None:
    """Render the shared top bar; return the tab strip on the dashboard."""
    tabs_element: ui.tabs | None = None
    with ui.header().classes("ct-app-header items-center flex-nowrap"):
        ui.label(APP_NAME).classes("ct-app-brand flex-1 truncate min-w-0")
        if tabs:
            with ui.tabs().props("dense").classes("header-tabs") as tabs_element:
                for section in SECTIONS:
                    ui.tab(section.label, icon=section.icon).props("no-caps")
        else:
            with ui.row().classes("ct-header-links items-center gap-1"):
                for section in SECTIONS:
                    ui.button(
                        section.label,
                        icon=section.icon,
                        on_click=_go_to_section(section.slug),
                    ).props("flat dense no-caps").classes("ct-header-link")
        with ui.element("div").classes("flex-1 flex justify-end min-w-0 items-center gap-2"):
            if _multi_user_enabled():
                email = current_email()
                if email:
                    ui.label(email).classes(
                        "text-caption ct-text-secondary truncate max-w-48"
                    ).mark("header-email")
                change_password_dialog = _change_password_dialog()
                ui.button("Change password", on_click=change_password_dialog.open).props(
                    "flat dense"
                ).classes("ct-ghost-btn").mark("change-password-button")
                ui.button("Logout", on_click=logout).props("flat dense").classes(
                    "ct-ghost-btn"
                ).mark("logout-button")
            settings_button = (
                ui.button(icon="settings")
                .props("flat dense")
                .classes("ct-ghost-btn")
                .mark("settings-button")
            )

    with ui.right_drawer(value=False).props("width=480") as drawer:
        settings_button.on_click(lambda: drawer.toggle())
        _render_settings(refresh, dark)
    return tabs_element
