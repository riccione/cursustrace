"""Runtime settings state and dashboard preference helpers."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from cursustrace import attention, config, db
from cursustrace.web.session import current_user_id

_settings: config.Settings | None = None

DARK_MODE_KEY = "dark_mode"
SIMILAR_NOTICE_KEY = "similar_notice"
ATTENTION_NOTICE_KEY = "attention_notice"
SELECTED_PROFILE_KEY = "selected_profile_id"
DARK_MODE_OPTIONS: dict[str, str] = {
    "light": "Light",
    "dark": "Dark",
    "system": "System",
}
DARK_MODE_ICONS: dict[str, str] = {
    "light": "light_mode",
    "dark": "dark_mode",
    "system": "computer",
}


def _cv_style_path() -> Path | None:
    return _settings.cv_style_path if _settings is not None else None


def _similar_notice_enabled() -> bool:
    return db.get_setting(SIMILAR_NOTICE_KEY, "on", user_id=current_user_id()) != "off"


def _attention_notice_enabled() -> bool:
    return db.get_setting(ATTENTION_NOTICE_KEY, "on", user_id=current_user_id()) != "off"


def _attention_thresholds() -> attention.Thresholds:
    settings = _settings if _settings is not None else config.load_settings()
    return attention.Thresholds.from_settings(settings)


def _dark_mode_value(name: str | None) -> bool | None:
    if name == "dark":
        return True
    if name == "light":
        return False
    return None


def _dark_mode_name(value: bool | None) -> str:
    if value is None:
        return "system"
    return "dark" if value else "light"


def _apply_dark_mode() -> ui.dark_mode:
    name = db.get_setting(DARK_MODE_KEY, "system", user_id=current_user_id())
    return ui.dark_mode(value=_dark_mode_value(name))
