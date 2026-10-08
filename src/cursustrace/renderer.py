"""Render JavaScript-driven listing pages with the lightpanda browser."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

ENV_BINARY = "CURSUS_LIGHTPANDA"
RENDER_TIMEOUT_SECONDS = 20
RENDER_WAIT_MS = 5000


def find_binary() -> str | None:
    """Locate the lightpanda binary via CURSUS_LIGHTPANDA or PATH; None when absent."""
    override = os.environ.get(ENV_BINARY, "").strip()
    if override:
        return shutil.which(override)
    return shutil.which("lightpanda")


def render_html(url: str) -> str | None:
    """Render a page in lightpanda and return the final DOM as HTML.

    Returns None when the binary is missing, the render times out, or the
    browser exits without usable output, so callers can fall through to the
    next tier instead of failing the whole scrape.
    """
    binary = find_binary()
    if binary is None:
        logger.warning(
            "lightpanda not found; skipping render for %s (install lightpanda or set %s)",
            url,
            ENV_BINARY,
        )
        return None
    command = [
        binary,
        "fetch",
        url,
        "--dump",
        "html",
        "--wait-until",
        "done",
        "--wait-ms",
        str(RENDER_WAIT_MS),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("lightpanda timed out after %ss rendering %s", RENDER_TIMEOUT_SECONDS, url)
        return None
    except OSError as exc:
        logger.warning("lightpanda failed to start for %s: %s", url, exc)
        return None
    stdout = result.stdout or ""
    if result.returncode != 0 or not stdout.strip():
        detail = (result.stderr or "").strip()[:200]
        logger.warning("lightpanda exited %s rendering %s: %s", result.returncode, url, detail)
        return None
    return stdout
