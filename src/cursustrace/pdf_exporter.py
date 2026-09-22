"""Render a Markdown CV into a downloadable PDF document."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from markdown_it import MarkdownIt
from weasyprint import HTML

from cursustrace.errors import PdfExportError

if TYPE_CHECKING:
    from cursustrace.db import Profile

_CV_STYLES = """@page {
    size: letter;
    margin: 1.5cm 1.5cm 1.5cm 1.5cm;
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 9pt;
        color: #666;
    }
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.35;
    color: #222;
}

h1 {
    text-align: center;
    font-size: 16pt;
    font-weight: 700;
    margin: 0 0 2pt 0;
    color: #1a1a1a;
    letter-spacing: 1pt;
}

h1 + p {
    text-align: left;
    font-size: 9pt;
    color: #444;
    margin-bottom: 12pt;
    line-height: 1.5;
}

h1 + p a {
    color: #2563eb;
    text-decoration: none;
}

h2 {
    font-size: 11pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: #1a1a1a;
    border-bottom: 1px solid #dddddd;
    padding-bottom: 2pt;
    margin: 12pt 0 6pt 0;
    page-break-after: avoid;
    break-after: avoid;
}

h3 {
    font-size: 10pt;
    font-weight: 700;
    color: #1a1a1a;
    margin: 8pt 0 2pt 0;
    page-break-after: avoid;
    break-after: avoid;
}

p {
    margin: 0 0 4pt 0;
    text-align: justify;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 4pt 0 6pt 0;
    font-size: 9pt;
}

th, td {
    border: 1px solid #dddddd;
    padding: 2pt 4pt;
    text-align: left;
}

ul {
    margin: 2pt 0 5pt 1.4em;
    padding: 0;
}

li {
    margin-bottom: 1pt;
}

strong {
    font-weight: 700;
}

a {
    color: #2563eb;
    text-decoration: none;
}

hr {
    border: none;
    border-top: 1px solid #ddd;
    margin: 8pt 0;
}

em {
    font-style: italic;
}
"""


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


def build_html(markdown_text: str) -> str:
    """Render Markdown into a print-styled HTML document."""
    body_html = MarkdownIt("commonmark").enable("table").render(markdown_text)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<style>\n{_CV_STYLES}</style>\n</head>\n<body>\n{body_html}\n</body>\n</html>"
    )


def generate_cv_pdf(profile: Profile) -> bytes:
    """Render the profile header and Markdown CV body into a PDF document."""
    combined_md = f"{build_header_markdown(profile)}{profile.get('cv_markdown', '').strip()}"
    try:
        pdf = HTML(string=build_html(combined_md)).write_pdf()
    except Exception as exc:
        raise PdfExportError("WeasyPrint could not render the CV") from exc
    return cast("bytes", pdf)
