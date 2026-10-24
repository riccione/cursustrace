"""Source resolution and link discovery for agent-driven position ingestion.

This module only does mechanics: fetch a source page, extract candidate
listing links, and split them against the database. Deciding which candidates
are relevant is left to the caller (an AI agent or a human).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
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
ALL_SOURCES = "*"
BATCH_PER_BOARD = 10
BATCH_TOTAL = 300
BATCH_WORKERS = 6

logger = logging.getLogger(__name__)
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

_DETAIL_SEGMENTS = frozenset(
    {
        "j",
        "job",
        "jobs",
        "job-detail",
        "job-details",
        "position",
        "positions",
        "vacancy",
        "vacancies",
        "opening",
        "openings",
        "offer",
        "offers",
        "career",
        "careers",
        "posting",
        "postings",
        "listing",
        "listings",
        "stellenangebot",
        "stellenangebote",
        "posao",
        "poslovi",
        "oglas",
        "konkurs",
        "p",
    }
)
_JOB_ROOTS = frozenset({"jobs", "job", "careers", "career"})
_FACET_SEGMENTS = frozenset(
    {
        "remote",
        "hybrid",
        "onsite",
        "locations",
        "location",
        "companies",
        "company",
        "categories",
        "category",
        "tags",
        "topics",
        "tools",
        "salaries",
        "salary",
    }
)
_FACET_INTERIOR = _FACET_SEGMENTS - {"companies", "company"}
_CATEGORY_LAST = frozenset(
    {"jobs", "job", "careers", "career", "remote-jobs", "search", "salary", "salaries"}
)
_CATEGORY_TAIL_RE = re.compile(
    r"(?:^|/)(?:[^/]*-jobs?|jobs-with-[^/]*|job-with-[^/]*)$", re.IGNORECASE
)
_LISTING_ROOT_RE = re.compile(
    r"^/(?:jobs?|careers?|remote-jobs?|search|stellenangebote?|vacanc(?:y|ies)"
    r"|positions?|openings?|poslovi|posao)(?:/|$)",
    re.IGNORECASE,
)
_LISTING_TEXT_RE = re.compile(
    r"\b(jobs?|careers?|vacanc(?:y|ies)|positions?|openings?|search)\b", re.IGNORECASE
)
_ACTION_TEXT_RE = re.compile(
    r"\b(post|submit|hire|pricing|login|sign\s?in|register)\b", re.IGNORECASE
)
_MIN_DETAIL_SCORE = 4
_MIN_LINKS_BEFORE_FOLLOW = 2


@dataclass(frozen=True)
class SourceRef:
    """A resolved discovery source: the input as given and where it points."""

    input: str
    url: str
    site: str | None
    site_status: str | None
    search_url: str | None = None


@dataclass(frozen=True)
class LinkEntry:
    """A candidate link with its anchor text and detail score."""

    url: str
    title: str | None
    score: int


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


def _is_social(host: str, path: str) -> bool:
    bare = host.removeprefix("www.")
    if bare in _SOCIAL_HOSTS:
        return True
    return bare.endswith("linkedin.com") and path.startswith("/company")


def _score_candidate(url: str, text: str) -> int:
    """Score how likely a link is a job detail rather than navigation.

    A negative score means the link is page chrome and should be dropped.
    """
    parsed = urlparse(url)
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if not segments:
        return -1
    last = segments[-1]
    if segments[0] in _NON_LISTING_SEGMENTS or last in _NON_LISTING_SEGMENTS:
        return -1
    if last.endswith((".rss", ".xml")):
        return -1
    parts = re.split(r"[-_]", last)
    id_tail = last.isdigit() or (
        len(parts) >= 2
        and (
            (parts[-1].isdigit() and len(parts[-1]) >= 3)
            or (
                6 <= len(parts[-1]) <= 24
                and parts[-1].islower()
                and any(char.isdigit() for char in parts[-1])
            )
        )
    )
    marker = any(segment in _DETAIL_SEGMENTS for segment in segments[:-1])
    category = bool(_CATEGORY_TAIL_RE.search(parsed.path))
    if (
        len(segments) == 2
        and segments[0] in _JOB_ROOTS
        and (
            segments[1] in _FACET_SEGMENTS or (not id_tail and len(last) <= 12 and "-" not in last)
        )
    ):
        category = True
    if len(segments) == 3 and segments[0] in _JOB_ROOTS and segments[1] in _FACET_SEGMENTS:
        category = True
    if last in _CATEGORY_LAST:
        category = True
    if not id_tail and any(segment in _FACET_INTERIOR for segment in segments[1:-1]):
        category = True
    if category:
        return -1
    title_like = (
        len(text) >= 18 and len(text.split()) >= 3 and not text.casefold().endswith(("jobs", "job"))
    )
    score = 0
    if id_tail:
        score += 4
    if marker:
        score += 2
    if title_like:
        score += 2
    if len(segments) >= 3:
        score += 1
    return score


def extract_link_entries(
    html: str, base_url: str, limit: int = DEFAULT_LIMIT, *, query: str | None = None
) -> list[LinkEntry]:
    """Return the most job-like links, ranked by detail likelihood.

    Resolves relative hrefs, keeps query strings, drops fragments, non-http
    schemes, social and page-chrome links, and deduplicates by canonical URL.
    ``query`` keeps links whose URL or anchor text contains the term.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_canonical = clean_url(base_url)
    needle = query.strip().casefold() if query else None
    entries: dict[str, LinkEntry] = {}
    order: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(_NON_PAGE_SCHEMES):
            continue
        resolved = urljoin(base_url, href).split("#", 1)[0]
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            continue
        if _is_social(parsed.netloc.lower(), parsed.path):
            continue
        canonical = clean_url(resolved)
        if canonical == base_canonical or not urlparse(canonical).path:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split()) or None
        if (
            needle is not None
            and needle not in resolved.casefold()
            and (text is None or needle not in text.casefold())
        ):
            continue
        score = _score_candidate(resolved, text or "")
        if score < _MIN_DETAIL_SCORE:
            continue
        entry = LinkEntry(url=resolved, title=text, score=score)
        current = entries.get(canonical)
        if current is None:
            entries[canonical] = entry
            order.append(canonical)
        elif score > current.score:
            entries[canonical] = entry
    ranked = sorted(order, key=lambda canonical: -entries[canonical].score)
    return [entries[canonical] for canonical in ranked[:limit]]


