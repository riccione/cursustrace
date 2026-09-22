"""Shared NiceGUI test fixtures for cursustrace."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from nicegui.testing import User, user_simulation

from cursustrace import db, scraper

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "cursustrace" / "app.py"


def fake_scrape(url: str) -> scraper.ScrapedJob:
    return {
        "title": "Senior Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Body text",
    }


@pytest.fixture
async def user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[User, None]:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(scraper, "scrape_job", fake_scrape)
    async with user_simulation(main_file=APP_PATH) as simulated_user:
        yield simulated_user
