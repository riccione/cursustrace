"""Streamlit entry point for cursustrace."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

from cursustrace import db, scraper
from cursustrace.errors import ScrapeError

APP_TITLE = "CursusTrace — Job Application Tracker"
DETAIL_PARAM = "job"


def run() -> None:
    """CLI entrypoint that launches the CursusTrace Streamlit dashboard."""
    app_file = str(Path(__file__).resolve())

    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    cli_args = ["streamlit", "run", app_file, "--browser.gatherUsageStats=false"]

    try:
        os.execvp("streamlit", cli_args)
    except FileNotFoundError:
        import subprocess

        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    app_file,
                    "--browser.gatherUsageStats=false",
                ],
                check=False,
            )
        except KeyboardInterrupt:
            sys.exit(0)


def _render_job_card(job: db.Job) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with st.expander(f"{title} — {company}"):
        st.markdown(f"**Location:** {job['location']}")
        st.markdown(f"**Added:** {job['date_added']}")
        st.markdown(f"[Open job posting]({job['job_url']})")
        st.markdown(f"[View full details](?{DETAIL_PARAM}={job['id']})")

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


def _resolve_job(job_id: int) -> db.Job | None:
    return next((job for job in db.get_jobs() if job["id"] == job_id), None)


def _render_back_button() -> None:
    if st.button("← Back to list"):
        st.query_params.clear()
        st.rerun()


def _render_job_detail(job: db.Job) -> None:
    _render_back_button()

    st.subheader(job["title"] or "Untitled position")
    st.markdown(f"**Company:** {job['company'] or 'Unknown company'}")
    st.markdown(f"**Location:** {job['location'] or 'Not Specified'}")
    st.markdown(f"**Added:** {job['date_added']}")
    applied = f"Yes — {job['date_applied']}" if job["applied"] else "No"
    st.markdown(f"**Applied:** {applied}")
    st.markdown(f"[Open original posting]({job['job_url']})")

    st.divider()
    st.markdown(job["description"] or "_No description captured._")


def _render_settings() -> None:
    with st.sidebar:
        st.header("⚙️ Settings & Maintenance")
        with st.expander("⚠️ Danger Zone: Clear Database"):
            cleared_count = st.session_state.pop("cleared_count", None)
            if cleared_count is not None:
                st.success(f"Successfully cleared {cleared_count} positions.")

            st.warning(
                "⚠️ This action cannot be undone. All tracked job postings and "
                "application history will be permanently erased."
            )
            confirm_text = st.text_input(
                "Type 'DELETE' to confirm:",
                placeholder="DELETE",
                key="clear_confirm",
            )
            if st.button(
                "Confirm & Clear All Data",
                type="primary",
                disabled=(confirm_text != "DELETE"),
            ):
                count = db.clear_all_jobs()
                st.session_state["cleared_count"] = count
                st.rerun()


def _handle_scan(url: str) -> None:
    if not url.strip():
        st.warning("Please enter a job URL.")
        return
    try:
        job = scraper.scrape_job(url.strip())
    except ScrapeError as exc:
        st.error(f"Could not scan that URL: {exc}")
        return

    duplicate, _reason = db.check_duplicate(
        url.strip(),
        job["title"],
        job["company"],
        job["location"],
        job["description"],
    )
    if duplicate:
        st.warning(
            "⚠️ Position already exists in database (Matched by title/company fingerprint)."
        )
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
        st.warning(
            "⚠️ Position already exists in database (Matched by title/company fingerprint)."
        )


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📋")
    db.init_db()

    st.title(APP_TITLE)

    job_id_raw = st.query_params.get(DETAIL_PARAM)
    if job_id_raw is not None:
        job = _resolve_job(int(job_id_raw)) if job_id_raw.isdigit() else None
        if job is not None:
            _render_job_detail(job)
        else:
            st.warning("Position not found.")
            _render_back_button()
        return

    url = st.text_input("Job URL", key="job_url")
    if st.button("Scan & Save Position"):
        _handle_scan(url)

    unapplied_tab, applied_tab = st.tabs(["⏳ Unapplied Positions", "✅ Applied Positions"])
    with unapplied_tab:
        _render_job_list(db.get_jobs(applied_filter=False), "No unapplied positions yet.")
    with applied_tab:
        _render_job_list(db.get_jobs(applied_filter=True), "No applied positions yet.")

    _render_settings()


if __name__ == "__main__":
    main()
