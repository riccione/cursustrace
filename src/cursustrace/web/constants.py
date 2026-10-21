"""Dashboard constants: pipeline stage labels, stat cards, and the webapp stylesheet."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from cursustrace import db

APP_NAME = "CursusTrace"
APP_TITLE = "CursusTrace — Job Application Tracker"

STATUS_TABS: tuple[tuple[db.JobStatus, str, str], ...] = (
    ("unapplied", "⏳ Unapplied", "No unapplied positions yet."),
    ("applied", "✅ Applied", "No applied positions yet."),
    ("interview", "🗣️ Interview", "No interview positions yet."),
    ("offer", "🎉 Offer", "No offer positions yet."),
    ("rejected", "❌ Rejected", "No rejected positions yet."),
    ("outdated", "🗄️ Outdated", "No outdated positions yet."),
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
    ("total", "📋 Total positions"),
    ("unapplied", "⏳ Unapplied"),
    ("applied", "✅ Applied"),
    ("interview", "🗣️ Interview"),
    ("offer", "🎉 Offer"),
    ("rejected", "❌ Rejected"),
    ("outdated", "🗄️ Outdated"),
)

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
