"""Domain exceptions for cursustrace."""

from __future__ import annotations


class CursustraceError(Exception):
    """Base class for all cursustrace domain errors."""


class ScrapeError(CursustraceError):
    """Raised when a job listing cannot be fetched or parsed."""
