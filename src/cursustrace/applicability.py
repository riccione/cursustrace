"""Heuristic applicability flags for listings aimed at a Serbia/remote-EU applicant.

Flags are advisory: the MCP add paths return them instead of ingesting so the
caller can read the listing and retry with force=true. The checks are
deliberately conservative — a false positive costs one extra round-trip, a
false negative would put a Germany-only job in the tracker.
"""

from __future__ import annotations

import re

_PLACE_NAMES: tuple[str, ...] = (
    # Europe (Serbia deliberately absent: home market)
    "germany",
    "france",
    "netherlands",
    "belgium",
    "luxembourg",
    "austria",
    "switzerland",
    "liechtenstein",
    "italy",
    "spain",
    "portugal",
    "greece",
    "poland",
    "czech republic",
    "czechia",
    "slovakia",
    "slovenia",
    "hungary",
    "romania",
    "bulgaria",
    "croatia",
    "denmark",
    "sweden",
    "norway",
    "finland",
    "estonia",
    "latvia",
    "lithuania",
    "ireland",
    "iceland",
    "malta",
    "cyprus",
    "united kingdom",
    "great britain",
    "ukraine",
    "moldova",
    "belarus",
    "russia",
    "turkey",
    # rest of the world
    "united states",
    "usa",
    "canada",
    "mexico",
    "brazil",
    "india",
    "china",
    "japan",
    "singapore",
    "australia",
    "new zealand",
    "united arab emirates",
    "uae",
    "dubai",
    "israel",
    "south africa",
    # common job-location cities
    "berlin",
    "munich",
    "münchen",
    "hamburg",
    "frankfurt",
    "cologne",
    "köln",
    "düsseldorf",
    "stuttgart",
    "leipzig",
    "amsterdam",
    "rotterdam",
    "paris",
    "lyon",
    "london",
    "manchester",
    "edinburgh",
    "dublin",
    "madrid",
    "barcelona",
    "lisbon",
    "porto",
    "rome",
    "milan",
    "warsaw",
    "krakow",
    "prague",
    "vienna",
    "zurich",
    "geneva",
    "basel",
    "copenhagen",
    "stockholm",
    "gothenburg",
    "oslo",
    "helsinki",
    "tallinn",
    "riga",
    "vilnius",
    "bratislava",
    "ljubljana",
    "zagreb",
    "budapest",
    "bucharest",
    "sofia",
    "athens",
    "brussels",
    "new york",
    "san francisco",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "denver",
    "los angeles",
    "toronto",
    "vancouver",
    "montreal",
    "sydney",
    "melbourne",
    "auckland",
    "tokyo",
    "tel aviv",
    "bangalore",
)

# Two-letter country codes appear uppercase in location strings; matching them
# case-insensitively would flag plain English words ("in", "it", "de").
_PLACE_CODES: tuple[str, ...] = (
    "DE",
    "AT",
    "CH",
    "FR",
    "NL",
    "BE",
    "ES",
    "IT",
    "PL",
    "CZ",
    "DK",
    "SE",
    "NO",
    "FI",
    "IE",
    "PT",
    "GR",
    "HU",
    "RO",
    "BG",
    "HR",
    "SK",
    "SI",
    "EE",
    "LV",
    "LT",
    "UA",
    "UK",
    "GB",
    "US",
    "CA",
    "AU",
)

_RESTRICTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:legally\s+)?authori[sz](?:ed|ation)\s+to\s+work\s+in\b[^,.;\n]*", re.IGNORECASE
    ),
    re.compile(r"\bmust\s+(?:be\s+able\s+to\s+)?work\s+in\b[^,.;\n]*", re.IGNORECASE),
    re.compile(
        r"\b(?:no|without|will\s+not|won't|do(?:es)?\s+not)\s+(?:visa\s+)?sponsor(?:ship)?\b"
        r"[^,.;\n]*",
        re.IGNORECASE,
    ),
    re.compile(r"\bmust\s+(?:currently\s+)?(?:reside|live)\s+in\b[^,.;\n]*", re.IGNORECASE),
    re.compile(r"\bmust\s+be\s+(?:a\s+)?(?:resident\s+of|based\s+in)\b[^,.;\n]*", re.IGNORECASE),
    re.compile(
        r"\brequired\s+to\s+(?:be\s+based\s+in|reside\s+in|relocate)\b[^,.;\n]*", re.IGNORECASE
    ),
    re.compile(r"\brelocation\s+(?:is\s+)?required\b[^,.;\n]*", re.IGNORECASE),
    re.compile(r"\brelocation\s+to\b[^,.;\n]*\brequired\b", re.IGNORECASE),
    re.compile(r"\bmust\s+relocate\b[^,.;\n]*", re.IGNORECASE),
)


def _alternation(tokens: tuple[str, ...]) -> str:
    return "|".join(sorted((re.escape(token) for token in tokens), key=len, reverse=True))


_PLACE_NAME_RE = re.compile(rf"\b(?:{_alternation(_PLACE_NAMES)})\b", re.IGNORECASE)
_PLACE_CODE_RE = re.compile(rf"\b(?:{_alternation(_PLACE_CODES)})\b")


def check_applicability(location: str | None, description: str | None) -> list[str]:
    """Return human-readable flags when a listing looks tied to a specific place.

    The location field is scanned for countries and cities other than Serbia;
    the description is scanned for work-authorization and relocation phrasing.
    An empty result means no red flags were found, not a positive endorsement —
    the agent still reads the listing and makes the final call.
    """
    flags: list[str] = []
    if location:
        for place in _PLACE_NAME_RE.finditer(location):
            flags.append(f"location mentions '{place.group(0)}' — not Serbia/EU-remote")
        for code in _PLACE_CODE_RE.finditer(location):
            flags.append(f"location mentions '{code.group(0)}' — not Serbia/EU-remote")
    if description:
        for pattern in _RESTRICTION_PATTERNS:
            phrase = pattern.search(description)
            if phrase is not None:
                flags.append(
                    f"description says '{phrase.group(0).strip()}' — work/residency restriction"
                )
    return list(dict.fromkeys(flags))
