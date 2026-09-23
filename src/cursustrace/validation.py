"""Shared field validation for job positions."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse


def validate_url(value: str | None) -> str | None:
    """Return an error message unless the value is a full http(s) URL."""
    text = (value or "").strip()
    if not text:
        return "Job URL is required."
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Enter a full URL starting with http:// or https://."
    return None


def required_error(label: str, value: str | None) -> str | None:
    """Return a '<label> is required.' message when the value is blank."""
    return None if (value or "").strip() else f"{label} is required."


def validate_required(label: str) -> Callable[[str | None], str | None]:
    """Build a validation callable for NiceGUI inputs."""

    def check(value: str | None) -> str | None:
        return required_error(label, value)

    return check
