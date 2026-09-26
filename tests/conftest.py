"""Shared NiceGUI test fixtures for cursustrace."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
from nicegui.testing import User, user_simulation

from cursustrace import config, db, logsetup, scraper

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "cursustrace" / "app.py"


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    logsetup._remove_handlers()
    logsetup._configured = None
    logging.getLogger().setLevel(logging.WARNING)


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
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(scraper, "scrape_job", fake_scrape)
    async with user_simulation(main_file=APP_PATH) as simulated_user:
        yield simulated_user
