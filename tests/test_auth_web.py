"""Web-layer auth tests: login, register, route protection, and isolation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from nicegui.storage import Storage
from nicegui.testing import User, user_simulation

from cursustrace import config, db, scraper
from cursustrace.auth import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
from cursustrace.web import run as web_run

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "cursustrace" / "app.py"


def _fake_scrape(url: str) -> scraper.ScrapedJob:
    return {
        "title": "Senior Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Body text",
        "deadline": None,
    }


def _setup_multi_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(web_run, "STORAGE_SECRET_PATH", tmp_path / ".storage-secret")
    monkeypatch.setattr(Storage, "path", tmp_path / ".nicegui")
    monkeypatch.setenv("CURSUS_MULTI_USER", "1")
    for name in (
        "CURSUS_BACKUP_DIR",
        "CURSUS_BACKUP_KEEP",
        "CURSUS_BACKUP_ON_START",
        "CURSUS_STALE_APPLIED_DAYS",
        "CURSUS_STALE_UNAPPLIED_DAYS",
        "CURSUS_DEADLINE_WARNING_DAYS",
        "CURSUS_OUTDATED_AFTER_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(scraper, "scrape_job", _fake_scrape)


@pytest.fixture
async def multi_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[User, None]:
    _setup_multi_user(monkeypatch, tmp_path)
    async with user_simulation(main_file=APP_PATH) as user:
        yield user


async def _register(user: User, email: str, password: str = "Secret1") -> None:
    await user.open("/register")
    user.find(marker="register-email").type(email)
    user.find(marker="register-password").type(password)
    user.find(marker="register-confirm").type(password)
    user.find(marker="register-submit").click()


async def _login(user: User, email: str, password: str) -> None:
    await user.open("/login")
    user.find(marker="login-email").type(email)
    user.find(marker="login-password").type(password)
    user.find(marker="login-submit").click()


async def test_anonymous_visits_redirect_to_login(multi_user: User) -> None:
    await multi_user.open("/")
    await multi_user.should_see(marker="login-email")


async def test_login_page_is_public(multi_user: User) -> None:
    await multi_user.open("/login")
    await multi_user.should_see(marker="login-email")


async def test_register_page_is_public(multi_user: User) -> None:
    await multi_user.open("/register")
    await multi_user.should_see(marker="register-email")


async def test_login_with_wrong_password_shows_error(multi_user: User) -> None:
    await multi_user.open("/login")
    multi_user.find(marker="login-email").type("nobody@example.com")
    multi_user.find(marker="login-password").type("Wrong1")
    multi_user.find(marker="login-submit").click()
    await multi_user.should_see(marker="login-error")


async def test_seeded_admin_can_sign_in(multi_user: User) -> None:
    await _login(multi_user, DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD)
    await multi_user.should_see(marker="scan-urls")


async def test_register_auto_logs_in(multi_user: User) -> None:
    await _register(multi_user, "alice@example.com")
    await multi_user.should_see(marker="scan-urls")


async def test_login_redirects_to_requested_path(multi_user: User) -> None:
    await multi_user.open("/login?redirect_to=%2Fjob%2F1")
    multi_user.find(marker="login-email").type(DEFAULT_ADMIN_EMAIL)
    multi_user.find(marker="login-password").type(DEFAULT_ADMIN_PASSWORD)
    multi_user.find(marker="login-submit").click()
    await multi_user.should_see("Position not found.")


async def test_register_rejects_duplicate_email(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_multi_user(monkeypatch, tmp_path)
    async with user_simulation(main_file=APP_PATH) as first:
        await _register(first, "alice@example.com")
        await first.should_see(marker="scan-urls")
    async with user_simulation(main_file=APP_PATH) as second:
        await second.open("/register")
        second.find(marker="register-email").type("alice@example.com")
        second.find(marker="register-password").type("Secret1")
        second.find(marker="register-confirm").type("Secret1")
        second.find(marker="register-submit").click()
        await second.should_see("An account with this email already exists.")


async def test_user_data_is_isolated_between_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_multi_user(monkeypatch, tmp_path)
    async with user_simulation(main_file=APP_PATH) as alice:
        await _register(alice, "alice@example.com")
        await alice.should_see(marker="scan-urls")
        row = db.get_user_by_email("alice@example.com")
        assert row is not None
        db.add_job(
            "https://example.com/alice-job",
            "Alice Role",
            "AliceCo",
            "Remote",
            "Body",
            user_id=row["id"],
        )
        await alice.open("/")
        await alice.should_see("Alice Role")
    async with user_simulation(main_file=APP_PATH) as bob:
        await _register(bob, "bob@example.com")
        await bob.should_see(marker="scan-urls")
        await bob.should_not_see("Alice Role")


async def test_seeded_admin_adopts_orphan_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_multi_user(monkeypatch, tmp_path)
    db.init_db()
    orphan = db.add_job(
        "https://example.com/orphan", "Orphan", "Co", "Remote", "Body", user_id=None
    )
    assert orphan is not None
    async with user_simulation(main_file=APP_PATH) as user:
        await _login(user, DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD)
        await user.should_see(marker="scan-urls")
        await user.should_see("Orphan")


async def test_multi_user_off_keeps_dashboard_open(user: User) -> None:
    """Regression guard: with the toggle off the dashboard still loads without login."""
    await user.open("/")
    await user.should_see(marker="scan-urls")


async def test_logout_returns_to_login(multi_user: User) -> None:
    await _register(multi_user, "alice@example.com")
    await multi_user.should_see(marker="scan-urls")
    multi_user.find(marker="logout-button").click()
    await multi_user.should_see(marker="login-email")


async def test_header_controls_hidden_when_multi_user_off(user: User) -> None:
    await user.open("/")
    await user.should_not_see(marker="logout-button")
    await user.should_not_see(marker="change-password-button")


async def test_change_password_rejects_wrong_current(multi_user: User) -> None:
    await _register(multi_user, "alice@example.com")
    await multi_user.should_see(marker="scan-urls")
    multi_user.find(marker="change-password-button").click()
    multi_user.find(marker="current-password").type("Wrong1")
    multi_user.find(marker="new-password").type("NewPass1")
    multi_user.find(marker="confirm-new-password").type("NewPass1")
    multi_user.find(marker="change-password-submit").click()
    await multi_user.should_see(marker="change-password-error")


async def test_change_password_allows_login_with_new_password(multi_user: User) -> None:
    await _register(multi_user, "alice@example.com")
    await multi_user.should_see(marker="scan-urls")
    multi_user.find(marker="change-password-button").click()
    multi_user.find(marker="current-password").type("Secret1")
    multi_user.find(marker="new-password").type("NewPass1")
    multi_user.find(marker="confirm-new-password").type("NewPass1")
    multi_user.find(marker="change-password-submit").click()
    await multi_user.should_see("Password changed.")
    multi_user.find(marker="logout-button").click()
    await multi_user.should_see(marker="login-email")
    multi_user.find(marker="login-email").type("alice@example.com")
    multi_user.find(marker="login-password").type("NewPass1")
    multi_user.find(marker="login-submit").click()
    await multi_user.should_see(marker="scan-urls")