def extract_links(
    html: str, base_url: str, limit: int = DEFAULT_LIMIT, *, query: str | None = None
) -> list[str]:
    """Return the URLs of the highest-ranked candidate links."""
    return [entry.url for entry in extract_link_entries(html, base_url, limit, query=query)]


def _pick_listing_page(html: str, base_url: str, query: str | None) -> str | None:
    """Pick a same-site listings page to follow when a homepage has no job links."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    needle = query.strip().casefold() if query else None
    best: tuple[int, str] | None = None
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(_NON_PAGE_SCHEMES):
            continue
        resolved = urljoin(base_url, href).split("#", 1)[0]
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower().removeprefix("www.") != base_host:
            continue
        segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
        if not segments:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        path_match = bool(_LISTING_ROOT_RE.search(parsed.path))
        if not path_match and (not _LISTING_TEXT_RE.search(text) or _ACTION_TEXT_RE.search(text)):
            continue
        if any(segment in _NON_LISTING_SEGMENTS for segment in segments):
            continue
        if _score_candidate(resolved, text) >= _MIN_DETAIL_SCORE:
            continue
        score = 4 if path_match else 0
        if needle is not None and (needle in resolved.casefold() or needle in text.casefold()):
            score += 3
        if len(segments) == 1:
            score += 2
        elif len(segments) == 2:
            score += 1
        if best is None or score > best[0]:
            best = (score, resolved)
    return best[1] if best is not None and best[0] > 0 else None


def _substitute_query(source: SourceRef, query: str) -> str:
    """Return the source's search URL with the query term substituted in."""
    template = source.search_url
    if template is None or _SEARCH_PLACEHOLDER not in template:
        raise ScrapeError(f"Source '{source.site}' has no usable search template")
    return template.replace(_SEARCH_PLACEHOLDER, quote_plus(query.strip()))


def _known_index(user_id: int | None) -> dict[str, db.Job]:
    """Map canonical URLs to the tracked jobs of one owner."""
    return {clean_url(job["job_url"]): job for job in db.get_jobs(user_id=user_id)}


def _classify(
    urls: list[str], index: Mapping[str, db.Job]
) -> tuple[list[dict[str, object]], list[str], dict[str, int]]:
    """Split URLs into candidates, new URLs, and known status counts."""
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
    return candidates, new_urls, status_counts


