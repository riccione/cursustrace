"""Tests for the Markdown CV to PDF exporter."""

from __future__ import annotations

from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename


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


def test_generate_cv_pdf_returns_pdf_bytes() -> None:
    cv_md = "# Jane Doe\n\n## Skills\n\n- Python\n\n| A | B |\n|---|---|\n| 1 | 2 |"

    data = generate_cv_pdf("Jane Doe", cv_md)

    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_generate_cv_pdf_empty_markdown() -> None:
    assert generate_cv_pdf("", "").startswith(b"%PDF")
