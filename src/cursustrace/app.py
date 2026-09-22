"""Streamlit entry point for cursustrace."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

from cursustrace import db, scraper
from cursustrace.errors import PdfExportError, ScrapeError
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename

APP_TITLE = "CursusTrace — Job Application Tracker"
DETAIL_PARAM = "job"

STATUS_CHECKBOXES: tuple[tuple[db.JobFlag, str], ...] = (
    ("applied", "Mark as Applied"),
    ("interview", "Interview"),
    ("rejected", "Rejected"),
)


@st.cache_data(show_spinner=False)
def _cv_pdf_bytes(profile: db.Profile) -> bytes:
    return generate_cv_pdf(profile)


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


def _on_status_change(job_id: int, status: db.JobFlag) -> None:
    checked = bool(st.session_state.get(f"{status}_{job_id}"))
    db.set_job_status(job_id, status if checked else "unapplied")
    for name, _label in STATUS_CHECKBOXES:
        st.session_state.pop(f"{name}_{job_id}", None)


def _render_job_card(job: db.Job) -> None:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with st.expander(f"{title} — {company}"):
        st.markdown(f"**Location:** {job['location']}")
        st.markdown(f"**Added:** {job['date_added']}")
        st.markdown(f"[Open job posting]({job['job_url']})")
        st.markdown(f"[View full details](?{DETAIL_PARAM}={job['id']})")

        for status, label in STATUS_CHECKBOXES:
            st.checkbox(
                label,
                value=bool(job[status]),
                key=f"{status}_{job['id']}",
                on_change=_on_status_change,
                args=(job["id"], status),
            )


def _render_job_list(jobs: list[db.Job], empty_message: str) -> None:
    if not jobs:
        st.info(empty_message)
        return
    for job in jobs:
        _render_job_card(job)


def _job_status(job: db.Job) -> db.JobStatus:
    if job["interview"]:
        return "interview"
    if job["rejected"]:
        return "rejected"
    if job["applied"]:
        return "applied"
    return "unapplied"


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
    st.markdown(f"**Status:** {_job_status(job).title()}")
    if job["date_applied"]:
        st.markdown(f"**Applied:** {job['date_applied']}")
    if job["date_interview"]:
        st.markdown(f"**Interview:** {job['date_interview']}")
    if job["date_rejected"]:
        st.markdown(f"**Rejected:** {job['date_rejected']}")
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


def _render_profile_editor() -> None:
    st.header("👤 Profile & CV Configuration")
    if st.session_state.pop("profile_saved", False):
        st.success("Profile and CV saved successfully!")
    profile = db.get_profile()

    with st.form("profile_form"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Full Name", value=profile["full_name"])
            email = st.text_input("Email", value=profile["email"])
            linkedin = st.text_input("LinkedIn URL", value=profile["linkedin_url"])
        with col2:
            location = st.text_input("Location", value=profile["location"])
            phone = st.text_input("Phone Number", value=profile["phone"])
            github = st.text_input("GitHub URL", value=profile["github_url"])

        st.subheader("Markdown CV")
        editor_col, preview_col = st.columns([1, 1])
        with editor_col:
            cv_text = st.text_area(
                "Edit your CV in Markdown format:",
                value=profile["cv_markdown"],
                height=450,
            )
        with preview_col:
            st.markdown("**Live preview**")
            st.markdown(cv_text or "_Nothing to preview yet._")

        submitted = st.form_submit_button("💾 Save Profile & CV")
        if submitted:
            db.save_profile(
                {
                    "full_name": name,
                    "location": location,
                    "phone": phone,
                    "email": email,
                    "linkedin_url": linkedin,
                    "github_url": github,
                    "cv_markdown": cv_text,
                    "date_updated": None,
                }
            )
            st.session_state["profile_saved"] = True
            st.rerun()

    st.divider()
    try:
        pdf_bytes = _cv_pdf_bytes(profile)
    except PdfExportError as exc:
        st.error(f"Could not generate PDF: {exc}")
    else:
        st.download_button(
            label="📄 Export to PDF",
            data=pdf_bytes,
            file_name=get_pdf_filename(profile["full_name"]),
            mime="application/pdf",
            type="secondary",
        )


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
    st.set_page_config(page_title=APP_TITLE, page_icon="📋", layout="wide")
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

    dashboard_tab, profile_tab = st.tabs(["📋 Dashboard", "👤 Profile & CV Editor"])
    with dashboard_tab:
        url = st.text_input("Job URL", key="job_url")
        if st.button("Scan & Save Position"):
            _handle_scan(url)

        unapplied_tab, applied_tab, interview_tab, rejected_tab = st.tabs(
            [
                "⏳ Unapplied Positions",
                "✅ Applied Positions",
                "🗣️ Interview Positions",
                "❌ Rejected Positions",
            ]
        )
        with unapplied_tab:
            _render_job_list(db.get_jobs(status="unapplied"), "No unapplied positions yet.")
        with applied_tab:
            _render_job_list(db.get_jobs(status="applied"), "No applied positions yet.")
        with interview_tab:
            _render_job_list(db.get_jobs(status="interview"), "No interview positions yet.")
        with rejected_tab:
            _render_job_list(db.get_jobs(status="rejected"), "No rejected positions yet.")

        _render_settings()

    with profile_tab:
        _render_profile_editor()


if __name__ == "__main__":
    main()