def _discover_page(
    source: SourceRef,
    *,
    limit: int,
    query: str | None,
    index: Mapping[str, db.Job],
) -> DiscoveryResult:
    """Fetch one source and classify its links against a prebuilt index."""
    term = query.strip() if query else None
    fetched_url = source.url
    client_term: str | None = None
    if term:
        if source.search_url is not None:
            fetched_url = _substitute_query(source, term)
        else:
            client_term = term
    html = scraper.fetch_html(fetched_url)
    entries = extract_link_entries(html, fetched_url, limit, query=client_term)
    if (
        len(entries) < _MIN_LINKS_BEFORE_FOLLOW
        and source.site is not None
        and source.search_url is None
    ):
        listing_url = _pick_listing_page(html, fetched_url, term)
        if listing_url is not None and clean_url(listing_url) != clean_url(fetched_url):
            try:
                listing_html = scraper.fetch_html(listing_url)
            except ScrapeError:
                listing_html = None
            if listing_html is not None:
                fetched_url = listing_url
                entries = extract_link_entries(listing_html, listing_url, limit, query=client_term)
    urls = [entry.url for entry in entries]
    candidates, new_urls, status_counts = _classify(urls, index)
    titles = {entry.url: entry.title for entry in entries}
    for candidate in candidates:
        if not candidate.get("known"):
            candidate["title"] = titles.get(str(candidate["url"]))
    return DiscoveryResult(
        source=source,
        candidates=tuple(candidates),
        new_urls=tuple(new_urls),
        status_counts=status_counts,
        query=term,
        fetched_url=fetched_url,
    )


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
    return _discover_page(source, limit=limit, query=query, index=_known_index(user_id))


@dataclass(frozen=True)
class BoardError:
    """A board that could not be fetched during batch discovery."""

    source: SourceRef
    error: str


@dataclass(frozen=True)
class DiscoveryBatch:
    """New candidates merged across several boards, deduplicated by URL."""

    source: SourceRef
    candidates: tuple[dict[str, object], ...]
    new_urls: tuple[str, ...]
    checked: int
    extracted: int
    skipped_not_live: int
    known_count: int
    empty: int
    errors: tuple[BoardError, ...]
    truncated: bool
    cancelled: bool = False
    query: str | None = None

    @property
    def new_count(self) -> int:
        return len(self.new_urls)


def discover_all(
    sources: list[SourceRef] | tuple[SourceRef, ...],
    *,
    per_board: int = BATCH_PER_BOARD,
    total: int = BATCH_TOTAL,
    query: str | None = None,
    only_live: bool = True,
    user_id: int | None = None,
    workers: int = BATCH_WORKERS,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> DiscoveryBatch:
    """Fetch many boards concurrently and merge their new links.

    Known links are dropped from the output but counted; duplicates across
    boards keep their first occurrence. Fetching stops once ``total`` new links
    are found or ``cancelled`` returns True. Per-board failures are collected
    instead of aborting the batch.
    """
    selected = [source for source in sources if not only_live or source.site_status == "live"]
    skipped_not_live = len(sources) - len(selected)
    index = _known_index(user_id)
    candidates: list[dict[str, object]] = []
    new_urls: list[str] = []
    seen_urls: set[str] = set()
    known_urls: set[str] = set()
    errors: list[BoardError] = []
    status_counts: dict[str, int] = {}
    checked = extracted = known_count = empty = 0
    truncated = False
    was_cancelled = False
    if selected:
        pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(selected))))
        futures = {
            pool.submit(_discover_page, source, limit=per_board, query=query, index=index): source
            for source in selected
        }
        try:
            for future in as_completed(futures):
                source = futures[future]
                if cancelled is not None and cancelled():
                    was_cancelled = True
                    break
                checked += 1
                try:
                    page = future.result()
                except ScrapeError as exc:
                    errors.append(BoardError(source=source, error=str(exc)))
                except Exception as exc:
                    logger.exception("Board discovery failed for %s", source.url)
                    errors.append(BoardError(source=source, error=str(exc)))
                else:
                    extracted += len(page.candidates)
                    if not page.candidates:
                        empty += 1
                    for status, count in page.status_counts.items():
                        status_counts[status] = status_counts.get(status, 0) + count
                    for candidate in page.candidates:
                        canonical = clean_url(str(candidate["url"]))
                        if candidate.get("known"):
                            if canonical not in known_urls:
                                known_urls.add(canonical)
                                known_count += 1
                            continue
                        if canonical in seen_urls:
                            continue
                        seen_urls.add(canonical)
                        candidates.append(
                            {
                                "url": candidate["url"],
                                "known": False,
                                "site": source.site,
                                "title": candidate.get("title"),
                            }
                        )
                        new_urls.append(str(candidate["url"]))
                if progress is not None:
                    progress(checked, len(selected), source.site or source.url)
                if len(new_urls) >= total:
                    truncated = True
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    return DiscoveryBatch(
        source=SourceRef(
            input="All known boards", url="", site="All known boards", site_status=None
        ),
        candidates=tuple(candidates[:total]),
        new_urls=tuple(new_urls[:total]),
        checked=checked,
        extracted=extracted,
        skipped_not_live=skipped_not_live,
        known_count=known_count,
        empty=empty,
        errors=tuple(errors),
        truncated=truncated,
        cancelled=was_cancelled,
        query=query.strip() if query else None,
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
