"""Source resolution and link discovery for agent-driven position ingestion.

This module only does mechanics: fetch a source page, extract candidate
listing links, and split them against the database. Deciding which candidates
are relevant is left to the caller (an AI agent or a human).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from cursustrace import db, scraper
from cursustrace.errors import ScrapeError
from cursustrace.utils import clean_url
from cursustrace.validation import validate_url

JOB_SOURCES_DIR = Path(__file__).resolve().parents[2] / "job-sources"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
NON_LIVE_STATUSES = frozenset({"blocked", "login", "dead"})
_HEADER = ("Site", "URL", "Type", "Notes", "Status")
_HEADER_SEARCH = _HEADER + ("Search",)
_SEARCH_PLACEHOLDER = "{query}"
_NON_PAGE_SCHEMES = ("mailto:", "javascript:", "tel:", "data:")

_NON_LISTING_SEGMENTS = frozenset(
    {
        "about",
        "about-us",
        "o-nama",
        "blog",
        "news",
        "novosti",
        "vesti",
        "saveti",
        "login",
        "log-in",
        "signin",
        "sign-in",
        "signup",
        "sign-up",
        "register",
        "registration",
        "terms",
        "terms-of-use",
        "uslovi",
        "uslovi-koriscenja",
        "privacy",
        "privacy-policy",
        "privatnost",
        "politika-privatnosti",
        "faq",
        "help",
        "contact",
        "contact-us",
        "kontakt",
        "pricing",
        "plans",
        "legal",
        "imprint",
        "cookies",
        "company",
        "companies",
        "category",
        "categories",
        "tags",
        "topics",
        "kompanija",
        "kompanije",
        "prakse",
        "iskustva",
        "kategorija",
        "kategorije",
    }
)
_SOCIAL_HOSTS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        "discord.gg",
        "discord.com",
        "t.me",
        "telegram.me",
        "pinterest.com",
        "reddit.com",
    }
)


@dataclass(frozen=True)
class SourceRef:
    """A resolved discovery source: the input as given and where it points."""

    input: str
    url: str
    site: str | None
    site_status: str | None
    search_url: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    """Candidate links from one source, split against the database."""

    source: SourceRef
    candidates: tuple[dict[str, object], ...]
    new_urls: tuple[str, ...]
    status_counts: dict[str, int] = field(default_factory=dict)
    query: str | None = None
    fetched_url: str | None = None

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
    site, url, status, search = match
    return SourceRef(input=text, url=url, site=site, site_status=status, search_url=search)


def list_sources() -> list[SourceRef]:
    """Return every known site from job-sources/, deduplicated and sorted by name."""
    seen: set[str] = set()
    sources: list[SourceRef] = []
    for site, url, status, search in _job_source_rows():
        key = site.casefold()
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            SourceRef(input=site, url=url, site=site, site_status=status, search_url=search)
        )
    return sorted(sources, key=lambda source: (source.site or "").casefold())


def source_warning(source: SourceRef) -> str | None:
    """Return a human note when a named source is not currently live."""
    if source.site is not None and source.site_status in NON_LIVE_STATUSES:
        return f"Source '{source.site}' is {source.site_status}; results may be empty."
    return None


def _strip_ext(segment: str) -> str:
    return segment.split(".", 1)[0].casefold()


def _is_social(host: str, path: str) -> bool:
    bare = host.removeprefix("www.")
    if bare in _SOCIAL_HOSTS:
        return True
    return bare.endswith("linkedin.com") and path.startswith("/company")


def _is_probable_listing(url: str) -> bool:
    """Drop obvious page chrome (nav, legal, social) so the limit counts listings."""
    parsed = urlparse(url)
    path = parsed.path
    if _is_social(parsed.netloc.lower(), path):
        return False
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return False
    last = segments[-1].casefold()
    if last.endswith((".rss", ".xml")):
        return False
    if len(segments) == 1 and _strip_ext(last) in _NON_LISTING_SEGMENTS:
        return False
    return _strip_ext(segments[0]) not in _NON_LISTING_SEGMENTS


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
        if not _is_probable_listing(resolved):
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


def _substitute_query(source: SourceRef, query: str) -> str:
    """Return the source's search URL with the query term substituted in."""
    template = source.search_url
    if template is None or _SEARCH_PLACEHOLDER not in template:
        raise ScrapeError(f"Source '{source.site}' has no usable search template")
    return template.replace(_SEARCH_PLACEHOLDER, quote_plus(query.strip()))


def discover(
    source: SourceRef,
    *,
    limit: int = DEFAULT_LIMIT,
    query: str | None = None,
    only_live: bool = False,
    user_id: int | None = None,
) -> DiscoveryResult:
    """Fetch the source page and classify its candidate links against the DB.

    When ``query`` is set and the source declares a search template, the term is
    substituted into that URL so the board filters server-side; otherwise the
    term filters the extracted links client-side. ``only_live`` refuses to fetch
    named sources whose job-sources status is blocked/login/dead. ``user_id``
    scopes the "known" set to one account's positions.
    """
    if only_live and source.site is not None and source.site_status != "live":
        raise ScrapeError(f"Source '{source.site}' is {source.site_status}; skipped (only_live).")
    term = query.strip() if query else None
    fetched_url = source.url
    client_term: str | None = None
    if term:
        if source.search_url is not None:
            fetched_url = _substitute_query(source, term)
        else:
            client_term = term
    html = scraper.fetch_html(fetched_url)
    index = {clean_url(job["job_url"]): job for job in db.get_jobs(user_id=user_id)}
    urls = extract_links(html, fetched_url, MAX_LIMIT if client_term else limit)
    if client_term:
        needle = client_term.casefold()
        urls = [url for url in urls if needle in url.casefold()][:limit]
    candidates: list[dict[str, object]] = []
    new_urls: list[str] = []
    status_counts: dict[str, int] = {}
    for url in urls:
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
        query=term,
        fetched_url=fetched_url,
    )


def _job_source_rows() -> list[tuple[str, str, str, str | None]]:
    """Return (site, url, status, search) rows from the job-sources region files."""
    if not JOB_SOURCES_DIR.is_dir():
        return []
    rows: list[tuple[str, str, str, str | None]] = []
    for path in sorted(JOB_SOURCES_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        rows.extend(_parse_region_file(path))
    return rows


def _parse_region_file(path: Path) -> list[tuple[str, str, str, str | None]]:
    rows: list[tuple[str, str, str, str | None]] = []
    in_frontmatter = True
    for line in path.read_text(encoding="utf-8").splitlines():
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if not line.startswith("| ") or line.startswith("|--"):
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if cells in (_HEADER, _HEADER_SEARCH) or len(cells) not in (5, 6):
            continue
        site, url, _typ, _notes, status = cells[:5]
        search = cells[5] if len(cells) == 6 and cells[5] else None
        rows.append((site, url, status, search))
    return rows


def _lookup_site(name: str) -> tuple[str, str, str, str | None] | None:
    wanted = name.casefold()
    for site, url, status, search in _job_source_rows():
        if site.casefold() == wanted:
            return site, url, status, search
    return None
