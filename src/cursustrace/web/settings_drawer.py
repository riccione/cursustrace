"""Settings drawer: appearance, notification toggles, tags, and maintenance."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import events, ui

from cursustrace import db
from cursustrace.web.dialogs import _clear_database, _tag_manager_dialog
from cursustrace.web.state import (
    ATTENTION_NOTICE_KEY,
    DARK_MODE_KEY,
    DARK_MODE_OPTIONS,
    SIMILAR_NOTICE_KEY,
    _attention_banner_enabled,
    _dark_mode_name,
    _dark_mode_value,
    _similar_notice_enabled,
)


def _render_settings(refresh: Callable[[], None], dark: ui.dark_mode) -> None:
    ui.label("Appearance").classes("text-h6")
    theme = ui.toggle(DARK_MODE_OPTIONS, value=_dark_mode_name(dark.value)).classes("w-full")

    def update_theme(event: events.ValueChangeEventArguments[str | None]) -> None:
        name = event.value or "system"
        dark.value = _dark_mode_value(name)
        db.set_setting(DARK_MODE_KEY, name)
        refresh()

    theme.on_value_change(update_theme)

    ui.separator()
    ui.label("Notifications").classes("text-h6")
    similar_toggle = ui.switch(
        "Notify about similar positions", value=_similar_notice_enabled()
    ).classes("w-full")

    def update_similar(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_setting(SIMILAR_NOTICE_KEY, "on" if event.value else "off")

    similar_toggle.on_value_change(update_similar)

    attention_toggle = (
        ui.switch("Show needs-attention banner", value=_attention_banner_enabled())
        .classes("w-full")
        .mark("attention-banner-toggle")
    )

    def update_attention(event: events.ValueChangeEventArguments[bool | None]) -> None:
        db.set_setting(ATTENTION_NOTICE_KEY, "on" if event.value else "off")
        refresh()

    attention_toggle.on_value_change(update_attention)

    ui.separator()
    ui.label("🏷️ Tags").classes("text-h6")
    tag_manager = _tag_manager_dialog(refresh)
    ui.button("Manage tags", on_click=tag_manager.open).mark("manage-tags")

    ui.separator()
    ui.label("⚙️ Settings & Maintenance").classes("text-h6")
    with ui.expansion("⚠️ Danger Zone: Clear Database"):
        ui.label(
            "⚠️ This action cannot be undone. All tracked job postings and "
            "application history will be permanently erased."
        ).classes("text-negative")
        confirm = ui.input("Type 'DELETE' to confirm", placeholder="DELETE").classes("w-full")
        button = ui.button(
            "Confirm & Clear All Data",
            on_click=lambda: _clear_database(refresh),
        ).props("color=negative")
        button.enabled = False

        def update_button(event: events.ValueChangeEventArguments[str | None]) -> None:
            button.enabled = event.value == "DELETE"

        confirm.on_value_change(update_button)
