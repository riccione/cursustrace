"""Job listing scraping and extraction."""

from __future__ import annotations

from typing import TypedDict
from urllib.parse import urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup, Tag

from cursustrace.errors import ScrapeError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
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


def scrape_job(url: str) -> ScrapedJob:
    """Fetch a job listing and extract its title, company, location and description."""
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"Unable to fetch job listing {url}: {exc}") from exc

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    return ScrapedJob(
        title=_first_nonempty(
            _meta_content(soup, "og:title"),
            _text_of(soup.find("h1")),
            _text_of(soup.title),
        ),
        company=_meta_content(soup, "og:site_name") or _infer_company(url),
        location=_meta_content(soup, "og:locality", "og:region", "job:location") or DEFAULT_LOCATION,
        description=_extract_description(html, url, soup),
    )
