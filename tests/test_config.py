"""Tests for runtime configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from cursustrace import config
from cursustrace.errors import ConfigError


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cursustrace.toml"
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    return path


def test_defaults(config_file: Path) -> None:
    settings = config.load_settings(env={})

    assert settings == config.Settings()


def test_file_overrides_defaults(config_file: Path) -> None:
    config_file.write_text(
        "[cursustrace]\n"
        'host = "127.0.0.1"\n'
        "port = 9000\n"
        "reload = true\n"
        "show = true\n"
        'cv_style = "custom.css"\n',
        encoding="utf-8",
    )

    settings = config.load_settings(env={})

    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.reload is True
    assert settings.show is True
    assert settings.cv_style_path == Path("custom.css")


def test_env_overrides_file(config_file: Path) -> None:
    config_file.write_text('[cursustrace]\nhost = "127.0.0.1"\nport = 9000\n', encoding="utf-8")

    settings = config.load_settings(env={"CURSUS_HOST": "0.0.0.0", "CURSUS_PORT": "9100"})

    assert settings.host == "0.0.0.0"
    assert settings.port == 9100


def test_flags_override_env(config_file: Path) -> None:
    settings = config.load_settings(
        host="192.168.0.1",
        port=9200,
        env={"CURSUS_HOST": "0.0.0.0", "CURSUS_PORT": "9100"},
    )

    assert settings.host == "192.168.0.1"
    assert settings.port == 9200


@pytest.mark.parametrize("value", ["0", "70000", "abc"])
def test_invalid_port_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="port"):
        config.load_settings(env={"CURSUS_PORT": value})


def test_invalid_bool_raises(config_file: Path) -> None:
    with pytest.raises(ConfigError, match="reload"):
        config.load_settings(env={"CURSUS_RELOAD": "maybe"})


@pytest.mark.parametrize("value", ["yes", "on", "1"])
def test_env_bool_true(config_file: Path, value: str) -> None:
    assert config.load_settings(env={"CURSUS_RELOAD": value}).reload is True


@pytest.mark.parametrize("value", ["no", "off", "0"])
def test_env_bool_false(config_file: Path, value: str) -> None:
    assert config.load_settings(env={"CURSUS_SHOW": value}).show is False


def test_missing_explicit_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        config.load_settings(config_path=tmp_path / "nope.toml", env={})


def test_invalid_toml_raises(config_file: Path) -> None:
    config_file.write_text("[cursustrace\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Could not read"):
        config.load_settings(env={})


def test_logging_defaults(config_file: Path) -> None:
    settings = config.load_settings(env={})

    assert settings.log_level == "error"
    assert settings.log_retention_days == 7


def test_logging_file_overrides_defaults(config_file: Path) -> None:
    config_file.write_text(
        '[cursustrace]\nlog_level = "warning"\nlog_retention_days = 3\n',
        encoding="utf-8",
    )

    settings = config.load_settings(env={})

    assert settings.log_level == "warning"
    assert settings.log_retention_days == 3


def test_logging_env_and_flags_override(config_file: Path) -> None:
    config_file.write_text('[cursustrace]\nlog_level = "warning"\n', encoding="utf-8")

    settings = config.load_settings(
        log_level="debug",
        log_retention_days=10,
        env={"CURSUS_LOG_LEVEL": "info", "CURSUS_LOG_RETENTION_DAYS": "5"},
    )

    assert settings.log_level == "debug"
    assert settings.log_retention_days == 10


def test_log_level_is_case_insensitive(config_file: Path) -> None:
    assert config.load_settings(env={"CURSUS_LOG_LEVEL": "DEBUG"}).log_level == "debug"


@pytest.mark.parametrize("value", ["verbose", "", "2"])
def test_invalid_log_level_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="log_level"):
        config.load_settings(env={"CURSUS_LOG_LEVEL": value})


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_invalid_log_retention_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="log_retention_days"):
        config.load_settings(env={"CURSUS_LOG_RETENTION_DAYS": value})
