"""Job listing scraping and extraction."""

from __future__ import annotations

import json
import re
from typing import TypedDict
from urllib.parse import urlparse

import curl_cffi
import trafilatura
from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.exceptions import RequestException

from cursustrace.errors import ScrapeError
from cursustrace.validation import normalize_deadline

REQUEST_TIMEOUT = 15
DEFAULT_LOCATION = "Not Specified"
MIN_DESCRIPTION_CHARS = 600

_SECOND_LEVEL_SUFFIXES = frozenset({"co", "com", "org", "net", "gov", "ac", "edu"})

_PAYLOAD_KEYS: tuple[str, ...] = (
    "Job_Description",
    "jobDescription",
    "job_description",
    "jobDescriptionHtml",
    "descriptionHtml",
    "jobPostingDescription",
)
_JSON_PAYLOAD_KEYS = frozenset({key.casefold() for key in _PAYLOAD_KEYS} | {"description"})
_JS_ESCAPE_RE = re.compile(r"\\(?:x([0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|(.))")
_JS_SIMPLE_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
}


class ScrapedJob(TypedDict):
    """Metadata extracted from a job listing page."""

    title: str | None
    company: str | None
    location: str
    description: str | None
    deadline: str | None


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag):
            content = tag.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


def _text_of(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    text = tag.get_text(" ", strip=True)
    return text or None


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


def _description_is_sufficient(description: str | None, title: str | None) -> bool:
    text = (description or "").strip()
    if len(text) < MIN_DESCRIPTION_CHARS:
        return False
    if title is None:
        return True
    return text.casefold() != title.strip().casefold()


def data_is_sufficient(data: ScrapedJob) -> bool:
    """Whether the scraped payload carries a real job description, not page chrome."""
    return _description_is_sufficient(data["description"], data["title"])


def _infer_company(url: str) -> str | None:
    netloc = urlparse(url).netloc.removeprefix("www.")
    if not netloc:
        return None
    labels = netloc.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL_SUFFIXES:
        name = labels[-3]
    elif len(labels) >= 2:
        name = labels[-2]
    else:
        name = labels[0]
    return name.capitalize() or None


def _extract_description(html: str, url: str, soup: BeautifulSoup) -> str | None:
    extracted = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_comments=False,
    )
    if extracted and extracted.strip():
        return extracted.strip()
    fallback = soup.get_text(" ", strip=True)
    return fallback or None


def _decode_js_escapes(text: str) -> str:
    """Decode JS string escapes (\\xNN, \\uNNNN, \\n, \\-) in one pass."""

    def _replace(match: re.Match[str]) -> str:
        hex_digits = match.group(1)
        if hex_digits is not None:
            return chr(int(hex_digits, 16))
        unicode_digits = match.group(2)
        if unicode_digits is not None:
            return chr(int(unicode_digits, 16))
        char = match.group(3)
        if char is None:
            return match.group(0)
        return _JS_SIMPLE_ESCAPES.get(char, char)

    return _JS_ESCAPE_RE.sub(_replace, text)


