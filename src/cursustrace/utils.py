"""URL and metadata normalization helpers for deduplication."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def clean_url(url: str) -> str:
    """Return a canonical URL with query string and fragment removed."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), host, path, "", "", ""))


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return _NON_ALNUM.sub("", text.lower())


def generate_fingerprint(company: str | None, title: str | None, location: str | None) -> str:
    """Hash the normalized company/title/location triple into a stable fingerprint."""
    payload = f"{_normalize(company)}:{_normalize(title)}:{_normalize(location)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
