"""Application bootstrap: config, logging, database, backup, and NiceGUI launch."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
from types import FrameType

from nicegui import app, ui

from cursustrace import backup, config, db
from cursustrace.logsetup import setup_logging
from cursustrace.web import state
from cursustrace.web.constants import APP_TITLE, load_webapp_css
from cursustrace.web.pages import register_pages

logger = logging.getLogger(__name__)


def _print_config_source(settings: config.Settings) -> None:
    """Tell the shell which configuration source the app resolved from."""
    if settings.config_file is not None:
        print(f"Config: using config file {settings.config_file}", flush=True)
    else:
        print("Config: no config file found, using hardcoded default values", flush=True)


def _backup_on_startup(settings: config.Settings) -> None:
    """Write a startup copy of the database; failures are logged, never fatal."""
    try:
        result = backup.backup_database(settings.backup_dir, keep=settings.backup_keep)
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Database backup failed")
        print(f"Backup failed: {exc}", flush=True)
        return
    if result is None:
        print("Backup skipped: no database file yet", flush=True)
        return
    logger.info("Database backup written to %s", result.path)
    pruned = ""
    if result.pruned:
        count = len(result.pruned)
        pruned = f" (pruned {count} old backup{'s' if count != 1 else ''})"
    print(f"Backup completed successfully to {result.path}{pruned}", flush=True)


def run(settings: config.Settings | None = None) -> None:
    """CLI entrypoint that launches the CursusTrace NiceGUI dashboard."""
    current = settings or config.load_settings()
    state._settings = current
    _print_config_source(current)
    setup_logging(current.log_level, retention_days=current.log_retention_days)
    try:
        db.init_db()
    except Exception:
        logger.exception("Failed to initialize the database")
        raise
    if current.backup_on_start:
        _backup_on_startup(current)
    else:
        print("Backup skipped: backup_on_start is disabled", flush=True)
    register_pages()
    ui.add_css(load_webapp_css(), shared=True)
    shutdown_signal: int | None = None

    def handle_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_signal
        shutdown_signal = signum
        app.shutdown()

    def install_handlers() -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handle_shutdown)
            except ValueError:
                pass

    # Register on startup so our handlers replace uvicorn's (and win).
    # The test simulator manages its own lifecycle and must not be intercepted.
    if os.environ.get("NICEGUI_USER_SIMULATION") != "true":
        app.on_startup(install_handlers)
    try:
        ui.run(
            title=APP_TITLE,
            favicon="📋",
            host=current.host,
            port=current.port,
            show=current.show,
            reload=current.reload,
        )
    except KeyboardInterrupt:
        shutdown_signal = signal.SIGINT
    if shutdown_signal is not None:
        print("\nCursusTrace stopped.")
        raise SystemExit(128 + shutdown_signal)
