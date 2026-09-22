"""SQLite persistence layer for cursustrace."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/cursustrace.db")


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection, creating the parent directory and database on first use."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)
