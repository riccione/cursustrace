"""Tests for the cursustrace command-line interface."""

from __future__ import annotations

import csv
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
    "CURSUS_BACKUP_DIR",
    "CURSUS_BACKUP_KEEP",
    "CURSUS_BACKUP_ON_START",
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
        "id": 1,
        "name": "Default",
        "full_name": "Jane Doe",
        "location": "Remote",
        "phone": "555-0100",
        "email": "jane@example.com",
        "linkedin_url": "",
        "github_url": "",
        "summary": "# Jane",
        "work_history": "",
        "education": "",
        "skills": "",
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


_DISCOVER_HTML = (
    '<a href="/jobs/111">one</a>'
    '<a href="https://board.example/jobs/222?ref=x">two</a>'
    '<a href="https://board.example/">home</a>'
)


def test_discover_json_lists_new_and_known(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    job_id = db.add_job("https://board.example/jobs/111", "Engineer", "Acme", None, "Body")
    assert job_id is not None
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)

    result = runner.invoke(cli_module.cli, ["discover", "https://board.example", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["source"] == {
        "input": "https://board.example",
        "url": "https://board.example",
        "site": None,
        "site_status": None,
    }
    assert payload["extracted"] == 2
    assert payload["new_count"] == 1
    assert payload["known_count"] == 1
    assert payload["known_by_status"] == {"unapplied": 1}
    assert payload["candidates"] == [
        {
            "url": "https://board.example/jobs/111",
            "known": True,
            "status": "unapplied",
            "id": job_id,
            "title": "Engineer",
        },
        {"url": "https://board.example/jobs/222?ref=x", "known": False},
    ]
    assert "added" not in payload


def test_discover_human_output(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)

    result = runner.invoke(cli_module.cli, ["discover", "https://board.example"])

    assert result.exit_code == 0
    assert "Extracted 2 links · 2 new · 0 already in database" in result.output
    assert "https://board.example/jobs/111" in result.output
    assert "Tip: add --add" in result.output


def test_discover_resolves_job_source_name(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)

    result = runner.invoke(cli_module.cli, ["discover", "RemoteOK", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["source"]["site"] == "RemoteOK"
    assert payload["source"]["site_status"] == "live"
    assert payload["source"]["url"] == "https://remoteok.com"


def test_discover_add_ingests_new_positions(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    db.add_job("https://board.example/jobs/111", "Engineer", "Acme", None, "Body")
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)
    monkeypatch.setattr(scraper, "scrape_job", _varying_scrape)

    result = runner.invoke(cli_module.cli, ["discover", "https://board.example", "--add", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["added"] == 1
    assert payload["skipped"] == 0
    assert payload["errors"] == []
    urls = [job["job_url"] for job in db.get_jobs()]
    assert "https://board.example/jobs/222?ref=x" in urls


def test_discover_add_assigns_tags(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)
    monkeypatch.setattr(scraper, "scrape_job", _varying_scrape)

    result = runner.invoke(
        cli_module.cli,
        ["discover", "https://board.example", "--add", "--tag", "qa", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["added"] == 2
    assert db.tag_counts() == {"qa": 2}


def test_discover_add_reports_errors_and_exits_nonzero(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scraper, "fetch_html", lambda url: _DISCOVER_HTML)

    def boom(url: str) -> scraper.ScrapedJob:
        raise ScrapeError(f"Blocked: {url}")

    monkeypatch.setattr(scraper, "scrape_job", boom)

    result = runner.invoke(cli_module.cli, ["discover", "https://board.example", "--add", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["added"] == 0
    assert len(payload["errors"]) == 2
    assert "Blocked" in payload["errors"][0]["error"]


def test_discover_unknown_source_fails(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["discover", "NotASite"])

    assert result.exit_code == 1
    assert "Unknown source" in result.stderr


def test_discover_fetch_failure_json(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str) -> str:
        raise ScrapeError(f"Unable to fetch page {url}: 403 Forbidden")

    monkeypatch.setattr(scraper, "fetch_html", boom)

    result = runner.invoke(cli_module.cli, ["discover", "https://x.example/jobs", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "403 Forbidden" in payload["error"]


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


def test_add_with_salary(runner: CliRunner) -> None:
    result = runner.invoke(
        cli_module.cli,
        [
            *ADD_ARGS,
            "--salary-min",
            "60000",
            "--salary-max",
            "80000",
            "--salary-currency",
            "EUR",
            "--salary-period",
            "year",
            "--salary-note",
            "plus bonus",
        ],
    )

    assert result.exit_code == 0
    job = db.get_jobs()[0]
    assert job["salary_min"] == 60000
    assert job["salary_max"] == 80000
    assert job["salary_currency"] == "EUR"
    assert job["salary_period"] == "year"
    assert job["salary_note"] == "plus bonus"


def test_add_salary_requires_currency_and_period(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, [*ADD_ARGS, "--salary-min", "60000"])

    assert result.exit_code == 1
    assert "salary-currency" in result.stderr
    db.init_db()
    assert db.get_jobs() == []


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


def test_scan_assigns_tags(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scraper, "scrape_job", _varying_scrape)

    result = runner.invoke(
        cli_module.cli, ["scan", "https://a.com/1", "--tag", "qa", "--tag", "remote"]
    )

    assert result.exit_code == 0
    assert db.tag_counts() == {"qa": 1, "remote": 1}


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


def test_import_reads_salary(runner: CliRunner) -> None:
    payload = [
        {
            "url": "https://a.com/1",
            "title": "Paid",
            "company": "Acme",
            "description": "Body",
            "salary_min": 3000,
            "salary_currency": "USD",
            "salary_period": "month",
        },
    ]

    result = runner.invoke(cli_module.cli, ["import"], input=json.dumps(payload))

    assert result.exit_code == 0
    job = db.get_jobs()[0]
    assert job["salary_min"] == 3000
    assert job["salary_currency"] == "USD"
    assert job["salary_period"] == "month"


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


def test_list_filters_by_salary(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Unknown", "Acme", "Remote", "Body")
    db.add_job(
        "https://a.com/2",
        "Paid",
        "Acme",
        "Remote",
        "Body",
        salary_min=100000,
        salary_currency="EUR",
        salary_period="year",
    )

    result = runner.invoke(
        cli_module.cli, ["list", "--min-salary", "50000", "--salary-currency", "EUR", "--json"]
    )

    jobs = json.loads(result.output)
    assert [job["title"] for job in jobs] == ["Paid"]
    assert jobs[0]["salary_min"] == 100000


def test_list_default_is_unsorted_and_unpaginated(runner: CliRunner) -> None:
    db.init_db()
    for i in range(3):
        db.add_job(f"https://a.com/{i}", f"Job {i}", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["list", "--json"])

    jobs = json.loads(result.output)
    assert [job["title"] for job in jobs] == ["Job 2", "Job 1", "Job 0"]


def test_list_sort_company(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Zeta", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Alpha", "Remote", "Body")
    db.add_job("https://a.com/3", "Three", "Mid", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["list", "--sort", "company", "--json"])

    jobs = json.loads(result.output)
    assert [job["company"] for job in jobs] == ["Alpha", "Mid", "Zeta"]


def test_list_sort_status_groups_pipeline(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "Body")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "Body")
    jobs = db.get_jobs()
    db.set_job_status(jobs[0]["id"], "rejected")
    db.set_job_status(jobs[1]["id"], "applied")

    result = runner.invoke(cli_module.cli, ["list", "--sort", "status", "--json"])

    parsed = json.loads(result.output)
    assert [job["title"] for job in parsed] == ["One", "Two", "Three"]


def test_list_limit_and_offset(runner: CliRunner) -> None:
    db.init_db()
    for i in range(3):
        db.add_job(f"https://a.com/{i}", f"Job {i}", "Acme", "Remote", "Body")

    page_one = runner.invoke(cli_module.cli, ["list", "--limit", "2", "--json"])
    page_two = runner.invoke(cli_module.cli, ["list", "--limit", "2", "--offset", "2", "--json"])
    beyond = runner.invoke(cli_module.cli, ["list", "--offset", "99", "--json"])

    assert [job["title"] for job in json.loads(page_one.output)] == ["Job 2", "Job 1"]
    assert [job["title"] for job in json.loads(page_two.output)] == ["Job 0"]
    assert json.loads(beyond.output) == []


def test_list_sort_applies_before_limit(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Zeta", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Alpha", "Remote", "Body")
    db.add_job("https://a.com/3", "Three", "Mid", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["list", "--sort", "company", "--limit", "2", "--json"])

    jobs = json.loads(result.output)
    assert [job["company"] for job in jobs] == ["Alpha", "Mid"]


def test_export_is_never_paginated(runner: CliRunner) -> None:
    db.init_db()
    for i in range(3):
        db.add_job(f"https://a.com/{i}", f"Job {i}", "Acme", "Remote", "Body")

    listed = runner.invoke(cli_module.cli, ["list", "--limit", "1", "--json"])
    exported = runner.invoke(cli_module.cli, ["export"])

    assert len(json.loads(listed.output)) == 1
    assert len(json.loads(exported.output)) == 3


def test_stats_includes_salary(runner: CliRunner) -> None:
    db.init_db()
    db.add_job(
        "https://a.com/1",
        "Paid",
        "Acme",
        "Remote",
        "Body",
        salary_min=60000,
        salary_max=80000,
        salary_currency="EUR",
        salary_period="year",
    )

    result = runner.invoke(cli_module.cli, ["stats", "--json"])

    payload = json.loads(result.output)
    assert payload["salary"]["EUR"]["median"] == 70000


def test_export_json_stdout(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["export"])

    assert result.exit_code == 0
    records = json.loads(result.output)
    assert len(records) == 1
    record = records[0]
    assert record["url"] == "https://a.com/1"
    assert record["title"] == "Engineer"
    assert record["status"] == "unapplied"
    assert record["salary_min"] is None
    assert "fingerprint" not in record


def test_export_json_round_trips_through_import(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    job_id = db.add_job(
        "https://a.com/1",
        "Engineer",
        "Acme",
        "Remote",
        "Body",
        salary_min=60000,
        salary_max=80000,
        salary_currency="EUR",
        salary_period="year",
        salary_note="plus bonus",
    )
    assert job_id is not None
    db.set_job_status(job_id, "interview")
    db.set_job_comment(job_id, "interview", "tech round")
    db.set_job_tags(job_id, ["remote", "startup"])
    dump = tmp_path / "jobs.json"

    assert runner.invoke(cli_module.cli, ["export", str(dump)]).exit_code == 0
    exported = json.loads(dump.read_text(encoding="utf-8"))
    assert exported[0]["tags"] == ["remote", "startup"]
    assert runner.invoke(cli_module.cli, ["clear", "--yes"]).exit_code == 0

    import_result = runner.invoke(cli_module.cli, ["import", str(dump), "--json"])
    assert json.loads(import_result.output)["added"] == 1

    job = db.get_jobs()[0]
    assert job["job_url"] == "https://a.com/1"
    assert job["title"] == "Engineer"
    assert job["location"] == "Remote"
    assert job["description"] == "Body"
    assert job["salary_min"] == 60000
    assert job["salary_max"] == 80000
    assert job["salary_currency"] == "EUR"
    assert job["salary_note"] == "plus bonus"
    assert db.job_status(job) == "interview"
    assert job["interview_comment"] == "tech round"
    assert db.job_tags(job["id"]) == ["remote", "startup"]  # tags survive too


def test_export_csv_uses_extension_and_quotes_fields(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer, Lead", "Acme", "Remote", "Line one\nLine two")
    dump = tmp_path / "jobs.csv"

    result = runner.invoke(cli_module.cli, ["export", str(dump)])

    assert result.exit_code == 0
    assert f"Exported 1 position(s) to {dump}." in result.output
    with dump.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["title"] == "Engineer, Lead"
    assert rows[0]["description"] == "Line one\nLine two"
    assert rows[0]["status"] == "unapplied"


def test_export_format_flag_overrides_extension(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["export", "--format", "csv"])

    assert result.exit_code == 0
    assert result.output.startswith("url,title,company,")
    assert "https://a.com/1" in result.output


def test_export_status_filter(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "Body")
    db.set_job_status(db.get_jobs()[0]["id"], "applied")

    result = runner.invoke(cli_module.cli, ["export", "--status", "applied"])

    records = json.loads(result.output)
    assert [record["title"] for record in records] == ["Two"]
    assert records[0]["status"] == "applied"


def test_export_empty_database(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    json_result = runner.invoke(cli_module.cli, ["export"])
    assert json.loads(json_result.output) == []

    dump = tmp_path / "empty.csv"
    assert runner.invoke(cli_module.cli, ["export", str(dump)]).exit_code == 0
    lines = dump.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("url,title,company,")


def test_import_rejects_invalid_status(runner: CliRunner) -> None:
    payload = [
        {
            "url": "https://a.com/1",
            "title": "T",
            "company": "C",
            "description": "D",
            "status": "archived",
        },
    ]

    result = runner.invoke(cli_module.cli, ["import", "--json"], input=json.dumps(payload))

    assert result.exit_code == 1
    assert json.loads(result.output)["errors"][0]["error"] == "invalid status: archived"
    assert db.get_jobs() == []


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


def test_backup_command_json(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    dest = tmp_path / "backups"

    result = runner.invoke(cli_module.cli, ["backup", "--dir", str(dest), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["positions"] == 1
    assert payload["pruned"] == []
    assert Path(payload["path"]).parent == dest
    assert Path(payload["path"]).is_file()


def test_backup_command_prunes_older_copies(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    dest = tmp_path / "backups"
    dest.mkdir()
    for stamp in ("20200101-000000", "20200102-000000"):
        (dest / f"cursustrace-{stamp}.db").write_text("old", encoding="utf-8")

    result = runner.invoke(cli_module.cli, ["backup", "--dir", str(dest), "--keep", "1"])

    assert result.exit_code == 0
    assert "Backed up 0 position(s)" in result.output
    assert "Pruned 2 older backup(s)." in result.output
    remaining = [path.name for path in dest.glob("cursustrace-*.db")]
    assert len(remaining) == 1
    assert remaining[0] not in {
        "cursustrace-20200101-000000.db",
        "cursustrace-20200102-000000.db",
    }


def test_backup_rejects_negative_keep(runner: CliRunner) -> None:
    result = runner.invoke(cli_module.cli, ["backup", "--keep", "-1"])

    assert result.exit_code == 1
    assert "backup_keep" in result.stderr


def test_backup_dir_flag_beats_env_and_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.init_db()
    from_file = tmp_path / "from-file"
    from_env = tmp_path / "from-env"
    from_flag = tmp_path / "from-flag"
    Path(config.CONFIG_FILE).write_text(
        f'[cursustrace]\nbackup_dir = "{from_file}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CURSUS_BACKUP_DIR", str(from_env))

    env_result = runner.invoke(cli_module.cli, ["backup", "--json"])
    flag_result = runner.invoke(cli_module.cli, ["backup", "--dir", str(from_flag), "--json"])

    assert Path(json.loads(env_result.output)["path"]).parent == from_env
    assert from_file.is_dir() is False
    assert Path(json.loads(flag_result.output)["path"]).parent == from_flag


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
    db.create_tag("remote")

    result = runner.invoke(cli_module.cli, ["clear", "--yes"])

    assert result.exit_code == 0
    assert "Removed 1 position(s)." in result.output
    assert db.get_jobs() == []
    assert db.get_profile()["full_name"] == "Jane Doe"
    assert db.get_setting("dark_mode") == "dark"
    assert db.list_tags() == ["remote"]  # plain clear keeps the catalog


def test_clear_all_removes_everything(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    db.save_profile(_profile())
    db.set_setting("dark_mode", "dark")
    db.create_tag("remote")

    result = runner.invoke(cli_module.cli, ["clear", "--all", "--yes", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"positions": 1, "profile": True, "settings": 1}
    assert db.get_jobs() == []
    assert db.get_profile()["full_name"] == ""
    assert db.get_setting("dark_mode") is None
    assert db.list_tags() == []


# --- Delete -----------------------------------------------------------------


def test_delete_requires_selector(runner: CliRunner) -> None:
    db.init_db()

    result = runner.invoke(cli_module.cli, ["delete", "--yes"])

    assert result.exit_code == 1
    assert "Provide position IDs" in result.output


def test_delete_by_id_with_preview(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "Other", "Beta", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["delete", "1", "--yes"])

    assert result.exit_code == 0
    assert "1. Engineer — Acme" in result.output
    assert "Removed 1 position(s)." in result.output
    assert [job["job_url"] for job in db.get_jobs()] == ["https://a.com/2"]


def test_delete_confirmation_aborts(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["delete", "1"], input="n\n")

    assert result.exit_code == 1
    assert len(db.get_jobs()) == 1


def test_delete_by_url_and_url_file(runner: CliRunner, tmp_path: Path) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "Body")
    db.add_job("https://a.com/2", "Two", "Acme", "Remote", "Body")
    db.add_job("https://a.com/3", "Three", "Acme", "Remote", "Body")
    url_file = tmp_path / "urls.txt"
    url_file.write_text("# comment\nhttps://a.com/2\n\nhttps://a.com/missing\n", encoding="utf-8")

    result = runner.invoke(
        cli_module.cli,
        ["delete", "--url", "https://a.com/1", "--url-file", str(url_file), "--yes", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "deleted": 2,
        "ids": [1, 2],
        "not_found_ids": [],
        "not_found_urls": ["https://a.com/missing"],
    }
    assert [job["job_url"] for job in db.get_jobs()] == ["https://a.com/3"]


def test_delete_by_filters(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "One", "Acme", "Remote", "Body")
    job_id = db.add_job("https://a.com/2", "Two", "Beta", "Remote", "Body")
    assert job_id is not None
    db.set_job_tags(job_id, ["spam"])

    result = runner.invoke(cli_module.cli, ["delete", "--tag", "spam", "--yes", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["ids"] == [2]
    assert [job["job_url"] for job in db.get_jobs()] == ["https://a.com/1"]


def test_delete_json_requires_yes(runner: CliRunner) -> None:
    db.init_db()

    result = runner.invoke(cli_module.cli, ["delete", "1", "--json"])

    assert result.exit_code == 1
    assert "--json requires --yes" in result.output


def test_delete_reports_unknown_ids(runner: CliRunner) -> None:
    db.init_db()
    db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")

    result = runner.invoke(cli_module.cli, ["delete", "99", "--yes", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "deleted": 0,
        "ids": [],
        "not_found_ids": [99],
        "not_found_urls": [],
    }
    assert len(db.get_jobs()) == 1


def test_delete_cascades_events_and_tags(runner: CliRunner) -> None:
    db.init_db()
    job_id = db.add_job("https://a.com/1", "Engineer", "Acme", "Remote", "Body")
    assert job_id is not None
    db.set_job_tags(job_id, ["remote"])
    db.set_job_status(job_id, "applied")
    assert db.get_events(job_id)

    result = runner.invoke(cli_module.cli, ["delete", str(job_id), "--yes"])

    assert result.exit_code == 0
    assert db.get_jobs() == []
    assert db.get_events(job_id) == []
    assert db.filter_by_tags(db.get_jobs(), ["remote"]) == []


# --- Tags -------------------------------------------------------------------


def _tagged_job(
    url: str,
    company: str,
    tags: tuple[str, ...] = (),
    *,
    title: str = "Engineer",
) -> int:
    job_id = db.add_job(url, title, company, "Remote", "Body")
    assert job_id is not None
    if tags:
        db.set_job_tags(job_id, tags)
    return job_id


def test_list_filters_by_tag(runner: CliRunner) -> None:
    db.init_db()
    _tagged_job("https://a.com/1", "Zeta", ("remote",))
    _tagged_job("https://a.com/2", "Alpha", ("startup", "remote"))
    _tagged_job("https://a.com/3", "Mid")

    def companies(*args: str) -> list[str]:
        result = runner.invoke(cli_module.cli, ["list", "--json", *args])
        assert result.exit_code == 0
        return [record["company"] for record in json.loads(result.output)]

    assert len(companies()) == 3  # no filter
    assert companies("--tag", "remote") == ["Alpha", "Zeta"]  # any-match, id-DESC
    assert companies("--tag", "REMOTE") == ["Alpha", "Zeta"]  # case-insensitive
    assert companies("--tag", "remote", "--tag", "startup") == ["Alpha", "Zeta"]
    assert companies("--tag", "referral") == []  # unknown tag matches nothing
    assert "No positions." in runner.invoke(cli_module.cli, ["list", "--tag", "x"]).output


def test_export_filters_by_tag(runner: CliRunner) -> None:
    db.init_db()
    _tagged_job("https://a.com/1", "Zeta", ("remote",))
    _tagged_job("https://a.com/2", "Alpha", ("startup",))

    result = runner.invoke(cli_module.cli, ["export", "--tag", "startup"])

    assert result.exit_code == 0
    records = json.loads(result.output)
    assert [record["company"] for record in records] == ["Alpha"]


def test_add_assigns_tags(runner: CliRunner) -> None:
    db.init_db()

    result = runner.invoke(
        cli_module.cli,
        [*ADD_ARGS, "--tag", "remote", "--tag", "Startup"],
    )

    assert result.exit_code == 0
    assert db.list_tags() == ["remote", "Startup"]  # alphabetical, case-insensitive
    assert db.job_tags(db.get_jobs()[0]["id"]) == ["remote", "Startup"]


def test_add_duplicate_does_not_touch_tags(runner: CliRunner) -> None:
    db.init_db()
    job_id = _tagged_job("https://a.com/1", "Acme", ("remote",))

    result = runner.invoke(cli_module.cli, [*ADD_ARGS, "--tag", "startup"])

    assert result.exit_code == 0
    assert "Skipped (already tracked)" in result.output
    assert db.job_tags(job_id) == ["remote"]


def test_tags_lifecycle(runner: CliRunner) -> None:
    db.init_db()

    created = runner.invoke(cli_module.cli, ["tags", "add", "remote", "startup", "--json"])
    assert created.exit_code == 0
    assert json.loads(created.output) == {"created": ["remote", "startup"], "existing": []}

    again = runner.invoke(cli_module.cli, ["tags", "add", "remote", "--json"])
    assert json.loads(again.output) == {"created": [], "existing": ["remote"]}

    job_id = _tagged_job("https://a.com/1", "Acme", ("remote",))
    counts = runner.invoke(cli_module.cli, ["tags", "list", "--json"])
    assert json.loads(counts.output) == {"remote": 1, "startup": 0}
    listed = runner.invoke(cli_module.cli, ["tags", "list"])
    assert "remote (1)" in listed.output
    assert "startup (0)" in listed.output

    renamed = runner.invoke(cli_module.cli, ["tags", "rename", "remote", "wfh", "--json"])
    assert renamed.exit_code == 0
    assert json.loads(renamed.output) == {"renamed": "remote", "to": "wfh"}
    assert db.job_tags(job_id) == ["wfh"]

    collision = runner.invoke(cli_module.cli, ["tags", "rename", "startup", "WFH"])
    assert collision.exit_code == 1
    assert "empty or taken" in collision.output

    unknown_rename = runner.invoke(cli_module.cli, ["tags", "rename", "nope", "x"])
    assert unknown_rename.exit_code == 1
    assert "Unknown tag: nope" in unknown_rename.output

    removed = runner.invoke(cli_module.cli, ["tags", "rm", "wfh", "--json"])
    assert removed.exit_code == 0
    assert json.loads(removed.output) == {"removed": ["wfh"]}
    assert db.list_tags() == ["startup"]
    assert db.job_tags(job_id) == []

    unknown_rm = runner.invoke(cli_module.cli, ["tags", "rm", "wfh", "other"])
    assert unknown_rm.exit_code == 1
    assert "Unknown tag(s): wfh, other" in unknown_rm.output
    assert db.list_tags() == ["startup"]  # aborted before deleting


def test_tags_add_rejects_blank_name(runner: CliRunner) -> None:
    db.init_db()
    result = runner.invoke(cli_module.cli, ["tags", "add", "  "])
    assert result.exit_code == 1
    assert "must not be empty" in result.output
    assert db.list_tags() == []


def test_import_accepts_list_and_string_tags(runner: CliRunner) -> None:
    db.init_db()
    payload = json.dumps(
        [
            {
                "url": "https://a.com/1",
                "title": "Engineer",
                "company": "Acme",
                "description": "Body",
                "tags": ["remote", "startup"],
            },
            {
                "url": "https://a.com/2",
                "title": "Dev",
                "company": "Two",
                "description": "Body",
                "tags": "remote; referral",
            },
        ]
    )

    result = runner.invoke(cli_module.cli, ["import", "--json"], input=payload)

    assert result.exit_code == 0
    assert json.loads(result.output)["added"] == 2
    jobs = {job["job_url"]: job for job in db.get_jobs()}
    assert db.job_tags(jobs["https://a.com/1"]["id"]) == ["remote", "startup"]
    assert db.job_tags(jobs["https://a.com/2"]["id"]) == ["referral", "remote"]


def test_export_csv_joins_tags_with_semicolons(runner: CliRunner) -> None:
    db.init_db()
    _tagged_job("https://a.com/1", "Acme", ("remote", "startup"))
    _tagged_job("https://a.com/2", "Other")

    result = runner.invoke(cli_module.cli, ["export", "--format", "csv", "--tag", "remote"])

    assert result.exit_code == 0
    rows = list(csv.DictReader(result.output.splitlines()))
    assert [row["company"] for row in rows] == ["Acme"]
    assert rows[0]["tags"] == "remote;startup"
