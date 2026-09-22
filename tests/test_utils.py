"""Tests for URL and fingerprint normalization helpers."""

from __future__ import annotations

from cursustrace.utils import clean_url, generate_fingerprint


def test_clean_url_strips_query_and_fragment() -> None:
    cleaned = clean_url("https://example.com/jobs/1?utm_source=x&ref=y#section")
    assert cleaned == "https://example.com/jobs/1"


def test_clean_url_lowercases_scheme_and_host() -> None:
    assert clean_url("HTTPS://EXAMPLE.COM/Path") == "https://example.com/Path"


def test_clean_url_strips_trailing_slash() -> None:
    assert clean_url("https://example.com/jobs/") == "https://example.com/jobs"


def test_clean_url_is_idempotent() -> None:
    once = clean_url("https://example.com/jobs/1?utm=1")
    assert clean_url(once) == once


def test_generate_fingerprint_is_deterministic_and_case_insensitive() -> None:
    first = generate_fingerprint("Acme Corp", "Senior Engineer", "Remote")
    second = generate_fingerprint("acme corp", "senior engineer", "remote")
    assert first == second
    assert first == generate_fingerprint("ACME-CORP", "Senior_Engineer", "Remote")


def test_generate_fingerprint_ignores_non_alphanumeric() -> None:
    assert generate_fingerprint("Acme, Inc.", "Dev-Ops", "New York") == generate_fingerprint(
        "AcmeInc", "DevOps", "NewYork"
    )


def test_generate_fingerprint_handles_missing_fields() -> None:
    assert generate_fingerprint(None, None, None) == generate_fingerprint("", "", "")


def test_generate_fingerprint_differs_on_fields() -> None:
    assert generate_fingerprint("Acme", "Engineer", "Remote") != generate_fingerprint(
        "Acme", "Engineer", "Berlin"
    )
