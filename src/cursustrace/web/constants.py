"""Dashboard constants: pipeline stage labels, stat cards, and the webapp stylesheet."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cursustrace import db

APP_NAME = "CursusTrace"
APP_TITLE = "CursusTrace — Job Application Tracker"
# Inline SVG (a clipboard on the brand indigo) so the browser tab icon is not an
# emoji, which renders differently per OS.
APP_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#4F46E5"/>'
    '<rect x="9" y="10" width="14" height="15" rx="2" fill="#FFFFFF"/>'
    '<rect x="12" y="7" width="8" height="4" rx="1.5" fill="#FFFFFF"/>'
    '<path d="M12.5 16.5h7M12.5 20h5" stroke="#4F46E5" stroke-width="1.6" '
    'stroke-linecap="round"/>'
    "</svg>"
)

STAGE_ICONS: dict[db.JobStatus, str] = {
    "unapplied": "hourglass_empty",
    "applied": "check_circle",
    "interview": "forum",
    "offer": "emoji_events",
    "rejected": "cancel",
    "outdated": "inventory_2",
}
"""Material icon for each pipeline stage (emojis render differently per OS)."""

STATUS_TABS: tuple[tuple[db.JobStatus, str, str], ...] = (
    ("unapplied", "Unapplied", "No unapplied positions yet."),
    ("applied", "Applied", "No applied positions yet."),
    ("interview", "Interview", "No interview positions yet."),
    ("offer", "Offer", "No offer positions yet."),
    ("rejected", "Rejected", "No rejected positions yet."),
    ("outdated", "Outdated", "No outdated positions yet."),
)

# Stages shown as tabs; the remaining ones live behind the "More" menu.
PRIMARY_STATUSES: tuple[db.JobStatus, ...] = ("unapplied", "applied", "interview", "offer")
SECONDARY_STATUSES: tuple[db.JobStatus, ...] = ("rejected", "outdated")

STATUS_CHECKBOXES: tuple[tuple[db.JobFlag | Literal["outdated"], str], ...] = (
    ("applied", "Applied"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
    ("outdated", "Outdated"),
)

STAT_CARDS: tuple[tuple[str, str], ...] = (
    ("total", "Total positions"),
    ("unapplied", "Unapplied"),
    ("applied", "Applied"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
    ("outdated", "Outdated"),
)

STAT_CARD_ICONS: dict[str, str] = {
    "total": "assignment",
    **{str(status): icon for status, icon in STAGE_ICONS.items()},
}

STATUS_NAMES: tuple[tuple[db.JobStatus, str], ...] = (
    ("unapplied", "Unapplied"),
    ("applied", "Applied"),
    ("interview", "Interview"),
    ("offer", "Offer"),
    ("rejected", "Rejected"),
    ("outdated", "Outdated"),
)

FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    ("added", "Added"),
    ("applied", "Applied"),
    ("response", "Response"),
    ("interview", "Interview"),
    ("offer", "Offer"),
)

WEBAPP_CSS_PATH: Path = Path("styles/webapp.css")


def load_webapp_css(path: Path | None = None) -> str:
    """Read the customizable webapp stylesheet; exit with guidance when missing."""
    stylesheet = path if path is not None else WEBAPP_CSS_PATH
    try:
        return stylesheet.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Webapp stylesheet not found at {stylesheet} — "
            "run cursustrace from the repository root"
        ) from exc
