"""Tests for the applicability flag heuristics."""

from __future__ import annotations

from cursustrace.applicability import check_applicability


def test_clear_locations_yield_no_flags() -> None:
    for location in (
        None,
        "",
        "Not Specified",
        "Remote",
        "Remote (EU)",
        "Europe",
        "European Union",
        "Worldwide",
        "Anywhere",
        "Belgrade, Serbia",
        "Novi Sad",
        "Remote - EU timezones",
    ):
        assert check_applicability(location, "Build and ship product features.") == []


def test_specific_places_in_location_flag() -> None:
    flags = check_applicability("Berlin, Germany", None)
    assert any("Germany" in flag for flag in flags)
    assert any("Berlin" in flag for flag in flags)


def test_country_codes_in_location_flag() -> None:
    flags = check_applicability("Hamburg, DE", None)
    assert any("'Hamburg'" in flag for flag in flags)
    assert any("'DE'" in flag for flag in flags)


def test_place_wins_over_remote_word() -> None:
    flags = check_applicability("Remote - Germany", None)
    assert any("Germany" in flag for flag in flags)


def test_cities_without_country_flag() -> None:
    assert any("Dublin" in flag for flag in check_applicability("Dublin", None))
    assert any("New York" in flag for flag in check_applicability("New York, US", None))


def test_two_letter_lowercase_words_do_not_flag() -> None:
    # "it"/"in"/"de" are plain English words when lowercase.
    assert check_applicability("Remote working it in de", None) == []


def test_work_authorization_phrases_flag() -> None:
    for phrase in (
        "You must be authorized to work in Germany to be considered.",
        "Candidates need legal authorization to work in the United States.",
        "Applicants must work in France — no exceptions.",
    ):
        flags = check_applicability("Remote", phrase)
        assert any("restriction" in flag for flag in flags), phrase


def test_sponsorship_phrases_flag() -> None:
    for phrase in (
        "We offer no sponsorship for this role.",
        "This position does not sponsor visa holders.",
        "Sorry, we will not sponsor.",
    ):
        flags = check_applicability(None, phrase)
        assert any("restriction" in flag for flag in flags), phrase


def test_residency_and_relocation_phrases_flag() -> None:
    for phrase in (
        "You must reside in the United Kingdom.",
        "Must currently live in Amsterdam.",
        "Must be based in Berlin three days a week.",
        "Relocation to Munich is required.",
        "Candidates must relocate for this role.",
    ):
        flags = check_applicability(None, phrase)
        assert any("restriction" in flag for flag in flags), phrase


def test_friendly_descriptions_stay_clear() -> None:
    for text in (
        "Remote-first team across Europe with quarterly onsites.",
        "We sponsor relocation packages for candidates joining our Belgrade office.",
        "Flexible hours, work from anywhere in EU timezones.",
    ):
        assert check_applicability("Remote (EU)", text) == []


def test_flags_are_deduplicated() -> None:
    flags = check_applicability("Berlin, Germany", "Must work in Germany. Must work in Germany?")
    assert len(flags) == len(set(flags))


def test_location_and_description_both_contribute() -> None:
    flags = check_applicability("Dublin", "authorized to work in Ireland")
    assert any(flag.startswith("location") for flag in flags)
    assert any(flag.startswith("description") for flag in flags)
