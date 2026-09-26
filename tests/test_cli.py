"""Tests for the cursustrace command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cursustrace import cli as cli_module
from cursustrace import config, db, logsetup, scraper
from cursustrace.errors import ScrapeError

ADD_ARGS = [
    "add",
    "--url",
    "https://a.com/1",
    "--title",
    "Engineer",
    "--company",
    "Acme",
    "--description",
    "Body",
]

_CURSUS_ENV = (
    "CURSUS_HOST",
    "CURSUS_PORT",
    "CURSUS_RELOAD",
    "CURSUS_SHOW",
    "CURSUS_CV_STYLE",
    "CURSUS_LOG_LEVEL",
    "CURSUS_LOG_RETENTION_DAYS",
)


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "cursustrace.toml")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    for name in _CURSUS_ENV:
        monkeypatch.delenv(name, raising=False)
    return CliRunner()


def _varying_scrape(url: str) -> scraper.ScrapedJob:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return {
        "title": f"Scraped {slug}",
        "company": "ScrapeCo",
        "location": "Remote",
        "description": f"Body {slug}",
    }


def _profile() -> db.Profile:
    return {
        "full_name": "Jane Doe",
        "location": "Remote",
        "phone": "555-0100",
        "email": "jane@example.com",
        "linkedin_url": "",
        "github_url": "",
        "cv_markdown": "# Jane",
        "date_updated": None,
    }


def test_add_inserts_position(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ADD_ARGS)

    assert result.exit_code == 0
    assert "Added 'Engineer'" in result.output
    jobs = db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Engineer"
    assert jobs[0]["company"] == "Acme"


def test_add_skips_duplicate(runner: CliRunner) -> None:
    runner.invoke(cli_module.cli, ADD_ARGS)

    result = runner.invoke(cli_module.cli, ADD_ARGS)

    assert result.exit_code == 0
    assert "Skipped (already tracked)" in result.output
    assert len(db.get_jobs()) == 1


def test_add_reports_similar_position_json(runner: CliRunner) -> None:
    runner.invoke(cli_module.cli, ADD_ARGS)

    result = runner.invoke(
        cli_module.cli,
        [
            "add",
            "--url",
            "https://b.com/2",
            "--title",
            "Engineer",
            "--company",
            "Acme",
            "--description",
            "Body",
            "--json",
        ],
    )

    payload = json.loads(result.output)
    assert payload["status"] == "added"
    assert payload["similar"][0]["url"] == "https://a.com/1"
    assert len(db.get_jobs()) == 2


def test_add_prints_similar_note(runner: CliRunner) -> None:
    runner.invoke(cli_module.cli, ADD_ARGS)

    result = runner.invoke(
        cli_module.cli,
        [
            "add",
            "--url",
            "https://b.com/2",
            "--title",
            "Engineer",
            "--company",
            "Acme",
            "--description",
            "Body",
        ],
    )

    assert "Note: looks similar to" in result.output
    assert len(db.get_jobs()) == 2


def test_scan_json_includes_similar(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(cli_module.cli, ADD_ARGS)
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Body",
        },
    )

    result = runner.invoke(cli_module.cli, ["scan", "https://b.com/2", "--json"])

    payload = json.loads(result.output)
    assert payload["added"] == 1
    assert payload["similar"][0]["url"] == "https://a.com/1"


def test_add_rejects_invalid_url(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["add", "--url", "not-a-url", *ADD_ARGS[3:]])

    assert result.exit_code == 1
    assert "full URL" in result.stderr
    assert db.get_jobs() == []


def test_add_requires_title(runner: CliRunner) -> None:
    result = runner.invoke(
        cli_module.cli,
        ["add", "--url", "https://a.com/1", "--company", "Acme", "--description", "B"],
    )

    assert result.exit_code != 0
    db.init_db()
    assert db.get_jobs() == []


def test_add_sets_status(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, [*ADD_ARGS, "--status", "applied"])

    assert result.exit_code == 0
    assert len(db.get_jobs(status="applied")) == 1


def test_add_json_output(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, [*ADD_ARGS, "--json"])

    payload = json.loads(result.output)
    assert payload["status"] == "added"
    assert isinstance(payload["id"], int)


def test_scan_adds_positions(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scraper, "scrape_job", _varying_scrape)

    result = runner.invoke(cli_module.cli, ["scan", "https://a.com/1", "https://a.com/2"])

    assert result.exit_code == 0
    assert len(db.get_jobs()) == 2


def test_scan_reports_scrape_errors(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_scrape(url: str) -> scraper.ScrapedJob:
        raise ScrapeError("boom")

    monkeypatch.setattr(scraper, "scrape_job", raise_scrape)

    result = runner.invoke(cli_module.cli, ["scan", "https://a.com/1", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["errors"][0]["error"] == "boom"
    assert db.get_jobs() == []


def test_import_from_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scraper, "scrape_job", _varying_scrape)
    payload = [
        {
            "url": "https://a.com/1",
            "title": "Structured",
            "company": "Acme",
            "description": "Body",
        },
        {"url": "https://a.com/2"},
    ]
    source = tmp_path / "jobs.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(cli_module.cli, ["import", str(source), "--json"])

    assert result.exit_code == 0
    summary = json.loads(result.output)
    assert summary["added"] == 2
    assert {job["title"] for job in db.get_jobs()} == {"Structured", "Scraped 2"}


def test_import_from_stdin(runner: CliRunner) -> None:
    payload = [
        {"url": "https://a.com/1", "title": "T", "company": "C", "description": "D"},
    ]

    result = runner.invoke(cli_module.cli, ["import"], input=json.dumps(payload))

    assert result.exit_code == 0
    assert len(db.get_jobs()) == 1


def test_import_skips_duplicates(runner: CliRunner) -> None:
    runner.invoke(cli_module.cli, ADD_ARGS)
    payload = [{"url": "https://a.com/1", "title": "E", "company": "A", "description": "B"}]

    result = runner.invoke(cli_module.cli, ["import", "--json"], input=json.dumps(payload))

    summary = json.loads(result.output)
    assert summary == {"added": 0, "skipped": 1, "errors": []}


def test_import_reports_invalid_json(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["import"], input="{not json")

    assert result.exit_code == 1
    assert "Invalid JSON" in result.stderr


def test_list_json(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["list", "--json"])

    assert result.exit_code == 0
    jobs = json.loads(result.output)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Engineer"


def test_list_filters_by_status(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "Body")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    result = runner.invoke(cli_module.cli, ["list", "--status", "applied", "--json"])

    jobs = json.loads(result.output)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Two"


def test_list_search(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Adapty", "Remote", "Body")
    db.add_job("https://a.com/2", "QA", "Paysend", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["list", "--search", "adapt", "--json"])

    assert result.exit_code == 0
    jobs = json.loads(result.output)
    assert [job["company"] for job in jobs] == ["Adapty"]


def test_list_json_includes_comments(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "QA", "Adapty", "Remote", "Body")
    db.set_job_comment(db.get_jobs()[0]["id"], "applied", "note")

    result = runner.invoke(cli_module.cli, ["list", "--json"])

    jobs = json.loads(result.output)
    assert jobs[0]["applied_comment"] == "note"


def test_stats(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "desc")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "desc")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    json_result = runner.invoke(cli_module.cli, ["stats", "--json"])
    assert json.loads(json_result.output) == {
        "total": 2,
        "unapplied": 1,
        "applied": 1,
        "interview": 0,
        "rejected": 0,
    }

    human_result = runner.invoke(cli_module.cli, ["stats"])
    assert human_result.exit_code == 0
    assert "Total: 2" in human_result.output
    assert "Applied: 1" in human_result.output


def test_version_flags(runner: CliRunner) -> None:
    for flag in ("--version", "-V"):
        result = runner.invoke(cli_module.cli, [flag])

        assert result.exit_code == 0
        assert "cursustrace 0.1.0" in result.output


def test_run_command_passes_settings(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[config.Settings] = []
    monkeypatch.setattr("cursustrace.app.run", captured.append)

    result = runner.invoke(
        cli_module.cli,
        ["run", "--host", "127.0.0.1", "--port", "9001", "--reload", "--show"],
    )

    assert result.exit_code == 0
    settings = captured[0]
    assert settings.host == "127.0.0.1"
    assert settings.port == 9001
    assert settings.reload is True
    assert settings.show is True


def test_run_command_css_flag(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    css = tmp_path / "custom.css"
    css.write_text("body {}", encoding="utf-8")
    captured: list[config.Settings] = []
    monkeypatch.setattr("cursustrace.app.run", captured.append)

    result = runner.invoke(cli_module.cli, ["run", "--css", str(css)])

    assert result.exit_code == 0
    assert captured[0].cv_style_path == css


def test_run_command_invalid_port_type(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["run", "--port", "abc"])

    assert result.exit_code == 2


def test_run_command_out_of_range_port(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cursustrace.app.run", lambda settings: None)

    result = runner.invoke(cli_module.cli, ["run", "--port", "70000"])

    assert result.exit_code == 1
    assert "port" in result.stderr


def test_run_command_help(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--config" in result.output


def test_run_command_inherits_group_log_level(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[config.Settings] = []
    monkeypatch.setattr("cursustrace.app.run", captured.append)

    result = runner.invoke(cli_module.cli, ["--log-level", "debug", "run"])

    assert result.exit_code == 0
    assert captured[0].log_level == "debug"


def test_bare_invocation_uses_group_log_level(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[config.Settings] = []
    monkeypatch.setattr("cursustrace.app.run", captured.append)

    result = runner.invoke(cli_module.cli, ["--log-level", "info"])

    assert result.exit_code == 0
    assert captured[0].log_level == "info"


def test_cli_writes_daily_log_file(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli_module.cli, ["list"])

    assert result.exit_code == 0
    assert (tmp_path / "logs" / f"cursustrace-{logsetup._today().isoformat()}.log").exists()


def test_invalid_log_level_exits_2(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["--log-level", "verbose", "list"])

    assert result.exit_code == 2


def test_clear_requires_confirmation(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["clear"], input="n\n")

    assert result.exit_code == 1
    assert len(db.get_jobs()) == 1


def test_clear_removes_positions(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    db.save_profile(_profile())
    db.set_setting("dark_mode", "dark")

    result = runner.invoke(cli_module.cli, ["clear", "--yes"])

    assert result.exit_code == 0
    assert "Removed 1 position(s)." in result.output
    assert db.get_jobs() == []
    assert db.get_profile()["full_name"] == "Jane Doe"
    assert db.get_setting("dark_mode") == "dark"


def test_clear_all_removes_everything(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    db.save_profile(_profile())
    db.set_setting("dark_mode", "dark")

    result = runner.invoke(cli_module.cli, ["clear", "--all", "--yes", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"positions": 1, "profile": True, "settings": 1}
    assert db.get_jobs() == []
    assert db.get_profile()["full_name"] == ""
    assert db.get_setting("dark_mode") is None
