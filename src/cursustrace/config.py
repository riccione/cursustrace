"""Runtime configuration from defaults, a TOML file, environment, and flags."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
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

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_CV_STYLE = Path("styles/cv.css")

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


def load_settings(
    *,
    host: str | None = None,
    port: int | None = None,
    reload: bool | None = None,
    show: bool | None = None,
    cv_style_path: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Merge defaults, `cursustrace.toml`, `CURSUS_*` env vars, and explicit overrides."""
    environ = os.environ if env is None else env
    values = _read_config(config_path)

    for key, env_name in (
        ("host", ENV_HOST),
        ("port", ENV_PORT),
        ("reload", ENV_RELOAD),
        ("show", ENV_SHOW),
        ("cv_style", ENV_CV_STYLE),
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

    return Settings(
        host=_as_str(values, "host", DEFAULT_HOST),
        port=_as_port(values.get("port", DEFAULT_PORT)),
        reload=_as_bool(values, "reload", False),
        show=_as_bool(values, "show", False),
        cv_style_path=Path(_as_str(values, "cv_style", str(DEFAULT_CV_STYLE))),
    )


def _read_config(config_path: str | Path | None) -> dict[str, object]:
    path = Path(config_path) if config_path is not None else CONFIG_FILE
    if not path.exists():
        if config_path is not None:
            raise ConfigError(f"Config file not found: {path}")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    table = data.get(CONFIG_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{CONFIG_TABLE}] must be a table in {path}")
    return {str(key): value for key, value in table.items()}


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
