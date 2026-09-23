"""Tests for shared job-field validation."""

from __future__ import annotations

from cursustrace.validation import required_error, validate_required, validate_url


def test_validate_url_accepts_http_urls() -> None:
    assert validate_url("https://example.com/job/1") is None
    assert validate_url("http://example.com") is None


def test_validate_url_rejects_missing_or_partial() -> None:
    assert validate_url(None) == "Job URL is required."
    assert validate_url("   ") == "Job URL is required."
    assert validate_url("example.com/job") is not None


def test_required_error() -> None:
    assert required_error("Title", " ") == "Title is required."
    assert required_error("Title", "Engineer") is None


def test_validate_required_callable() -> None:
    check = validate_required("Company")

    assert check(None) == "Company is required."
    assert check("Acme") is None
