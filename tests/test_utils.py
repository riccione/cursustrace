"""Tests for URL and fingerprint normalization helpers."""

from __future__ import annotations

from cursustrace.utils import clean_url, generate_fingerprint, role_key


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
    first = generate_fingerprint("https://example.com/1", "Acme Corp", "Senior Engineer")
    second = generate_fingerprint("https://example.com/1", "acme corp", "senior engineer")
    assert first == second
    assert first == generate_fingerprint("https://example.com/1", "ACME-CORP", "Senior_Engineer")


def test_generate_fingerprint_ignores_url_trackers() -> None:
    plain = generate_fingerprint("https://example.com/1", "Acme", "Engineer")
    tracked = generate_fingerprint("https://example.com/1?utm_source=x", "Acme", "Engineer")
    assert plain == tracked


def test_generate_fingerprint_ignores_non_alphanumeric() -> None:
    assert generate_fingerprint(
        "https://example.com/1", "Acme, Inc.", "Dev-Ops"
    ) == generate_fingerprint("https://example.com/1", "AcmeInc", "DevOps")


def test_generate_fingerprint_handles_missing_fields() -> None:
    assert generate_fingerprint("https://example.com/1", None, None) == generate_fingerprint(
        "https://example.com/1", "", ""
    )


def test_generate_fingerprint_differs_on_url() -> None:
    assert generate_fingerprint("https://example.com/1", "Acme", "Engineer") != (
        generate_fingerprint("https://example.com/2", "Acme", "Engineer")
    )


def test_role_key_is_case_and_punctuation_insensitive() -> None:
    assert role_key("Acme, Inc.", "Dev-Ops") == role_key("acmeinc", "devops")


def test_role_key_differs_by_company_or_title() -> None:
    assert role_key("Acme", "Engineer") != role_key("Globex", "Engineer")
    assert role_key("Acme", "Engineer") != role_key("Acme", "Manager")
