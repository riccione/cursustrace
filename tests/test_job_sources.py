"""Offline guardrails for the job-sources markdown tables (no network)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "job-sources"
REGION_FILES = ("usa.md", "eu.md", "serbia.md", "remote.md")
TYPES = frozenset(
    {
        "aggregator",
        "ats",
        "board",
        "community",
        "government",
        "remote",
        "search",
        "specialist",
    }
)
STATUSES = frozenset({"live", "blocked", "login", "dead"})
FRONTMATTER_KEYS = ("region", "title", "last_verified")
HEADER = ("Site", "URL", "Type", "Notes", "Status")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse(path: Path) -> tuple[dict[str, str], list[tuple[str, list[tuple[str, ...]]]]]:
    """Return (frontmatter, [(section, rows)]) for a region file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{path.name}: missing frontmatter start"
    end = lines.index("---", 1)
    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        frontmatter[key] = value

    sections: list[tuple[str, list[tuple[str, ...]]]] = []
    current: str | None = None
    rows: list[tuple[str, ...]] = []
    for line in lines[end + 1 :]:
        if line.startswith("## "):
            if current is not None:
                sections.append((current, rows))
            current = line[3:]
            rows = []
        elif line.startswith("| ") and not line.startswith("|--"):
            if tuple(c.strip() for c in line.strip("|").split("|")) != HEADER:
                rows.append(tuple(c.strip() for c in line.strip("|").split("|")))
    if current is not None:
        sections.append((current, rows))
    return frontmatter, sections


@pytest.mark.parametrize("name", REGION_FILES)
def test_region_file_schema(name: str) -> None:
    path = ROOT / name
    assert path.is_file(), f"missing {path}"
    frontmatter, sections = parse(path)

    assert set(frontmatter) == set(FRONTMATTER_KEYS), f"{name}: bad frontmatter keys"
    assert frontmatter["region"] == path.stem, f"{name}: region mismatch"
    assert frontmatter["title"], f"{name}: empty title"
    assert DATE_RE.fullmatch(frontmatter["last_verified"]), f"{name}: bad last_verified"

    total = 0
    for section, rows in sections:
        assert section, f"{name}: empty section heading"
        assert rows, f"{name}: section {section!r} has no rows"
        sites = [row[0] for row in rows]
        assert sites == sorted(sites), f"{name}: {section} rows not sorted by Site"
        for row in rows:
            assert len(row) == len(HEADER), f"{name}: bad row {row}"
            site, url, typ, notes, status = row
            assert site and notes, f"{name}: empty Site/Notes in {row}"
            assert url.startswith("https://"), f"{name}: non-https URL {url}"
            assert typ in TYPES, f"{name}: unknown type {typ!r}"
            assert status in STATUSES, f"{name}: unknown status {status!r}"
        total += len(rows)

    assert total >= 5, f"{name}: expected a curated list, got {total} rows"
    urls = [row[1] for _, rows in sections for row in rows]
    assert len(urls) == len(set(urls)), f"{name}: duplicate URL"


def test_readme_indexes_every_region_file() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in REGION_FILES:
        assert f"[{name}]({name})" in readme, f"README missing index row for {name}"
