"""Render JavaScript-driven listing pages with a headless browser."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

ENV_BINARY = "CURSUS_LIGHTPANDA"
ENV_BROWSER = "CURSUS_CHROMIUM"
RENDER_TIMEOUT_SECONDS = 20
BROWSER_TIMEOUT_SECONDS = 45
RENDER_WAIT_MS = 5000
BROWSER_WAIT_MS = 15000
_BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")


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


def find_browser() -> str | None:
    """Locate a Chrome/Chromium binary via CURSUS_CHROMIUM or PATH; None when absent."""
    override = os.environ.get(ENV_BROWSER, "").strip()
    if override:
        return shutil.which(override) or (override if os.path.isfile(override) else None)
    for name in _BROWSER_NAMES:
        found = shutil.which(name)
        if found is not None:
            return found
    return None


def _browser_command(binary: str, url: str, profile: str) -> list[str]:
    """Build the headless Chrome/Chromium command for a DOM dump."""
    command = [
        binary,
        "--headless",
        "--disable-gpu",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={BROWSER_WAIT_MS}",
        "--dump-dom",
        url,
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.insert(1, "--no-sandbox")
    return command


def render_html_browser(url: str) -> str | None:
    """Render a page in headless Chrome/Chromium and return the hydrated DOM.

    Returns None when no browser is installed, the render times out, or the
    browser exits without usable output, so callers can fall through to the
    next tier instead of failing the whole scrape.
    """
    binary = find_browser()
    if binary is None:
        logger.warning(
            "no Chrome/Chromium binary found; skipping browser render for %s "
            "(install one or set %s)",
            url,
            ENV_BROWSER,
        )
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="cursustrace-chrome-") as profile:
            command = _browser_command(binary, url, profile)
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=BROWSER_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "headless browser timed out after %ss rendering %s",
                    BROWSER_TIMEOUT_SECONDS,
                    url,
                )
                return None
            except OSError as exc:
                logger.warning("headless browser failed to start for %s: %s", url, exc)
                return None
    except OSError as exc:
        logger.warning("could not create a browser profile for %s: %s", url, exc)
        return None
    stdout = result.stdout or ""
    if result.returncode != 0 or not stdout.strip():
        detail = (result.stderr or "").strip()[:200]
        logger.warning(
            "headless browser exited %s rendering %s: %s", result.returncode, url, detail
        )
        return None
    return stdout
