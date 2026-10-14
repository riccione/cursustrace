"""Application version lookup."""

from __future__ import annotations

import importlib.metadata


def app_version() -> str:
    """Return the installed version, or 'unknown' when the package metadata is absent."""
    try:
        return importlib.metadata.version("cursustrace")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
