"""Streamlit entry point for cursustrace."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import streamlit as st

from cursustrace import db, scraper
from cursustrace.errors import ScrapeError

APP_TITLE = "CursusTrace — Job Application Tracker"


def run() -> int:
    """Launch the Streamlit UI in a subprocess and return its exit code."""
    app_path = Path(__file__).resolve()
    completed = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path)],
        check=False,
    )
    return completed.returncode


def _render_job_card(job: db.Job) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with st.expander(f"{title} — {company}"):
        st.markdown(f"**Location:** {job['location']}")
        st.markdown(f"**Added:** {job['date_added']}")
        st.markdown(f"[Open job posting]({job['job_url']})")

        checked = st.checkbox(
            "Mark as Applied",
            value=bool(job["applied"]),
            key=f"applied_{job['id']}",
        )
        if checked != bool(job["applied"]):
            db.update_applied_status(job["id"], checked)
            st.rerun()


def _render_job_list(jobs: list[db.Job], empty_message: str) -> None:
    if not jobs:
        st.info(empty_message)
        return
    for job in jobs:
        _render_job_card(job)


def _handle_scan(url: str) -> None:
    if not url.strip():
        st.warning("Please enter a job URL.")
        return
    try:
        job = scraper.scrape_job(url.strip())
    except ScrapeError as exc:
        st.error(f"Could not scan that URL: {exc}")
        return

    added = db.add_job(
        url.strip(),
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    if added:
        st.success("Position saved.")
    else:
        st.warning("This URL is already tracked.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📋")
    db.init_db()

    st.title(APP_TITLE)

    url = st.text_input("Job URL", key="job_url")
    if st.button("Scan & Save Position"):
        _handle_scan(url)

    unapplied_tab, applied_tab = st.tabs(["⏳ Unapplied Positions", "✅ Applied Positions"])
    with unapplied_tab:
        _render_job_list(db.get_jobs(applied_filter=False), "No unapplied positions yet.")
    with applied_tab:
        _render_job_list(db.get_jobs(applied_filter=True), "No applied positions yet.")


if __name__ == "__main__":
    main()
