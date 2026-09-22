"""Tests for the Markdown CV to PDF exporter."""

from __future__ import annotations

from cursustrace.db import Profile
from cursustrace.pdf_exporter import build_header_markdown, generate_cv_pdf, get_pdf_filename


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
