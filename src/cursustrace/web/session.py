"""Per-session identity helpers reading NiceGUI's user storage."""

from __future__ import annotations

from nicegui import app
from nicegui.storage import request_contextvar


def _stored() -> dict[str, object] | None:
    """Return the session's user dict, or None when storage is unavailable."""
    request = request_contextvar.get()
    if request is None or "session" not in request.scope:
        return None
    try:
        return app.storage.user
    except RuntimeError:
        return None


def current_user_id() -> int | None:
    """Return the logged-in account id, or None when signed out or single-user."""
    stored = _stored()
    if stored is None:
        return None
    value = stored.get("id")
    return value if isinstance(value, int) else None


def current_email() -> str | None:
    """Return the logged-in account email, or None when signed out or single-user."""
    stored = _stored()
    if stored is None:
        return None
    value = stored.get("email")
    return value if isinstance(value, str) else None
