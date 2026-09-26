"""Local file logging configured from cursustrace settings."""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from cursustrace import config

LOG_FILE_PREFIX = "cursustrace-"
LOG_FILE_SUFFIX = ".log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
SERVER_LOGGER = "uvicorn.error"

_handlers: list[logging.Handler] = []
_configured: tuple[str, Path, int, bool] | None = None


def _level(level: str) -> int:
    value = logging.getLevelNamesMapping().get(level.upper())
    if value is None:
        raise ValueError(f"Unknown log level: {level}")
    return value


def _today() -> date:
    now = time.localtime()
    return date(now.tm_year, now.tm_mon, now.tm_mday)


def _log_path(log_dir: Path, on: date | None = None) -> Path:
    return log_dir / f"{LOG_FILE_PREFIX}{(on or _today()).isoformat()}{LOG_FILE_SUFFIX}"


def _prune(log_dir: Path, retention_days: int) -> None:
    """Delete daily log files older than the retention window."""
    if retention_days <= 0:
        return
    cutoff = _today() - timedelta(days=retention_days)
    for path in log_dir.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_SUFFIX}"):
        stamp = path.name[len(LOG_FILE_PREFIX) : -len(LOG_FILE_SUFFIX)]
        try:
            logged = date.fromisoformat(stamp)
        except ValueError:
            continue
        if logged < cutoff:
            try:
                path.unlink()
            except OSError:
                pass


def _remove_handlers() -> None:
    root = logging.getLogger()
    server = logging.getLogger(SERVER_LOGGER)
    for handler in _handlers:
        root.removeHandler(handler)
        server.removeHandler(handler)
    _handlers.clear()


def setup_logging(
    level: str,
    log_dir: Path | None = None,
    retention_days: int = config.DEFAULT_LOG_RETENTION_DAYS,
    *,
    console: bool = True,
) -> Path | None:
    """Attach a daily file handler (plus optional stderr) to the root and uvicorn error loggers."""
    global _configured
    target = log_dir if log_dir is not None else config.LOG_DIR
    numeric = _level(level)
    if _configured == (level, target, retention_days, console):
        return _log_path(target)

    _remove_handlers()
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = _log_path(target)
        formatter = logging.Formatter(LOG_FORMAT)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(numeric)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(numeric)
    except OSError as exc:
        print(f"Could not set up file logging in {target}: {exc}", file=sys.stderr)
        return None

    root = logging.getLogger()
    root.setLevel(numeric)
    root.addHandler(file_handler)
    _handlers.append(file_handler)
    if console:
        root.addHandler(stream_handler)
        _handlers.append(stream_handler)
    # `uvicorn` disables propagation, so route server errors to the file explicitly.
    logging.getLogger(SERVER_LOGGER).addHandler(file_handler)
    _configured = (level, target, retention_days, console)
    _prune(target, retention_days)
    return path