def _scan_quoted(decoded: str, start: int, quote: str) -> str | None:
    chars: list[str] = []
    index = start
    while index < len(decoded):
        char = decoded[index]
        if char == "\\" and index + 1 < len(decoded):
            chars.append(char)
            chars.append(decoded[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(chars)
        chars.append(char)
        index += 1
    return None


def _quoted_value_after(decoded: str, key: str) -> str | None:
    needle = f'"{key}"'
    position = decoded.find(needle)
    while position != -1:
        index = position + len(needle)
        while index < len(decoded) and decoded[index] in " \t\r\n":
            index += 1
        if index < len(decoded) and decoded[index] == ":":
            index += 1
            while index < len(decoded) and decoded[index] in " \t\r\n":
                index += 1
            if index < len(decoded) and decoded[index] in "\"'":
                return _scan_quoted(decoded, index + 1, decoded[index])
        position = decoded.find(needle, position + 1)
    return None


def _html_to_text(fragment: str) -> str:
    text = BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _json_payload_description(data: object) -> str | None:
    """Find the longest string stored under a job-description key in parsed JSON."""
    best: str | None = None

    def _visit(node: object) -> None:
        nonlocal best
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    if key.casefold() in _JSON_PAYLOAD_KEYS and (
                        best is None or len(value) > len(best)
                    ):
                        best = value
                else:
                    _visit(value)
        elif isinstance(node, list):
            for item in node:
                _visit(item)

    _visit(data)
    return best


def _extract_payload_description(soup: BeautifulSoup) -> str | None:
    """Pull a job description out of inline scripts (embedded JSON or JS-escaped data)."""
    best: str | None = None
    for tag in soup.find_all("script"):
        source = tag.string or ""
        if not source.strip():
            continue
        stripped = source.lstrip()
        if stripped[:1] in "[{":
            try:
                parsed: object = json.loads(source)
            except ValueError:
                parsed = None
            if parsed is not None:
                found = _json_payload_description(parsed)
                if found:
                    text = _html_to_text(found)
                    if text and (best is None or len(text) > len(best)):
                        best = text
        if not any(key in source for key in _PAYLOAD_KEYS):
            continue
        decoded = _decode_js_escapes(source)
        for key in _PAYLOAD_KEYS:
            value = _quoted_value_after(decoded, key)
            if not value:
                continue
            text = _html_to_text(_decode_js_escapes(value))
            if text and (best is None or len(text) > len(best)):
                best = text
    return best


def _jsonld_jobposting_items(data: object) -> list[dict[str, object]]:
    """Return every JobPosting item in a parsed JSON-LD payload (incl. @graph)."""
    items: list[object]
    if isinstance(data, list):
        items = list(data)
    elif isinstance(data, dict):
        items = [data]
        if isinstance(data.get("@graph"), list):
            items.extend(data["@graph"])
    else:
        items = []
    found: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        jtype = item.get("@type")
        types = [jtype] if isinstance(jtype, str) else jtype if isinstance(jtype, list) else []
        if any(isinstance(entry, str) and entry.lower() == "jobposting" for entry in types):
            found.append(item)
    return found


def _jsonld_location(data: object) -> str | None:
    """Pull a location string from a parsed JSON-LD JobPosting payload."""
    for item in _jsonld_jobposting_items(data):
        location = item.get("jobLocation")
        if isinstance(location, list):
            location = next((entry for entry in location if isinstance(entry, dict)), None)
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, str) and address.strip():
            return address.strip()
        if not isinstance(address, dict):
            continue
        parts: list[str] = []
        seen: set[str] = set()
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                normalized = value.strip().lower()
                if normalized not in seen:
                    seen.add(normalized)
                    parts.append(value.strip())
        if parts:
            return ", ".join(parts)
    return None


def _jsonld_deadline(data: object) -> str | None:
    """Pull the validThrough deadline from a parsed JSON-LD JobPosting payload."""
    for item in _jsonld_jobposting_items(data):
        valid_through = item.get("validThrough")
        if isinstance(valid_through, str) and valid_through.strip():
            return normalize_deadline(valid_through)
    return None


def _extract_location(soup: BeautifulSoup, description: str | None) -> str | None:
    """Resolve a job location: Open Graph meta, then JSON-LD, then description marker."""
    meta = _meta_content(soup, "og:locality", "og:region", "job:location")
    if meta is not None:
        return meta
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = tag.string or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        found = _jsonld_location(data)
        if found is not None:
            return found
    if description:
        match = re.search(r"📍\s*Location:\s*([^\n\r]+)", description)
        if match is not None:
            return match.group(1).strip()
    return None


def _extract_deadline(soup: BeautifulSoup) -> str | None:
    """Resolve the application deadline from JSON-LD validThrough, normalized to YYYY-MM-DD."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = tag.string or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        found = _jsonld_deadline(data)
        if found is not None:
            return found
    return None


def _get(url: str, headers: dict[str, str] | None = None) -> curl_cffi.Response:
    """GET through Chrome impersonation so TLS/HTTP2 fingerprints match the headers.

    Raises a curl_cffi ``RequestException`` on transport failures and HTTP errors.
    """
    response = curl_cffi.get(url, headers=headers, impersonate="chrome", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()  # type: ignore[no-untyped-call]
    return response


def fetch_html(url: str) -> str:
    """Fetch any page with the shared timeout; raise ScrapeError on failure."""
    try:
        response = _get(url)
    except RequestException as exc:
        raise ScrapeError(f"Unable to fetch page {url}: {exc}") from exc
    return response.text


def _extract_job(html: str, url: str) -> ScrapedJob:
    """Extract listing fields from HTML with the static fallback ladder (no content gate)."""
    soup = BeautifulSoup(html, "html.parser")
    title = _first_nonempty(
        _meta_content(soup, "og:title"),
        _text_of(soup.find("h1")),
        _text_of(soup.title),
    )
    description = _extract_description(html, url, soup)
    if not _description_is_sufficient(description, title):
        payload = _extract_payload_description(soup)
        if payload is not None and _is_better_description(payload, description):
            description = payload
    if not _description_is_sufficient(description, title):
        meta = _meta_content(soup, "og:description", "description")
        if meta is not None and _is_better_description(meta, description):
            description = meta
    return ScrapedJob(
        title=title,
        company=_meta_content(soup, "og:site_name") or _infer_company(url),
        location=_extract_location(soup, description) or DEFAULT_LOCATION,
        description=description,
        deadline=_extract_deadline(soup),
    )


def _is_better_description(candidate: str, current: str | None) -> bool:
    return _description_is_sufficient(candidate, None) or len(candidate) > len(current or "")


def scrape_job(url: str) -> ScrapedJob:
    """Fetch a job listing and extract title, company, location, description and deadline.

    Raises ``ScrapeError`` when the page carries no real job content (typically a
    JavaScript-rendered shell), so callers never store title-only junk.
    """
    try:
        response = _get(url)
    except RequestException as exc:
        raise ScrapeError(f"Unable to fetch job listing {url}: {exc}") from exc

    job = _extract_job(response.text, url)
    if data_is_sufficient(job):
        return job
    raise ScrapeError(
        f"No job content found at {url} (JavaScript-rendered or blocked); add the position manually"
    )
