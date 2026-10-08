"""Runtime configuration from defaults, a TOML file, environment, and flags."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cursustrace.errors import ConfigError

CONFIG_FILE = Path("cursustrace.toml")
CONFIG_TABLE = "cursustrace"

ENV_HOST = "CURSUS_HOST"
ENV_PORT = "CURSUS_PORT"
ENV_RELOAD = "CURSUS_RELOAD"
ENV_SHOW = "CURSUS_SHOW"
ENV_CV_STYLE = "CURSUS_CV_STYLE"
ENV_LOG_LEVEL = "CURSUS_LOG_LEVEL"
ENV_LOG_RETENTION_DAYS = "CURSUS_LOG_RETENTION_DAYS"
ENV_BACKUP_DIR = "CURSUS_BACKUP_DIR"
ENV_BACKUP_KEEP = "CURSUS_BACKUP_KEEP"
ENV_BACKUP_ON_START = "CURSUS_BACKUP_ON_START"
ENV_STALE_APPLIED_DAYS = "CURSUS_STALE_APPLIED_DAYS"
ENV_STALE_UNAPPLIED_DAYS = "CURSUS_STALE_UNAPPLIED_DAYS"
ENV_DEADLINE_WARNING_DAYS = "CURSUS_DEADLINE_WARNING_DAYS"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_CV_STYLE = Path("styles/cv.css")
DEFAULT_LOG_LEVEL = "error"
DEFAULT_LOG_RETENTION_DAYS = 7
DEFAULT_BACKUP_DIR = Path("backups")
DEFAULT_BACKUP_KEEP = 14
DEFAULT_BACKUP_ON_START = True
DEFAULT_STALE_APPLIED_DAYS = 21
DEFAULT_STALE_UNAPPLIED_DAYS = 10
DEFAULT_DEADLINE_WARNING_DAYS = 7
LOG_DIR = Path("logs")
LOG_LEVELS = ("debug", "info", "warning", "error", "critical")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    reload: bool = False
    show: bool = False
    cv_style_path: Path = DEFAULT_CV_STYLE
    log_level: str = DEFAULT_LOG_LEVEL
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    backup_dir: Path = DEFAULT_BACKUP_DIR
    backup_keep: int = DEFAULT_BACKUP_KEEP
    backup_on_start: bool = DEFAULT_BACKUP_ON_START
    stale_applied_days: int = DEFAULT_STALE_APPLIED_DAYS
    stale_unapplied_days: int = DEFAULT_STALE_UNAPPLIED_DAYS
    deadline_warning_days: int = DEFAULT_DEADLINE_WARNING_DAYS
    config_file: Path | None = None


def load_settings(
    *,
    host: str | None = None,
    port: int | None = None,
    reload: bool | None = None,
    show: bool | None = None,
    cv_style_path: str | Path | None = None,
    log_level: str | None = None,
    log_retention_days: int | str | None = None,
    backup_dir: str | Path | None = None,
    backup_keep: int | str | None = None,
    backup_on_start: bool | None = None,
    stale_applied_days: int | str | None = None,
    stale_unapplied_days: int | str | None = None,
    deadline_warning_days: int | str | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Merge defaults, `cursustrace.toml`, `CURSUS_*` env vars, and explicit overrides."""
    environ = os.environ if env is None else env
    values, config_file = _read_config(config_path)

    for key, env_name in (
        ("host", ENV_HOST),
        ("port", ENV_PORT),
        ("reload", ENV_RELOAD),
        ("show", ENV_SHOW),
        ("cv_style", ENV_CV_STYLE),
        ("log_level", ENV_LOG_LEVEL),
        ("log_retention_days", ENV_LOG_RETENTION_DAYS),
        ("backup_dir", ENV_BACKUP_DIR),
        ("backup_keep", ENV_BACKUP_KEEP),
        ("backup_on_start", ENV_BACKUP_ON_START),
        ("stale_applied_days", ENV_STALE_APPLIED_DAYS),
        ("stale_unapplied_days", ENV_STALE_UNAPPLIED_DAYS),
        ("deadline_warning_days", ENV_DEADLINE_WARNING_DAYS),
    ):
        raw = environ.get(env_name)
        if raw is not None:
            values[key] = raw

    if host is not None:
        values["host"] = host
    if port is not None:
        values["port"] = port
    if reload is not None:
        values["reload"] = reload
    if show is not None:
        values["show"] = show
    if cv_style_path is not None:
        values["cv_style"] = cv_style_path
    if log_level is not None:
        values["log_level"] = log_level
    if log_retention_days is not None:
        values["log_retention_days"] = log_retention_days
    if backup_dir is not None:
        values["backup_dir"] = backup_dir
    if backup_keep is not None:
        values["backup_keep"] = backup_keep
    if backup_on_start is not None:
        values["backup_on_start"] = backup_on_start
    if stale_applied_days is not None:
        values["stale_applied_days"] = stale_applied_days
    if stale_unapplied_days is not None:
        values["stale_unapplied_days"] = stale_unapplied_days
    if deadline_warning_days is not None:
        values["deadline_warning_days"] = deadline_warning_days

    return Settings(
        host=_as_str(values, "host", DEFAULT_HOST),
        port=_as_port(values.get("port", DEFAULT_PORT)),
        reload=_as_bool(values, "reload", False),
        show=_as_bool(values, "show", False),
        cv_style_path=Path(_as_str(values, "cv_style", str(DEFAULT_CV_STYLE))),
        log_level=_as_log_level(values, "log_level", DEFAULT_LOG_LEVEL),
        log_retention_days=_as_retention_days(
            values.get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)
        ),
        backup_dir=_as_backup_dir(values.get("backup_dir"), DEFAULT_BACKUP_DIR),
        backup_keep=_as_backup_keep(values.get("backup_keep", DEFAULT_BACKUP_KEEP)),
        backup_on_start=_as_bool(values, "backup_on_start", DEFAULT_BACKUP_ON_START),
        stale_applied_days=_as_stale_applied_days(
            values.get("stale_applied_days", DEFAULT_STALE_APPLIED_DAYS)
        ),
        stale_unapplied_days=_as_stale_unapplied_days(
            values.get("stale_unapplied_days", DEFAULT_STALE_UNAPPLIED_DAYS)
        ),
        deadline_warning_days=_as_deadline_warning_days(
            values.get("deadline_warning_days", DEFAULT_DEADLINE_WARNING_DAYS)
        ),
        config_file=config_file,
    )


