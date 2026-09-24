"""Domain exceptions for cursustrace."""

from __future__ import annotations


class CursustraceError(Exception):
    """Base class for all cursustrace domain errors."""


class ScrapeError(CursustraceError):
    """Raised when a job listing cannot be fetched or parsed."""


class PdfExportError(CursustraceError):
    """Raised when a Markdown CV cannot be rendered to PDF."""


class ConfigError(CursustraceError):
    """Raised when configuration is missing, unreadable, or invalid."""
