"""Tests for the MCP server tools: in-memory client against a temp database."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from cursustrace import db, discovery, scraper
from cursustrace.errors import ScrapeError
from cursustrace.mcp_server import mcp


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "cursustrace.db")


def _message(result: Any) -> str:
    texts = [getattr(block, "text", None) for block in result.content]
    return " ".join(text for text in texts if isinstance(text, str))


async def ok(client: Client, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await client.call_tool(name, args or {})
    assert not result.is_error, f"{name} failed: {_message(result)}"
    assert result.structured_content is not None
    return dict(result.structured_content)


async def err(client: Client, name: str, args: dict[str, Any] | None = None) -> str:
    result = await client.call_tool(name, args or {})
    assert result.is_error, f"{name} unexpectedly succeeded"
    return _message(result)


def seed(
    url: str, title: str = "QA Engineer", company: str = "ACME", deadline: str | None = None
) -> int:
    db.init_db()
    job_id = db.add_job(url, title, company, "Berlin, DE", "Test everything.", deadline=deadline)
    assert job_id is not None
    return job_id


async def test_list_positions_filters_and_tags() -> None:
    first = seed("https://example.com/jobs/1", deadline="2026-12-31")
    second = seed("https://example.com/jobs/2", title="SDET")
    db.set_job_tags(first, ["qa"])

    async with Client(mcp) as client:
        payload = await ok(client, "list_positions")
        assert payload["count"] == 2
        by_id = {position["id"]: position for position in payload["positions"]}
        assert by_id[first]["tags"] == ["qa"]
        assert by_id[first]["status"] == "unapplied"
        assert by_id[first]["deadline"] == "2026-12-31"
        assert by_id[second]["tags"] == []
        assert by_id[second]["deadline"] is None

        tagged = await ok(client, "list_positions", {"tags": ["qa"]})
        assert [position["id"] for position in tagged["positions"]] == [first]

        none = await ok(client, "list_positions", {"status": "applied"})
        assert none["count"] == 0

        limited = await ok(client, "list_positions", {"limit": 1, "offset": 1})
        assert limited["count"] == 1

        message = await err(client, "list_positions", {"status": "maybe"})
        assert "status must be one of" in message
        message = await err(client, "list_positions", {"sort": "salary"})
        assert "sort must be one of" in message


async def test_get_position_and_events() -> None:
    job_id = seed("https://example.com/jobs/3", deadline="2026-11-30")
    db.set_job_status(job_id, "applied")
    db.set_job_comment(job_id, "applied", "referral sent")

    async with Client(mcp) as client:
        payload = await ok(client, "get_position", {"job_id": job_id})
        assert payload["position"]["status"] == "applied"
        assert payload["position"]["applied_comment"] == "referral sent"
        assert payload["position"]["tags"] == []
        assert payload["position"]["deadline"] == "2026-11-30"
        assert payload["events"][-1]["status"] == "applied"

        message = await err(client, "get_position", {"job_id": 999_999})
        assert "No position with id 999999" in message


async def test_get_stats_and_list_tags() -> None:
    job_id = seed("https://example.com/jobs/4")
    db.set_job_tags(job_id, ["remote", "qa"])

    async with Client(mcp) as client:
        stats = await ok(client, "get_stats")
        assert stats["counts"]["unapplied"] == 1
        tags = await ok(client, "list_tags")
        assert tags["tags"] == {"qa": 1, "remote": 1}


async def test_scrape_position_returns_fields_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "QA Engineer",
            "company": "ACME",
            "location": "Berlin, Germany",
            "description": "You must be authorized to work in Germany.",
            "deadline": "2026-12-31",
        },
    )
    async with Client(mcp) as client:
        flagged = await ok(client, "scrape_position", {"url": "https://example.com/jobs/9"})
        assert flagged["title"] == "QA Engineer"
        assert flagged["company"] == "ACME"
        assert flagged["location"] == "Berlin, Germany"
        assert flagged["deadline"] == "2026-12-31"
        flags = flagged["applicability"]["flags"]
        assert any("Germany" in flag for flag in flags)

        monkeypatch.setattr(
            scraper,
            "scrape_job",
            lambda url: {
                "title": "SDET",
                "company": "Globex",
                "location": "Remote (EU)",
                "description": "Remote-first team across Europe.",
                "deadline": None,
            },
        )
        clear = await ok(client, "scrape_position", {"url": "https://example.com/jobs/10"})
        assert clear["applicability"]["flags"] == []
        assert clear["deadline"] is None


async def test_scrape_position_surfaces_scrape_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str) -> scraper.ScrapedJob:
        raise ScrapeError("site unreachable")

    monkeypatch.setattr(scraper, "scrape_job", boom)
    async with Client(mcp) as client:
        message = await err(client, "scrape_position", {"url": "https://example.com/jobs/11"})
        assert "site unreachable" in message


async def test_add_position_scrapes_then_reports_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Scraped title",
            "company": "Scraped co",
            "location": "Remote (EU)",
            "description": "Scraped body",
            "deadline": "2026-11-30",
        },
    )

    async with Client(mcp) as client:
        added = await ok(client, "add_position", {"url": "https://example.com/jobs/5"})
        assert added["status"] == "added"
        assert added["id"] is not None
        stored = await ok(client, "get_position", {"job_id": added["id"]})
        assert stored["position"]["deadline"] == "2026-11-30"

        duplicate = await ok(client, "add_position", {"url": "https://example.com/jobs/5"})
        assert duplicate["status"] == "duplicate"


async def test_add_position_surfaces_scrape_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str) -> scraper.ScrapedJob:
        raise ScrapeError("site unreachable")

    monkeypatch.setattr(scraper, "scrape_job", boom)
    async with Client(mcp) as client:
        message = await err(client, "add_position", {"url": "https://example.com/jobs/6"})
        assert "site unreachable" in message


async def test_add_position_flags_country_restricted_then_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "QA Engineer",
            "company": "SAP",
            "location": "Walldorf, Germany",
            "description": "You must be authorized to work in Germany.",
            "deadline": "2026-10-15",
        },
    )
    url = "https://example.com/jobs/12"
    async with Client(mcp) as client:
        flagged = await ok(client, "add_position", {"url": url})
        assert flagged["status"] == "flagged"
        assert any("Germany" in flag for flag in flagged["applicability"]["flags"])
        assert flagged["position"]["company"] == "SAP"
        assert flagged["position"]["deadline"] == "2026-10-15"
        assert flagged["hint"]
        assert (await ok(client, "list_positions"))["count"] == 0

        forced = await ok(client, "add_position", {"url": url, "force": True})
        assert forced["status"] == "added"
        assert forced["applicability"]["flags"]

        duplicate = await ok(client, "add_position", {"url": url})
        assert duplicate["status"] == "duplicate"
        assert (await ok(client, "list_positions"))["count"] == 1


async def test_add_position_stores_explicit_deadline() -> None:
    async with Client(mcp) as client:
        added = await ok(
            client,
            "add_position",
            {
                "url": "https://example.com/jobs/14",
                "title": "Engineer",
                "company": "Acme",
                "description": "Body",
                "location": "Remote (EU)",
                "deadline": "2026-12-31T12:00:00Z",
            },
        )
        assert added["status"] == "added"
        stored = await ok(client, "get_position", {"job_id": added["id"]})
        assert stored["position"]["deadline"] == "2026-12-31"


async def test_add_position_rejects_invalid_deadline() -> None:
    async with Client(mcp) as client:
        message = await err(
            client,
            "add_position",
            {
                "url": "https://example.com/jobs/15",
                "title": "Engineer",
                "company": "Acme",
                "description": "Body",
                "location": "Remote (EU)",
                "deadline": "soon",
            },
        )
        assert "Invalid deadline" in message
        assert (await ok(client, "list_positions"))["count"] == 0


async def test_scan_collects_added_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def scrape(url: str) -> scraper.ScrapedJob:
        if "bad" in url:
            raise ScrapeError("boom")
        return {
            "title": "Test role",
            "company": "Co",
            "location": "Remote (EU)",
            "description": "Body",
            "deadline": "2026-08-15",
        }

    monkeypatch.setattr(scraper, "scrape_job", scrape)
    async with Client(mcp) as client:
        payload = await ok(
            client,
            "scan",
            {"urls": ["https://example.com/ok", "https://example.com/bad"], "tags": ["qa"]},
        )
        assert payload["added"] == 1
        assert payload["skipped"] == 0
        assert payload["errors"] == [{"url": "https://example.com/bad", "error": "boom"}]
        listed = await ok(client, "list_positions", {"tags": ["qa"]})
        assert listed["count"] == 1
        assert listed["positions"][0]["deadline"] == "2026-08-15"


async def test_scan_flags_restricted_listings_until_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "https://example.com/clear": {
            "title": "SDET",
            "company": "Globex",
            "location": "Remote (EU)",
            "description": "Remote-first.",
            "deadline": None,
        },
        "https://example.com/restricted": {
            "title": "QA Engineer",
            "company": "SAP",
            "location": "Hamburg, Germany",
            "description": "Must reside in Germany.",
            "deadline": "2026-09-01",
        },
    }
    monkeypatch.setattr(scraper, "scrape_job", lambda url: responses[url])

    async with Client(mcp) as client:
        result = await ok(client, "scan", {"urls": list(responses)})
        assert result["added"] == 1
        assert result["skipped"] == 0
        assert [entry["url"] for entry in result["flagged"]] == ["https://example.com/restricted"]
        assert result["flagged"][0]["position"]["company"] == "SAP"
        assert result["flagged"][0]["position"]["deadline"] == "2026-09-01"
        assert (await ok(client, "list_positions"))["count"] == 1

        forced = await ok(
            client, "scan", {"urls": ["https://example.com/restricted"], "force": True}
        )
        assert forced["added"] == 1
        assert "flagged" not in forced
        assert (await ok(client, "list_positions"))["count"] == 2

        again = await ok(client, "scan", {"urls": ["https://example.com/clear"]})
        assert again["skipped"] == 1


async def test_discover_add_ingests_new_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    source = discovery.SourceRef(
        input="https://example.com/jobs",
        url="https://example.com/jobs",
        site=None,
        site_status=None,
    )
    result = discovery.DiscoveryResult(
        source=source,
        candidates=({"url": "https://example.com/jobs/7"},),
        new_urls=("https://example.com/jobs/7",),
        status_counts={},
    )
    monkeypatch.setattr(discovery, "discover", lambda src, limit=100: result)
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Discovered",
            "company": "Co",
            "location": "Remote, EU",
            "description": "Body",
            "deadline": "2026-12-01",
        },
    )

    async with Client(mcp) as client:
        payload = await ok(client, "discover", {"source": source.url, "add": True})
        assert payload["extracted"] == 1
        assert payload["new_count"] == 1
        assert payload["added"] == 1
        assert payload["errors"] == []
        listed = await ok(client, "list_positions")
        assert listed["count"] == 1
        assert listed["positions"][0]["deadline"] == "2026-12-01"

        message = await err(client, "discover", {"source": source.url, "limit": 0})
        assert "limit must be between" in message


async def test_discover_add_flags_restricted_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = discovery.SourceRef(
        input="https://example.com/jobs",
        url="https://example.com/jobs",
        site=None,
        site_status=None,
    )
    result = discovery.DiscoveryResult(
        source=source,
        candidates=({"url": "https://example.com/jobs/13"},),
        new_urls=("https://example.com/jobs/13",),
        status_counts={},
    )
    monkeypatch.setattr(discovery, "discover", lambda src, limit=100: result)
    monkeypatch.setattr(
        scraper,
        "scrape_job",
        lambda url: {
            "title": "Onsite QA",
            "company": "Corp",
            "location": "Munich, Germany",
            "description": "Hybrid role in the office.",
            "deadline": None,
        },
    )

    async with Client(mcp) as client:
        payload = await ok(client, "discover", {"source": source.url, "add": True})
        assert payload["added"] == 0
        assert [entry["url"] for entry in payload["flagged"]] == ["https://example.com/jobs/13"]
        assert (await ok(client, "list_positions"))["count"] == 0

        forced = await ok(client, "discover", {"source": source.url, "add": True, "force": True})
        assert forced["added"] == 1
        assert "flagged" not in forced
        assert (await ok(client, "list_positions"))["count"] == 1


async def test_update_status_with_and_without_comment() -> None:
    job_id = seed("https://example.com/jobs/8")

    async with Client(mcp) as client:
        payload = await ok(
            client, "update_status", {"job_id": job_id, "status": "applied", "comment": "sent"}
        )
        assert payload == {"id": job_id, "status": "applied", "comment_set": True}

        message = await err(
            client,
            "update_status",
            {"job_id": job_id, "status": "unapplied", "comment": "reset"},
        )
        assert "comment is not allowed" in message
        message = await err(client, "update_status", {"job_id": 999_999, "status": "applied"})
        assert "No position with id" in message


async def test_update_status_outdated_and_list_filter() -> None:
    job_id = seed("https://example.com/jobs/outdated")

    async with Client(mcp) as client:
        payload = await ok(client, "update_status", {"job_id": job_id, "status": "outdated"})
        assert payload == {"id": job_id, "status": "outdated", "comment_set": False}

        listed = await ok(client, "list_positions", {"status": "outdated"})
        assert [position["id"] for position in listed["positions"]] == [job_id]
        assert listed["positions"][0]["status"] == "outdated"

        message = await err(
            client, "update_status", {"job_id": job_id, "status": "outdated", "comment": "stale"}
        )
        assert "comment is not allowed for outdated" in message

        message = await err(client, "list_positions", {"status": "maybe"})
        assert "outdated" in message


async def test_set_tags_adds_and_removes_case_insensitively() -> None:
    job_id = seed("https://example.com/jobs/9")
    db.set_job_tags(job_id, ["QA"])

    async with Client(mcp) as client:
        payload = await ok(
            client, "set_tags", {"job_id": job_id, "add": ["remote"], "remove": ["qa"]}
        )
        assert payload["tags"] == ["remote"]

        message = await err(client, "set_tags", {"job_id": 424_242, "add": ["x"]})
        assert "No position with id" in message


async def test_delete_positions_preview_then_confirm() -> None:
    first = seed("https://example.com/jobs/10")
    second = seed("https://example.com/jobs/11", title="SDET")

    async with Client(mcp) as client:
        message = await err(client, "delete_positions")
        assert "Provide job_ids" in message

        preview = await ok(client, "delete_positions", {"job_ids": [first, second]})
        assert preview["matched"] == 2
        assert "deleted" not in preview
        assert preview["sample"][0]["id"] in (first, second)
        assert db.get_job(first) is not None

        confirmed = await ok(
            client, "delete_positions", {"job_ids": [first, second], "confirm": True}
        )
        assert confirmed["deleted"] == 2
        assert db.get_job(first) is None
        assert db.get_job(second) is None

        missing = await ok(client, "delete_positions", {"job_ids": [999_999], "confirm": True})
        assert missing["matched"] == 0
        assert missing["not_found_ids"] == [999_999]


async def test_delete_positions_by_filter() -> None:
    job_id = seed("https://example.com/jobs/12")
    db.set_job_tags(job_id, ["purge"])
    other = seed("https://example.com/jobs/13")

    async with Client(mcp) as client:
        preview = await ok(client, "delete_positions", {"tags": ["purge"]})
        assert preview["ids"] == [job_id]
        deleted = await ok(client, "delete_positions", {"tags": ["purge"], "confirm": True})
        assert deleted["deleted"] == 1
    assert db.get_job(job_id) is None
    assert db.get_job(other) is not None


async def test_rename_and_delete_tag() -> None:
    job_id = seed("https://example.com/jobs/14")
    db.set_job_tags(job_id, ["qa"])

    async with Client(mcp) as client:
        renamed = await ok(client, "rename_tag", {"name": "qa", "new_name": "quality"})
        assert renamed == {"renamed": "qa", "name": "quality"}
        message = await err(client, "rename_tag", {"name": "missing", "new_name": "x"})
        assert "No tag named" in message

        deleted = await ok(client, "delete_tag", {"name": "quality"})
        assert deleted == {"deleted": "quality"}
    assert db.job_tags(job_id) == []
    async with Client(mcp) as client:
        message = await err(client, "delete_tag", {"name": "quality"})
        assert "No tag named" in message
