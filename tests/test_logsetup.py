"""Tests for local file logging setup."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pytest

from cursustrace import logsetup


def _log_file(log_dir: Path) -> Path:
    return log_dir / f"cursustrace-{logsetup._today().isoformat()}.log"


def test_setup_creates_daily_file(tmp_path: Path) -> None:
    path = logsetup.setup_logging("error", tmp_path)

    assert path == _log_file(tmp_path)
    assert path is not None and path.exists()


def test_error_is_written_with_traceback(tmp_path: Path) -> None:
    logsetup.setup_logging("error", tmp_path)
    logging.getLogger("cursustrace.test").error("boom")
    try:
        raise ValueError("nope")
    except ValueError:
        logging.getLogger("cursustrace.test").exception("with traceback")

    content = _log_file(tmp_path).read_text(encoding="utf-8")

    assert "cursustrace.test" in content
    assert "ERROR" in content
    assert "boom" in content
    assert "with traceback" in content
    assert "ValueError: nope" in content


def test_info_is_filtered_out_at_error(tmp_path: Path) -> None:
    logsetup.setup_logging("error", tmp_path)
    logging.getLogger("cursustrace.test").info("quiet noise")

    assert "quiet noise" not in _log_file(tmp_path).read_text(encoding="utf-8")


def test_info_is_written_at_info(tmp_path: Path) -> None:
    logsetup.setup_logging("info", tmp_path)
    logging.getLogger("cursustrace.test").info("loud enough")

    assert "loud enough" in _log_file(tmp_path).read_text(encoding="utf-8")


def test_server_errors_are_captured(tmp_path: Path) -> None:
    logsetup.setup_logging("error", tmp_path)
    logging.getLogger(logsetup.SERVER_LOGGER).error("server boom")

    assert "server boom" in _log_file(tmp_path).read_text(encoding="utf-8")


def test_setup_is_idempotent(tmp_path: Path) -> None:
    logsetup.setup_logging("error", tmp_path)
    logsetup.setup_logging("error", tmp_path)

    file_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.FileHandler)
        and handler.baseFilename == str(_log_file(tmp_path))
    ]
    assert len(file_handlers) == 1


def test_reconfigure_applies_new_level(tmp_path: Path) -> None:
    logsetup.setup_logging("error", tmp_path)
    logsetup.setup_logging("debug", tmp_path)
    logging.getLogger("cursustrace.test").debug("now visible")

    assert "now visible" in _log_file(tmp_path).read_text(encoding="utf-8")


def test_prune_removes_files_older_than_window(tmp_path: Path) -> None:
    today = logsetup._today()
    kept = [
        tmp_path / f"cursustrace-{today.isoformat()}.log",
        tmp_path / f"cursustrace-{(today - timedelta(days=7)).isoformat()}.log",
    ]
    removed = tmp_path / f"cursustrace-{(today - timedelta(days=8)).isoformat()}.log"
    for path in [*kept, removed]:
        path.write_text("old", encoding="utf-8")

    logsetup.setup_logging("error", tmp_path, retention_days=7)

    assert all(path.exists() for path in kept)
    assert not removed.exists()


def test_prune_disabled_with_zero(tmp_path: Path) -> None:
    old = tmp_path / f"cursustrace-{(logsetup._today() - timedelta(days=30)).isoformat()}.log"
    old.write_text("ancient", encoding="utf-8")

    logsetup.setup_logging("error", tmp_path, retention_days=0)

    assert old.exists()


def test_unwritable_log_dir_returns_none(tmp_path: Path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")

    assert logsetup.setup_logging("error", blocker) is None


def test_invalid_level_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown log level"):
        logsetup.setup_logging("verbose", tmp_path)
