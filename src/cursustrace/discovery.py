"""Source resolution and link discovery for agent-driven position ingestion.

This module only does mechanics: fetch a source page, extract candidate
listing links, and split them against the database. Deciding which candidates
are relevant is left to the caller (an AI agent or a human).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from cursustrace import db, scraper
from cursustrace.utils import clean_url
from cursustrace.validation import validate_url

JOB_SOURCES_DIR = Path(__file__).resolve().parents[2] / "job-sources"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
_HEADER = ("Site", "URL", "Type", "Notes", "Status")
_NON_PAGE_SCHEMES = ("mailto:", "javascript:", "tel:", "data:")


@dataclass(frozen=True)
class SourceRef:
    """A resolved discovery source: the input as given and where it points."""

    input: str
    url: str
    site: str | None
    site_status: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    """Candidate links from one source, split against the database."""

    source: SourceRef
    candidates: tuple[dict[str, object], ...]
    new_urls: tuple[str, ...]
    status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def new_count(self) -> int:
        return len(self.new_urls)

    @property
    def known_count(self) -> int:
        return len(self.candidates) - self.new_count


def resolve_source(text: str) -> SourceRef:
    """Resolve a raw URL or a job-sources site name into a SourceRef.

    Raises ValueError when the input is neither a full URL nor a known site.
    """
    candidate = text.strip()
    if validate_url(candidate) is None:
        return SourceRef(input=candidate, url=candidate, site=None, site_status=None)
    match = _lookup_site(candidate)
    if match is None:
        raise ValueError(
            f"Unknown source '{text}': pass a full http(s) URL "
            "or a site name from job-sources/ (e.g. 'RemoteOK')."
        )
    site, url, status = match
    return SourceRef(input=text, url=url, site=site, site_status=status)


def extract_links(html: str, base_url: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Extract candidate listing links from a page in document order.

    Resolves relative hrefs, keeps query strings (boards often need them),
    drops fragments, non-http schemes, self links and homepage links, and
    deduplicates by canonical URL up to ``limit`` links.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_canonical = clean_url(base_url)
    seen: set[str] = set()
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(_NON_PAGE_SCHEMES):
            continue
        resolved = urljoin(base_url, href).split("#", 1)[0]
        if urlparse(resolved).scheme not in ("http", "https"):
            continue
        canonical = clean_url(resolved)
        if canonical in seen or canonical == base_canonical:
            continue
        if not urlparse(canonical).path:
            continue
        seen.add(canonical)
        links.append(resolved)
        if len(links) >= limit:
            break
    return links


def discover(source: SourceRef, *, limit: int = DEFAULT_LIMIT) -> DiscoveryResult:
    """Fetch the source page and classify its candidate links against the DB."""
    html = scraper.fetch_html(source.url)
    index = {clean_url(job["job_url"]): job for job in db.get_jobs()}
    candidates: list[dict[str, object]] = []
    new_urls: list[str] = []
    status_counts: dict[str, int] = {}
    for url in extract_links(html, source.url, limit):
        job = index.get(clean_url(url))
        if job is None:
            candidates.append({"url": url, "known": False})
            new_urls.append(url)
            continue
        status = db.job_status(job)
        status_counts[status] = status_counts.get(status, 0) + 1
        candidates.append(
            {
                "url": url,
                "known": True,
                "status": status,
                "id": job["id"],
                "title": job["title"],
            }
        )
    return DiscoveryResult(
        source=source,
        candidates=tuple(candidates),
        new_urls=tuple(new_urls),
        status_counts=status_counts,
    )


def _job_source_rows() -> list[tuple[str, str, str]]:
    """Return (site, url, status) rows from the job-sources region files."""
    if not JOB_SOURCES_DIR.is_dir():
        return []
    rows: list[tuple[str, str, str]] = []
    for path in sorted(JOB_SOURCES_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        rows.extend(_parse_region_file(path))
    return rows


def _parse_region_file(path: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    in_frontmatter = True
    for line in path.read_text(encoding="utf-8").splitlines():
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if not line.startswith("| ") or line.startswith("|--"):
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if cells != _HEADER and len(cells) == len(_HEADER):
            rows.append((cells[0], cells[1], cells[4]))
    return rows


def _lookup_site(name: str) -> tuple[str, str, str] | None:
    wanted = name.casefold()
    for site, url, status in _job_source_rows():
        if site.casefold() == wanted:
            return site, url, status
    return None
