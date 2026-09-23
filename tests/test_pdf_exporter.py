"""Tests for the Markdown CV to PDF exporter."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from cursustrace.db import Profile
from cursustrace.errors import PdfExportError
from cursustrace.pdf_exporter import (
    build_header_markdown,
    build_html,
    generate_cv_pdf,
    get_pdf_filename,
    load_cv_styles,
)


def _page_footers(pdf: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf))
    footers: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\n", " ")
        match = re.search(r"Page\s+\d+\s+of\s+\d+", text)
        footers.append(match.group(0) if match else "")
    return footers


def _profile(
    full_name: str = "Jane Doe",
    location: str = "Remote",
    phone: str = "555-0100",
    email: str = "jane@example.com",
    linkedin_url: str = "https://linkedin.com/in/jane",
    github_url: str = "https://github.com/jane",
    cv_markdown: str = "# Jane\n\nEngineer",
) -> Profile:
    return {
        "full_name": full_name,
        "location": location,
        "phone": phone,
        "email": email,
        "linkedin_url": linkedin_url,
        "github_url": github_url,
        "cv_markdown": cv_markdown,
        "date_updated": None,
    }


def test_filename_uses_first_and_last_name() -> None:
    assert get_pdf_filename("John Doe") == "CV_John_Doe.pdf"


def test_filename_single_name() -> None:
    assert get_pdf_filename("Madonna") == "CV_Madonna.pdf"


def test_filename_blank_falls_back() -> None:
    assert get_pdf_filename("") == "CV_Candidate.pdf"
    assert get_pdf_filename("   ") == "CV_Candidate.pdf"


def test_filename_strips_punctuation() -> None:
    assert get_pdf_filename("Jean-Luc O'Brien") == "CV_JeanLuc_OBrien.pdf"


def test_filename_uses_last_token_as_surname() -> None:
    assert get_pdf_filename("John Ronald Reuel Tolkien") == "CV_John_Tolkien.pdf"


def test_build_header_joins_present_contacts() -> None:
    header = build_header_markdown(_profile())

    expected_contacts = (
        "**Remote** | 555-0100 | [jane@example.com](mailto:jane@example.com) | "
        "[https://linkedin.com/in/jane](https://linkedin.com/in/jane) | "
        "[https://github.com/jane](https://github.com/jane)"
    )
    assert header == f"# Jane Doe\n\n{expected_contacts}\n\n---\n\n"


def test_build_header_omits_empty_contacts() -> None:
    header = build_header_markdown(
        _profile(location="", phone="", email="", linkedin_url="", github_url="")
    )

    assert header == "# Jane Doe\n\n\n\n---\n\n"


def test_build_header_defaults_empty_name() -> None:
    header = build_header_markdown(_profile(full_name="   "))

    assert header.startswith("# Candidate Name\n\n")


def test_generate_cv_pdf_returns_pdf_bytes() -> None:
    data = _profile(
        cv_markdown="# Jane Doe\n\n## Skills\n\n- Python\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    )

    pdf = generate_cv_pdf(data)

    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


def test_generate_cv_pdf_handles_empty_profile() -> None:
    empty = _profile(
        full_name="",
        location="",
        phone="",
        email="",
        linkedin_url="",
        github_url="",
        cv_markdown="",
    )

    assert generate_cv_pdf(empty).startswith(b"%PDF")


def test_generate_cv_pdf_numbers_single_page() -> None:
    assert _page_footers(generate_cv_pdf(_profile())) == ["Page 1 of 1"]


def test_generate_cv_pdf_numbers_every_page() -> None:
    long_cv = "\n\n".join(f"Line {index}" for index in range(200))

    footers = _page_footers(generate_cv_pdf(_profile(cv_markdown=long_cv)))

    assert len(footers) > 1
    assert footers[0] == f"Page 1 of {len(footers)}"
    assert footers[-1] == f"Page {len(footers)} of {len(footers)}"


def test_build_html_renders_markdown_tables() -> None:
    html = build_html("| A | B |\n|---|---|\n| 1 | 2 |")

    assert "<table>" in html
    assert "<td>1</td>" in html


def test_build_html_applies_print_styles() -> None:
    html = build_html("# Jane Doe\n\nRemote | 555-0100")

    assert "@bottom-right" in html
    assert 'content: "Page " counter(page) " of " counter(pages);' in html
    assert "<h1>Jane Doe</h1>" in html


def test_load_cv_styles_reads_stylesheet() -> None:
    assert "@bottom-right" in load_cv_styles()


def test_load_cv_styles_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PdfExportError, match="stylesheet not found"):
        load_cv_styles(tmp_path / "does-not-exist.css")


def test_generate_cv_pdf_accepts_custom_styles() -> None:
    pdf = generate_cv_pdf(_profile(), css="body { font-size: 30pt; }")

    assert pdf.startswith(b"%PDF")


def test_generate_cv_pdf_wraps_render_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> object:
        raise ValueError("kaboom")

    monkeypatch.setattr("cursustrace.pdf_exporter.HTML", boom)

    with pytest.raises(PdfExportError, match="kaboom") as excinfo:
        generate_cv_pdf(_profile())
    assert isinstance(excinfo.value.__cause__, ValueError)
