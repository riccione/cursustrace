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
    assert settings.config_file is None


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
    assert settings.config_file == config_file


def test_explicit_config_path_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "explicit.toml"
    path.write_text("[cursustrace]\nport = 9001\n", encoding="utf-8")

    settings = config.load_settings(config_path=path, env={})

    assert settings.port == 9001
    assert settings.config_file == path


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


def test_render_defaults_to_auto(config_file: Path) -> None:
    assert config.load_settings(env={}).render == "auto"


def test_render_file_overrides_default(config_file: Path) -> None:
    config_file.write_text('[cursustrace]\nrender = "never"\n', encoding="utf-8")

    assert config.load_settings(env={}).render == "never"


def test_render_env_and_flag_override(config_file: Path) -> None:
    config_file.write_text('[cursustrace]\nrender = "never"\n', encoding="utf-8")

    settings = config.load_settings(render="auto", env={"CURSUS_RENDER": "never"})

    assert settings.render == "auto"


def test_render_is_case_insensitive(config_file: Path) -> None:
    assert config.load_settings(env={"CURSUS_RENDER": "NEVER"}).render == "never"


@pytest.mark.parametrize("value", ["sometimes", "", "2"])
def test_invalid_render_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="render"):
        config.load_settings(env={"CURSUS_RENDER": value})


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_invalid_log_retention_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="log_retention_days"):
        config.load_settings(env={"CURSUS_LOG_RETENTION_DAYS": value})


def test_backup_defaults(config_file: Path) -> None:
    settings = config.load_settings(env={})

    assert settings.backup_dir == Path("backups")
    assert settings.backup_keep == 14
    assert settings.backup_on_start is True


def test_backup_file_overrides_defaults(config_file: Path) -> None:
    config_file.write_text(
        '[cursustrace]\nbackup_dir = "/srv/backups"\nbackup_keep = 3\nbackup_on_start = false\n',
        encoding="utf-8",
    )

    settings = config.load_settings(env={})

    assert settings.backup_dir == Path("/srv/backups")
    assert settings.backup_keep == 3
    assert settings.backup_on_start is False


def test_backup_env_and_flags_override(config_file: Path, tmp_path: Path) -> None:
    config_file.write_text('[cursustrace]\nbackup_dir = "/srv/backups"\n', encoding="utf-8")

    settings = config.load_settings(
        backup_dir=tmp_path / "flagged",
        backup_keep=5,
        backup_on_start=True,
        env={
            "CURSUS_BACKUP_DIR": "/srv/env",
            "CURSUS_BACKUP_KEEP": "9",
            "CURSUS_BACKUP_ON_START": "0",
        },
    )

    assert settings.backup_dir == tmp_path / "flagged"
    assert settings.backup_keep == 5
    assert settings.backup_on_start is True


def test_backup_env_only(config_file: Path, tmp_path: Path) -> None:
    settings = config.load_settings(
        env={"CURSUS_BACKUP_DIR": str(tmp_path / "env"), "CURSUS_BACKUP_KEEP": "2"}
    )

    assert settings.backup_dir == tmp_path / "env"
    assert settings.backup_keep == 2


def test_empty_backup_dir_falls_back_to_default(config_file: Path) -> None:
    config_file.write_text('[cursustrace]\nbackup_dir = ""\n', encoding="utf-8")

    assert config.load_settings(env={}).backup_dir == Path("backups")


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_invalid_backup_keep_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="backup_keep"):
        config.load_settings(env={"CURSUS_BACKUP_KEEP": value})


def test_attention_threshold_defaults(config_file: Path) -> None:
    settings = config.load_settings(env={})

    assert settings.stale_applied_days == 21
    assert settings.stale_unapplied_days == 10
    assert settings.deadline_warning_days == 7
    assert settings.outdated_after_days == 30


def test_attention_thresholds_file_overrides_defaults(config_file: Path) -> None:
    config_file.write_text(
        "[cursustrace]\n"
        "stale_applied_days = 14\n"
        "stale_unapplied_days = 5\n"
        "deadline_warning_days = 3\n"
        "outdated_after_days = 45\n",
        encoding="utf-8",
    )

    settings = config.load_settings(env={})

    assert settings.stale_applied_days == 14
    assert settings.stale_unapplied_days == 5
    assert settings.deadline_warning_days == 3
    assert settings.outdated_after_days == 45


def test_attention_thresholds_env_and_flags_override(config_file: Path) -> None:
    config_file.write_text("[cursustrace]\nstale_applied_days = 14\n", encoding="utf-8")

    settings = config.load_settings(
        stale_applied_days=30,
        deadline_warning_days=2,
        outdated_after_days=60,
        env={
            "CURSUS_STALE_APPLIED_DAYS": "10",
            "CURSUS_STALE_UNAPPLIED_DAYS": "6",
            "CURSUS_OUTDATED_AFTER_DAYS": "45",
        },
    )

    assert settings.stale_applied_days == 30
    assert settings.stale_unapplied_days == 6
    assert settings.deadline_warning_days == 2
    assert settings.outdated_after_days == 60


@pytest.mark.parametrize(
    ("env_name", "key"),
    [
        ("CURSUS_STALE_APPLIED_DAYS", "stale_applied_days"),
        ("CURSUS_STALE_UNAPPLIED_DAYS", "stale_unapplied_days"),
        ("CURSUS_DEADLINE_WARNING_DAYS", "deadline_warning_days"),
        ("CURSUS_OUTDATED_AFTER_DAYS", "outdated_after_days"),
    ],
)
def test_invalid_attention_threshold_raises(config_file: Path, env_name: str, key: str) -> None:
    with pytest.raises(ConfigError, match=key):
        config.load_settings(env={env_name: "-1"})


@pytest.mark.parametrize("value", ["abc", "-1"])
def test_invalid_deadline_warning_days_raises(config_file: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="deadline_warning_days"):
        config.load_settings(env={"CURSUS_DEADLINE_WARNING_DAYS": value})


def test_zero_attention_threshold_is_allowed(config_file: Path) -> None:
    settings = config.load_settings(env={"CURSUS_DEADLINE_WARNING_DAYS": "0"})

    assert settings.deadline_warning_days == 0
