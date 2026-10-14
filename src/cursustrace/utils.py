"""URL and metadata normalization helpers for deduplication."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Query parameters that identify a specific posting on boards that carry the id
# in the query string (e.g. plata.careers?id=..., Greenhouse ?gh_jid=...). All
# other parameters (utm_*, ref, gh_src, board tracking, ...) are dropped.
_IDENTITY_PARAMS = frozenset(
    {
        "id",
        "jobid",
        "job_id",
        "jid",
        "gh_jid",
        "ashby_jid",
        "posting",
        "postingid",
        "posting_id",
        "vacancy",
        "vacancy_id",
        "position",
        "position_id",
        "opening",
        "opening_id",
        "requisition",
        "requisition_id",
        "req",
        "reqid",
        "reference",
        "job_reference",
    }
)


def clean_url(url: str) -> str:
    """Return a canonical URL keeping only posting-identifying query parameters.

    Tracking parameters (utm_*, ref, gh_src, ...) are dropped, but parameters
    that carry the posting id on some boards are preserved and sorted so the
    same listing keeps one canonical form regardless of parameter order.
    """
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() in _IDENTITY_PARAMS
    ]
    query = urlencode(sorted(kept))
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


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
