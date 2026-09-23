"""Render a Markdown CV into a downloadable PDF document."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

from markdown_it import MarkdownIt
from weasyprint import HTML

from cursustrace.errors import PdfExportError

if TYPE_CHECKING:
    from cursustrace.db import Profile

CV_STYLE_PATH = Path("styles/cv.css")


def load_cv_styles(path: Path | None = None) -> str:
    """Read the customizable CV stylesheet; raise PdfExportError when it is missing."""
    stylesheet = path if path is not None else CV_STYLE_PATH
    try:
        return stylesheet.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PdfExportError(f"CV stylesheet not found at {stylesheet}") from exc


def get_pdf_filename(full_name: str) -> str:
    """Build a CV_FirstName_LastName.pdf filename from a full name."""
    if not full_name or not full_name.strip():
        return "CV_Candidate.pdf"

    clean_name = re.sub(r"[^a-zA-Z0-9\s]", "", full_name.strip())
    parts = clean_name.split()
    if not parts:
        return "CV_Candidate.pdf"
    if len(parts) == 1:
        return f"CV_{parts[0]}.pdf"

    first_name, last_name = parts[0], parts[-1]
    return f"CV_{first_name}_{last_name}.pdf"


def build_header_markdown(profile: Profile) -> str:
    """Build the contact header Markdown block from profile metadata."""
    full_name = profile.get("full_name", "").strip() or "Candidate Name"

    contact_items: list[str] = []
    location = profile.get("location", "").strip()
    if location:
        contact_items.append(f"**{location}**")
    phone = profile.get("phone", "").strip()
    if phone:
        contact_items.append(phone)
    email = profile.get("email", "").strip()
    if email:
        contact_items.append(f"[{email}](mailto:{email})")
    linkedin = profile.get("linkedin_url", "").strip()
    if linkedin:
        contact_items.append(f"[{linkedin}]({linkedin})")
    github = profile.get("github_url", "").strip()
    if github:
        contact_items.append(f"[{github}]({github})")

    contact_line = " | ".join(contact_items)
    return f"# {full_name}\n\n{contact_line}\n\n---\n\n"


def build_html(markdown_text: str, css: str | None = None) -> str:
    """Render Markdown into a print-styled HTML document."""
    styles = css if css is not None else load_cv_styles()
    body_html = MarkdownIt("commonmark").enable("table").render(markdown_text)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<style>\n{styles}</style>\n</head>\n<body>\n{body_html}\n</body>\n</html>"
    )


def generate_cv_pdf(profile: Profile, css: str | None = None) -> bytes:
    """Render the profile header and Markdown CV body into a PDF document."""
    combined_md = f"{build_header_markdown(profile)}{profile.get('cv_markdown', '').strip()}"
    try:
        pdf = HTML(string=build_html(combined_md, css)).write_pdf()
    except Exception as exc:
        # WeasyPrint exposes no common error base, so catch broadly but keep the cause.
        raise PdfExportError(f"WeasyPrint could not render the CV: {exc}") from exc
    return cast("bytes", pdf)
