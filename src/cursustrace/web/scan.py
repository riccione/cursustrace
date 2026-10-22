"""URL scanning: parse the textarea, scrape one URL, and run the scan loop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

from nicegui import events, ui
from nicegui.run import io_bound

from cursustrace import db, scraper
from cursustrace.errors import ScrapeError
from cursustrace.web.attention_ui import _similar_summary
from cursustrace.web.session import current_user_id
from cursustrace.web.state import _similar_notice_enabled

logger = logging.getLogger(__name__)


def _parse_urls(text: str | None) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines()]
    return list(dict.fromkeys(line for line in lines if line))


def _scan_url(url: str, user_id: int | None) -> tuple[str, str, list[db.Job]]:
    try:
        job = scraper.scrape_job(url)
    except ScrapeError as exc:
        logger.error("Scrape failed for %s: %s", url, exc)
        return ("error", f"{url}: {exc}", [])

    if db.check_duplicate(url, user_id=user_id):
        return ("duplicate", url, [])
    job_id = db.add_job(
        url,
        job["title"],
        job["company"],
        job["location"],
        job["description"],
        deadline=job["deadline"],
        user_id=user_id,
    )
    if job_id is None:
        return ("duplicate", url, [])
    matches = db.find_similar(
        job["company"], job["title"], job["description"], exclude_id=job_id, user_id=user_id
    )
    return ("saved", url, matches)


def _scan_summary(saved: int, duplicates: int, errors: int, failures: list[str]) -> str:
    parts: list[str] = []
    if saved:
        parts.append(f"Saved {saved}")
    if duplicates:
        parts.append(f"Duplicates {duplicates}")
    if errors:
        parts.append(f"Failed {errors}: {'; '.join(failures)}")
    return " · ".join(parts) if parts else "Nothing saved."


async def run_scan(
    urls: list[str],
    refresh: Callable[[], None],
    status_label: ui.label,
    button: ui.button,
) -> list[str]:
    """Scrape and save each URL; return the URLs that failed."""
    if not urls:
        return []

    button.enabled = False
    saved = duplicates = errors = 0
    failures: list[str] = []
    failed_urls: list[str] = []
    similar: list[list[db.Job]] = []
    user_id = current_user_id()
    try:
        for index, url in enumerate(urls, start=1):
            status_label.set_text(f"Scanning {index}/{len(urls)}: {url}")
            matches: list[db.Job]
            outcome = await io_bound(_scan_url, url, user_id)
            if outcome is None:
                kind, message, matches = "error", f"{url}: cancelled", []
            else:
                kind, message, matches = outcome
            if kind == "saved":
                saved += 1
                if matches:
                    similar.append(matches)
            elif kind == "duplicate":
                duplicates += 1
            else:
                errors += 1
                failures.append(message)
                failed_urls.append(url)
    finally:
        status_label.set_text("")
        button.enabled = True

    refresh()
    notify_type: Literal["positive", "negative", "warning"] = (
        "negative" if errors else ("warning" if duplicates else "positive")
    )
    ui.notify(
        _scan_summary(saved, duplicates, errors, failures),
        type=notify_type,
    )
    if similar and _similar_notice_enabled():
        flat = [job for matches in similar for job in matches]
        ui.notify(
            f"Added {saved} — {len(similar)} look similar: {_similar_summary(flat)}",
            type="info",
        )
    return failed_urls


async def _handle_scan(
    urls_input: ui.textarea,
    refresh: Callable[[], None],
    status_label: ui.label,
    button: ui.button,
) -> None:
    urls = _parse_urls(urls_input.value)
    if not urls:
        ui.notify("Please enter at least one job URL.", type="warning")
        return

    failed_urls = await run_scan(urls, refresh, status_label, button)
    urls_input.value = "\n".join(failed_urls) if failed_urls else ""


def _add_job_dialog(refresh: Callable[[], None], add_dialog: ui.dialog) -> ui.dialog:
    """Dialog with the ways to add a position: scan listing URLs or enter details."""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
        dialog.mark("add-job-dialog")

        def open_manual() -> None:
            dialog.close()
            add_dialog.open()

        def reset() -> None:
            """Clear the URL box and the status line when the dialog opens."""
            urls_input.value = ""
            status_label.set_text("")

        def on_dialog_value_change(event: events.ValueChangeEventArguments[bool]) -> None:
            if event.value:
                reset()

        ui.label("Add positions").classes("text-h6")
        ui.label("Scan listing URLs, or enter the details yourself.").classes("text-caption")
        urls_input = (
            ui.textarea(
                "Job URLs (one per line)",
                placeholder="https://example.com/job/1\nhttps://example.com/job/2",
            )
            .classes("w-full")
            .props('autogrow input-style="min-height: 80px"')
            .mark("scan-urls")
        )
        with ui.row().classes("w-full items-center gap-3"):
            scan_button = ui.button("Scan & Save Positions")
            status_label = ui.label()
        scan_button.on_click(lambda: _handle_scan(urls_input, refresh, status_label, scan_button))
        ui.separator()
        ui.button("➕ Add Manually", on_click=open_manual).props("outline color=primary").mark(
            "add-manually"
        )
        dialog.on_value_change(on_dialog_value_change)
    return dialog
