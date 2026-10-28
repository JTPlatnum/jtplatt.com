"""Lane assignment + location classification. See SPEC.md "Lanes".

Shared by filter.py (sneaker-lane pay floor, authentication rule, location
rule) and score.py (lane-fit + location components). Pure functions: Posting
in, plain data out. Lane keywords come from inventory.LANE_PREFERENCES;
nothing here reads inventory.SKILLS.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from crawler.base import Posting
from data import inventory

_PREFS = inventory.LANE_PREFERENCES
LANES: List[Dict[str, Any]] = _PREFS["lanes"]
LANE_BY_ID: Dict[int, Dict[str, Any]] = {lane["id"]: lane for lane in LANES}
STRONG_MONTHLY_USD = _PREFS["pay"]["strong_annual_usd"] / 12


def _phrase_re(phrases: Iterable[str]) -> Optional[re.Pattern]:
    """Word-bounded, case-insensitive alternation. Longest phrase first so
    'supply chain' wins over a shorter overlapping entry."""
    items = sorted({p for p in phrases if p}, key=len, reverse=True)
    if not items:
        return None
    return re.compile(
        r"\b(" + "|".join(re.escape(p) for p in items) + r")\b", re.IGNORECASE,
    )


def _hits(pattern: Optional[re.Pattern], *texts: Optional[str]) -> List[str]:
    if pattern is None:
        return []
    found = set()
    for t in texts:
        if t:
            found.update(m.group(1).lower() for m in pattern.finditer(t))
    return sorted(found)


# --- Location classification ----------------------------------------------

_US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming",
    "Puerto Rico", "Guam", "American Samoa", "U.S. Virgin Islands",
    "Northern Mariana Islands",
    "United States", "USA", "US", "U.S.",
]
# Two-letter abbreviations: "Sacramento, CA" must not read as overseas.
_US_ABBREVS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR", "GU", "AS", "VI", "MP",
]
_US_SUFFIXES = frozenset(s.lower() for s in _US_STATES + _US_ABBREVS)

REGION_COUNTRIES: Dict[str, List[str]] = {
    "southeast_asia": [
        "Vietnam", "Viet Nam", "Thailand", "Indonesia", "Malaysia",
        "Singapore", "Philippines", "Cambodia", "Laos", "Myanmar", "Brunei",
        "Timor-Leste",
    ],
    "europe": [
        "Albania", "Andorra", "Austria", "Belgium", "Bosnia and Herzegovina",
        "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Czechia",
        "Denmark", "Estonia", "Finland", "France", "Germany", "Greece",
        "Hungary", "Iceland", "Ireland", "Italy", "Kosovo", "Latvia",
        "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova",
        "Monaco", "Montenegro", "Netherlands", "North Macedonia", "Norway",
        "Poland", "Portugal", "Romania", "San Marino", "Serbia", "Slovakia",
        "Slovenia", "Spain", "Sweden", "Switzerland", "Ukraine",
        "United Kingdom", "UK", "England", "Scotland", "Wales",
        "Northern Ireland",
    ],
}
_COUNTRY_TO_REGION = {
    c.lower(): region for region, cs in REGION_COUNTRIES.items() for c in cs
}

_HOME = [s.lower() for s in inventory.PREFERENCES["target_locations"]]
_PREMIUM_CITIES = frozenset(
    s.lower() for s in inventory.PREFERENCES["premium_metro_cities"]
)
_REMOTE_LOC_RE = re.compile(r"\b(remote|anywhere|virtual)\b", re.IGNORECASE)


def location_strings(posting: Posting) -> List[str]:
    if posting.all_locations:
        return [loc for loc in posting.all_locations if loc]
    return [posting.location] if posting.location else []


def classify_location(loc: str) -> Dict[str, Any]:
    """One location string -> {"kind", "region"}.

    kind: "remote" | "home" | "premium_metro" | "international" | "other"
    region: "southeast_asia" | "europe" | "other" for international, else None
    """
    low = loc.lower().strip()
    if _REMOTE_LOC_RE.search(low):
        return {"kind": "remote", "region": None}
    if any(h in low for h in _HOME):
        return {"kind": "home", "region": None}

    parts = [p.strip() for p in low.split(",")]
    city, suffix = parts[0], parts[-1]
    if city in _PREMIUM_CITIES and (len(parts) == 1 or suffix in _US_SUFFIXES):
        return {"kind": "premium_metro", "region": None}

    # Bare country ("Germany") or "City, Country".
    region = _COUNTRY_TO_REGION.get(suffix)
    if region:
        return {"kind": "international", "region": region}
    if len(parts) > 1 and suffix and suffix not in _US_SUFFIXES:
        return {"kind": "international", "region": "other"}
    return {"kind": "other", "region": None}


def classified_locations(posting: Posting) -> List[Dict[str, Any]]:
    return [dict(classify_location(loc), location=loc) for loc in location_strings(posting)]


def overseas_region(posting: Posting) -> Optional[str]:
    """Best overseas region across all locations (priority regions first),
    or None if the posting has no non-US location."""
    regions = {c["region"] for c in classified_locations(posting) if c["kind"] == "international"}
    for r in ("southeast_asia", "europe", "other"):
        if r in regions:
            return r
    return None


# --- Remote vs hybrid ------------------------------------------------------

_FULL_REMOTE_RE = re.compile(
    r"\b(fully[- ]remote|100% remote|remote[- ]first|remote only|"
    r"work from anywhere|anywhere in the u\.?s\.?|remote job|"
    r"remote position|remote role)\b|\(remote job\)",
    re.IGNORECASE,
)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTEISH_RE = re.compile(r"\b(telework|remote|flexible)\b", re.IGNORECASE)


def remote_type(posting: Posting) -> Optional[str]:
    """"full" | "hybrid" | None.

    Explicit posting.remote_type wins. Otherwise: a location string that is
    itself remote -> full; the word "hybrid" -> hybrid; a full-remote phrase
    -> full; telework_flag or a bare remote/telework mention -> hybrid
    (conservative: CA state "telework" is usually hybrid)."""
    if posting.remote_type in ("full", "hybrid"):
        return posting.remote_type
    if any(c["kind"] == "remote" for c in classified_locations(posting)):
        return "full"
    text = posting.raw_text or ""
    if _HYBRID_RE.search(text):
        return "hybrid"
    if _FULL_REMOTE_RE.search(text):
        return "full"
    if posting.telework_flag or _REMOTEISH_RE.search(text):
        return "hybrid"
    return None


# --- Lane assignment -------------------------------------------------------

_LANE_RES = {
    lane["id"]: {
        "domain": _phrase_re(lane.get("domain_keywords", [])),
        "title": _phrase_re(lane.get("title_keywords", [])),
        "text": _phrase_re(lane.get("text_keywords", [])),
    }
    for lane in LANES
}
_AUTH = _PREFS["authentication"]
_AUTH_TITLE_RE = _phrase_re(_AUTH["title_keywords"])
_AUTH_SUPER_RE = _phrase_re(_AUTH["supervisory_keywords"])
_AUTH_MIN_MONTHLY = _AUTH["min_annual_usd"] / 12
_LANE5_LOCS = [s.lower() for s in LANE_BY_ID[5]["locations"]]


def _title_fields(posting: Posting) -> Tuple[Optional[str], Optional[str]]:
    return posting.title, posting.classification


def in_sneaker_domain(posting: Posting) -> bool:
    return bool(_hits(_LANE_RES[1]["domain"], posting.title, posting.employer, posting.raw_text))


def assign_lane(posting: Posting) -> Dict[str, Any]:
    """Walk lanes in priority order; first rule that fires wins.

    Returns {"lane": int|None, "name": str|None, "reason": str,
             "overseas_region": str|None, "remote_type": str|None}.
    overseas_region is reported for every lane (overseas applies across
    lanes), not only lane 2.
    """
    region = overseas_region(posting)
    rtype = remote_type(posting)
    title, classification = _title_fields(posting)

    def out(lane_id: Optional[int], reason: str) -> Dict[str, Any]:
        return {
            "lane": lane_id,
            "name": LANE_BY_ID[lane_id]["name"] if lane_id else None,
            "reason": reason,
            "overseas_region": region,
            "remote_type": rtype,
        }

    # 1. Sneakers: domain anywhere AND function keyword in title.
    domain = _hits(_LANE_RES[1]["domain"], posting.title, posting.employer, posting.raw_text)
    function = _hits(_LANE_RES[1]["title"], title, classification)
    if domain and function:
        return out(1, f"domain {domain[0]!r} + title {function[0]!r}")

    # 2. Overseas: any non-US location.
    if region:
        return out(2, f"overseas location ({region})")

    # 3. Remote knowledge work: fully remote AND knowledge-work title.
    if rtype == "full":
        k = _hits(_LANE_RES[3]["title"], title, classification)
        if k:
            return out(3, f"fully remote + title {k[0]!r}")

    # 4. Education: education title keyword.
    k = _hits(_LANE_RES[4]["title"], title, classification)
    if k:
        return out(4, f"title {k[0]!r}")

    # 5. Sacramento state IT step-up: step-up title AND Sacramento ring.
    k = _hits(_LANE_RES[5]["title"], title, classification)
    if k and any(any(s in loc.lower() for s in _LANE5_LOCS) for loc in location_strings(posting)):
        return out(5, f"Sacramento + title {k[0]!r}")

    return out(None, "no lane rule fired")


def lane_text_hits(posting: Posting, lane_id: Optional[int]) -> List[str]:
    """Lane keyword hits for scoring: title keywords (title + classification
    only; "IT" would match "it" in any body text) plus text keywords and
    domain keywords across the posting."""
    if lane_id is None:
        return []
    res = _LANE_RES[lane_id]
    return sorted(set(
        _hits(res["title"], posting.title, posting.classification)
        + _hits(res["text"], posting.title, posting.raw_text)
        + _hits(res["domain"], posting.title, posting.employer, posting.raw_text)
    ))


# --- Authentication rule ---------------------------------------------------

def is_authentication_role(posting: Posting) -> bool:
    """Sneaker-domain authentication/authenticator title. IT identity roles
    ("Authentication Engineer" at a bank) are NOT in scope."""
    return bool(_hits(_AUTH_TITLE_RE, posting.title)) and in_sneaker_domain(posting)


def authentication_qualifies(posting: Posting) -> bool:
    """(fully remote OR supervisory title) AND verified pay >= $100k/yr."""
    remote_ok = remote_type(posting) == "full"
    super_ok = bool(_hits(_AUTH_SUPER_RE, posting.title))
    pay_ok = posting.salary_min is not None and posting.salary_min >= _AUTH_MIN_MONTHLY
    return (remote_ok or super_ok) and pay_ok
