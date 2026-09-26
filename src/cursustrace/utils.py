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


def _fingerprint(*parts: str) -> str:
    payload = ":".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_fingerprint(url: str, company: str | None, title: str | None) -> str:
    """Hash the canonical URL with the normalized company and title into a fingerprint."""
    return _fingerprint(clean_url(url), _normalize(company), _normalize(title))


def role_key(company: str | None, title: str | None) -> str:
    """Hash the normalized company and title, used to spot the same role on another link."""
    return _fingerprint(_normalize(company), _normalize(title))
