"""Render a Markdown CV into a downloadable PDF document."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import markdown
from xhtml2pdf import pisa

from cursustrace.errors import PdfExportError

if TYPE_CHECKING:
    from cursustrace.db import Profile

_CV_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @page {{
            size: letter portrait;
            margin: 1.8cm;
        }}
        body {{
            font-family: 'Helvetica', 'Arial', sans-serif;
            font-size: 10pt;
            line-height: 1.4;
            color: #222222;
        }}
        h1 {{
            font-size: 22pt;
            margin-bottom: 4px;
            color: #111827;
        }}
        h2 {{
            font-size: 13pt;
            border-bottom: 1px solid #d1d5db;
            padding-bottom: 2px;
            margin-top: 14px;
            margin-bottom: 6px;
            color: #1f2937;
        }}
        p, li {{
            margin-bottom: 4px;
        }}
        ul {{
            margin-top: 2px;
            padding-left: 18px;
        }}
        a {{
            color: #2563eb;
            text-decoration: none;
        }}
        hr {{
            border: none;
            border-top: 1.5px solid #374151;
            margin-top: 8px;
            margin-bottom: 12px;
        }}
    </style>
</head>
<body>
    {content}
</body>
</html>
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


def generate_cv_pdf(profile: Profile) -> bytes:
    """Render the profile header and Markdown CV body into a PDF document."""
    combined_md = f"{build_header_markdown(profile)}{profile.get('cv_markdown', '').strip()}"
    html_content = markdown.markdown(combined_md, extensions=["extra", "tables"])
    full_html = _CV_TEMPLATE.format(content=html_content)

    pdf_buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(full_html), dest=pdf_buffer)
    if result.err:
        raise PdfExportError("xhtml2pdf could not render the CV")
    return pdf_buffer.getvalue()
