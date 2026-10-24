"""Tests for the JSearch client, key storage, dedupe, and usage counter."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cursustrace import config, db, jsearch
from cursustrace.errors import JSearchError


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")
    monkeypatch.delenv(config.ENV_JSEARCH_API_KEY, raising=False)
    db.init_db()


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload


JOB: dict[str, object] = {
    "job_id": "abc",
    "job_title": "Senior Python Engineer",
    "employer_name": "Acme",
    "job_location": "Belgrade, Serbia",
    "job_description": "Build things.",
    "job_apply_link": "https://jobs.example.com/1?utm_source=google_jobs_apply&utm_medium=organic",
    "job_min_salary": 4000,
    "job_max_salary": 6000,
    "job_salary_currency": "EUR",
    "job_salary_period": "MONTH",
    "job_offer_expiration_datetime_utc": "2026-12-31T23:59:59.000Z",
    "job_posted_at_datetime_utc": "2026-10-20T10:00:00.000Z",
    "job_publisher": "LinkedIn",
    "job_is_remote": False,
}


def _payload(
    jobs: list[dict[str, object]] | None = None, cursor: str | None = None
) -> dict[str, object]:
    return {
        "status": "OK",
        "request_id": "req-1",
        "parameters": {},
        "data": {"jobs": jobs if jobs is not None else [JOB], "cursor": cursor},
    }


def _job(url: str = "https://jobs.example.com/1", **overrides: object) -> jsearch.JSearchJob:
    data: dict[str, object] = {
        "url": url,
        "title": "Senior Python Engineer",
        "company": "Acme",
        "location": "Belgrade, Serbia",
        "description": "Build things.",
    }
    data.update(overrides)
    return jsearch.JSearchJob(**data)  # type: ignore[arg-type]


def test_search_maps_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsearch.httpx, "get", lambda *a, **k: _FakeResponse(_payload()))

    page = jsearch.search("python", api_key="key")

    assert page.requests == 1
    job = page.jobs[0]
    assert job.url == JOB["job_apply_link"]
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme"
    assert job.location == "Belgrade, Serbia"
    assert (job.salary_min, job.salary_max) == (4000, 6000)
    assert (job.salary_currency, job.salary_period) == ("EUR", "month")
    assert job.deadline == "2026-12-31"
    assert job.publisher == "LinkedIn"
    assert job.remote is False
    assert job.flags == ()
    assert jsearch.requests_this_month() == 1


def test_search_uses_request_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse(_payload())

    monkeypatch.setattr(jsearch.httpx, "get", fake_get)

    jsearch.search("python", api_key="key", country="RS", date_posted="week", remote_only=True)

    params = captured["params"]
    assert isinstance(params, dict)
    assert params["query"] == "python"
    assert params["country"] == "rs"
    assert params["date_posted"] == "week"
    assert params["work_from_home"] == "true"
    assert captured["headers"] == {"x-api-key": "key"}


def test_search_falls_back_to_apply_options(monkeypatch: pytest.MonkeyPatch) -> None:
    job = {
        **JOB,
        "job_apply_link": "https://www.google.com/search?q=jobs",
        "apply_options": [
            {"publisher": "Google", "apply_link": "https://www.google.com/search?q=x"},
            {"publisher": "Board", "apply_link": "https://board.example/job/9"},
        ],
    }
    monkeypatch.setattr(jsearch.httpx, "get", lambda *a, **k: _FakeResponse(_payload([job])))

    page = jsearch.search("python", api_key="key")

    assert page.jobs[0].url == "https://board.example/job/9"


def test_search_skips_jobs_without_usable_link(monkeypatch: pytest.MonkeyPatch) -> None:
    job = {**JOB, "job_apply_link": "https://www.google.com/search?q=jobs"}
    monkeypatch.setattr(jsearch.httpx, "get", lambda *a, **k: _FakeResponse(_payload([job])))

    page = jsearch.search("python", api_key="key")

    assert page.jobs == ()


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_search_reports_http_errors(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    monkeypatch.setattr(
        jsearch.httpx, "get", lambda *a, **k: _FakeResponse({}, status_code=status_code)
    )

    with pytest.raises(JSearchError):
        jsearch.search("python", api_key="key")
    assert jsearch.requests_this_month() == 0


def test_search_reports_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"status": "ERROR", "error": {"code": 429, "message": "quota exhausted"}}
    monkeypatch.setattr(jsearch.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    with pytest.raises(JSearchError, match="quota exhausted"):
        jsearch.search("python", api_key="key")


def test_search_rejects_unknown_date_filter() -> None:
    with pytest.raises(JSearchError, match="Unknown date filter"):
        jsearch.search("python", api_key="key", date_posted="fortnight")


def test_search_many_follows_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        params = kwargs["params"]
        assert isinstance(params, dict)
        calls.append(params.get("cursor"))  # type: ignore[arg-type]
        cursor = "next" if params.get("cursor") is None else None
        return _FakeResponse(_payload([JOB] * 10, cursor=cursor))

    monkeypatch.setattr(jsearch.httpx, "get", fake_get)

    page = jsearch.search_many("python", api_key="key", total=15)

    assert calls == [None, "next"]
    assert len(page.jobs) == 15
    assert page.requests == 2
    assert jsearch.requests_this_month() == 2


def test_partition_marks_known_by_canonical_url() -> None:
    db.add_job("https://jobs.example.com/1", "Senior Python Engineer", "Acme", None, "Body")
    job = _job(url="https://jobs.example.com/1?utm_source=x")

    new, tracked = jsearch.partition([job])

    assert new == ()
    assert tracked[0].status == "unapplied"
    assert tracked[0].title == "Senior Python Engineer"


def test_partition_keeps_other_urls_new() -> None:
    db.add_job("https://other.example/job/1", "Senior Python Engineer", "Acme", None, "Body")
    job = _job(url="https://jobs.example.com/1")

    new, tracked = jsearch.partition([job])

    assert new == (job,)
    assert tracked == ()


def test_add_jobs_reports_similar_roles() -> None:
    db.add_job("https://other.example/job/1", "Senior Python Engineer", "Acme", None, "Body")

    saved, duplicates, similar = jsearch.add_jobs([_job()])

    assert (saved, duplicates) == (1, 0)
    assert len(similar) == 1
    assert similar[0][0]["job_url"] == "https://other.example/job/1"


def test_partition_dedupes_batch() -> None:
    jobs = [
        _job(url="https://jobs.example.com/1"),
        _job(url="https://jobs.example.com/1?utm_source=x"),
        _job(url="https://jobs.example.com/2", title="Other Role"),
    ]

    new, tracked = jsearch.partition(jobs)

    assert len(new) == 2
    assert tracked == ()


def test_partition_respects_user_scope() -> None:
    db.add_job("https://jobs.example.com/1", "Senior Python Engineer", "Acme", None, "Body")
    job = _job()

    new, tracked = jsearch.partition([job], user_id=1)

    assert len(new) == 1
    assert tracked == ()


def test_add_jobs_inserts_and_skips_duplicates() -> None:
    job = _job(deadline="2026-12-31", salary_min=4000, salary_max=6000)

    saved, duplicates, similar = jsearch.add_jobs([job])
    saved_again, duplicates_again, _ = jsearch.add_jobs([job])

    assert (saved, duplicates) == (1, 0)
    assert (saved_again, duplicates_again) == (0, 1)
    assert similar == []
    stored = db.get_jobs()
    assert len(stored) == 1
    assert stored[0]["deadline"] == "2026-12-31"
    assert stored[0]["salary_period"] is None
    assert db.get_events(stored[0]["id"])[0]["status"] == "unapplied"


def test_api_key_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ENV_JSEARCH_API_KEY, "env-key")

    assert jsearch.api_key(config.Settings(jsearch_api_key="toml-key")) == (
        "env-key",
        "environment",
    )


def test_api_key_reads_config_file() -> None:
    assert jsearch.api_key(config.Settings(jsearch_api_key="toml-key")) == (
        "toml-key",
        "config file",
    )


def test_api_key_falls_back_to_saved_file() -> None:
    jsearch.save_api_key("saved-key")

    assert jsearch.api_key(config.Settings()) == ("saved-key", "saved key")


def test_api_key_returns_none_without_sources() -> None:
    assert jsearch.api_key(config.Settings()) is None


def test_save_api_key_is_owner_only() -> None:
    jsearch.save_api_key("  secret-value  ")

    path = jsearch.key_path()
    assert path.read_text(encoding="utf-8") == "secret-value"
    assert path.stat().st_mode & 0o777 == 0o600


def test_save_api_key_rejects_blank() -> None:
    with pytest.raises(JSearchError):
        jsearch.save_api_key("   ")


def test_remove_api_key() -> None:
    jsearch.save_api_key("secret")
    jsearch.remove_api_key()

    assert not jsearch.key_path().exists()
    assert jsearch.api_key(config.Settings()) is None


def test_mask_key() -> None:
    assert jsearch.mask_key("abcdefgh") == "••••efgh"
    assert jsearch.mask_key("abc") == "••••"


def test_month_key() -> None:
    assert jsearch.month_key(date(2026, 10, 5)) == "jsearch_requests:2026-10"


def test_record_request_increments_monthly_counter() -> None:
    db.set_setting(jsearch.month_key(), "199")

    assert jsearch.record_request() == 200
    assert jsearch.requests_this_month() == 200


def test_country_options_cover_common_markets() -> None:
    options = jsearch.country_options()

    assert jsearch.DEFAULT_COUNTRY in options
    assert next(iter(options)) == "rs"
    for code in ("rs", "us", "de"):
        assert code in options
        assert code.upper() in options[code]
    assert all(len(code) == 2 and code.islower() for code in options)
    assert not {"eu", "ez", "un"} & set(options)
