"""Render a Markdown CV into a downloadable PDF document."""

from __future__ import annotations

import html
import io
import re

import markdown
from xhtml2pdf import pisa

from cursustrace.errors import PdfExportError

_CV_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        @page {{
            size: letter portrait;
            margin: 2cm;
        }}
        body {{
            font-family: 'Helvetica', 'Arial', sans-serif;
            font-size: 10pt;
            line-height: 1.4;
            color: #222222;
        }}
        h1 {{
            font-size: 20pt;
            border-bottom: 2px solid #333333;
            padding-bottom: 4px;
            margin-top: 0;
        }}
        h2 {{
            font-size: 14pt;
            border-bottom: 1px solid #cccccc;
            padding-bottom: 2px;
            margin-top: 14px;
            margin-bottom: 6px;
            color: #1a365d;
        }}
        h3 {{
            font-size: 11pt;
            margin-top: 10px;
            margin-bottom: 2px;
        }}
        p, li {{
            margin-bottom: 4px;
        }}
        ul {{
            margin-top: 2px;
            padding-left: 20px;
        }}
        a {{
            color: #2563eb;
            text-decoration: none;
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


def generate_cv_pdf(full_name: str, cv_md: str) -> bytes:
    """Convert Markdown text into a styled PDF binary buffer."""
    html_content = markdown.markdown(cv_md, extensions=["extra", "tables"])
    full_html = _CV_TEMPLATE.format(title=html.escape(full_name), content=html_content)

    pdf_buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(full_html), dest=pdf_buffer)
    if result.err:
        raise PdfExportError("xhtml2pdf could not render the CV")
    return pdf_buffer.getvalue()
