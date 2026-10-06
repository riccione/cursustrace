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

REQUEST_TIMEOUT = 15
DEFAULT_LOCATION = "Not Specified"

_SECOND_LEVEL_SUFFIXES = frozenset({"co", "com", "org", "net", "gov", "ac", "edu"})


class ScrapedJob(TypedDict):
    """Metadata extracted from a job listing page."""

    title: str | None
    company: str | None
    location: str
    description: str | None


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


def _jsonld_location(data: object) -> str | None:
    """Pull a location string from a parsed JSON-LD JobPosting payload."""
    items: list[object]
    if isinstance(data, list):
        items = list(data)
    elif isinstance(data, dict):
        items = [data]
        if isinstance(data.get("@graph"), list):
            items.extend(data["@graph"])
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        jtype = item.get("@type")
        types = [jtype] if isinstance(jtype, str) else jtype if isinstance(jtype, list) else []
        if not any(isinstance(entry, str) and entry.lower() == "jobposting" for entry in types):
            continue
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


def scrape_job(url: str) -> ScrapedJob:
    """Fetch a job listing and extract its title, company, location and description."""
    try:
        response = _get(url)
    except RequestException as exc:
        raise ScrapeError(f"Unable to fetch job listing {url}: {exc}") from exc

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    description = _extract_description(html, url, soup)

    return ScrapedJob(
        title=_first_nonempty(
            _meta_content(soup, "og:title"),
            _text_of(soup.find("h1")),
            _text_of(soup.title),
        ),
        company=_meta_content(soup, "og:site_name") or _infer_company(url),
        location=_extract_location(soup, description) or DEFAULT_LOCATION,
        description=description,
    )