def _read_config(config_path: str | Path | None) -> tuple[dict[str, object], Path | None]:
    path = Path(config_path) if config_path is not None else CONFIG_FILE
    if not path.exists():
        if config_path is not None:
            raise ConfigError(f"Config file not found: {path}")
        return {}, None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    table = data.get(CONFIG_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{CONFIG_TABLE}] must be a table in {path}")
    return {str(key): value for key, value in table.items()}, path


def _as_str(values: dict[str, object], key: str, default: str) -> str:
    if key not in values:
        return default
    value = values[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a non-empty string")
    return value


def _as_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError("'port' must be an integer")
    try:
        port = int(value)
    except ValueError as exc:
        raise ConfigError("'port' must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("'port' must be between 1 and 65535")
    return port


def _as_bool(values: dict[str, object], key: str, default: bool) -> bool:
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    raise ConfigError(f"'{key}' must be a boolean")


def _as_log_level(values: dict[str, object], key: str, default: str) -> str:
    if key not in values:
        return default
    value = values[key]
    if isinstance(value, str) and value.strip().lower() in LOG_LEVELS:
        return value.strip().lower()
    raise ConfigError(f"'{key}' must be one of: {', '.join(LOG_LEVELS)}")


def _non_negative_int(key: str) -> Callable[[object], int]:
    """Build a coercer that accepts a non-negative integer for the named setting."""

    def coerce(value: object) -> int:
        message = f"'{key}' must be a non-negative integer"
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ConfigError(message)
        try:
            days = int(value)
        except ValueError as exc:
            raise ConfigError(message) from exc
        if days < 0:
            raise ConfigError(message)
        return days

    return coerce


def _as_backup_dir(value: object, default: Path) -> Path:
    if value is None:
        return default
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        return Path(stripped) if stripped else default
    raise ConfigError("'backup_dir' must be a non-empty string")


_as_retention_days = _non_negative_int("log_retention_days")
_as_backup_keep = _non_negative_int("backup_keep")
_as_stale_applied_days = _non_negative_int("stale_applied_days")
_as_stale_unapplied_days = _non_negative_int("stale_unapplied_days")
_as_deadline_warning_days = _non_negative_int("deadline_warning_days")
