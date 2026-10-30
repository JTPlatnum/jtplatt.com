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
    "United States", "United States of America", "USA", "US", "U.S.",
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


# --- Remote job boards -----------------------------------------------------

# Remote-first boards. Their location strings say where the CANDIDATE may
# live, not where an office is, and every posting is fully remote. Keyed by
# source (not Posting.remote_type) because the DB doesn't store remote_type.
REMOTE_BOARD_SOURCES = frozenset({"remotive", "remoteok", "weworkremotely", "himalayas"})

_ANYWHERE_RE = re.compile(
    r"\b(anywhere|worldwide|world|global|globally|international)\b|^\s*remote\s*$",
    re.IGNORECASE,
)
_US_ELIGIBLE_RE = re.compile(
    r"(?<![\w.])(usa|u\.s\.a?\.?|us|united states(?: of america)?|north america|americas)(?!\w)",
    re.IGNORECASE,
)
# Positive non-US markers for remote-board candidate locations: regions plus
# countries outside REGION_COUNTRIES (those are caught by classify_location).
# Kept local to remote boards so other sources' overseas rule is unchanged.
# "Georgia" is left out on purpose (US state).
_NON_US_PLACES = [
    "Europe", "EU", "EMEA", "LATAM", "Latin America", "South America",
    "Central America", "Caribbean", "Asia", "APAC", "Asia Pacific", "Africa",
    "Middle East", "Oceania",
    "Canada", "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru",
    "Ecuador", "Uruguay", "Paraguay", "Bolivia", "Venezuela", "Guatemala",
    "Honduras", "El Salvador", "Nicaragua", "Costa Rica", "Panama",
    "Dominican Republic", "Jamaica", "Trinidad and Tobago", "Bahamas",
    "Barbados", "Belize", "India", "Pakistan", "Bangladesh", "Sri Lanka",
    "Nepal", "China", "Hong Kong", "Taiwan", "Japan", "South Korea", "Korea",
    "Mongolia", "Australia", "New Zealand", "South Africa", "Nigeria",
    "Kenya", "Egypt", "Morocco", "Ghana", "Ethiopia", "Uganda", "Tanzania",
    "Rwanda", "Tunisia", "Algeria", "Senegal", "Cameroon", "Zimbabwe",
    "Zambia", "Botswana", "Namibia", "Mauritius", "Israel", "Turkey",
    "Türkiye", "Saudi Arabia", "United Arab Emirates", "UAE", "Qatar",
    "Kuwait", "Bahrain", "Oman", "Jordan", "Lebanon", "Armenia", "Azerbaijan",
    "Kazakhstan", "Uzbekistan", "Kyrgyzstan", "Belarus", "Russia",
]
_NON_US_RE = _phrase_re(_NON_US_PLACES)

# Country locks JT would consider (Tokyo / Hanoi / Bangkok); lane-2 bonus.
_PREFERRED_LOCK_RE = re.compile(
    r"\b(japan|tokyo|vietnam|viet nam|hanoi|ho chi minh|thailand|bangkok)\b",
    re.IGNORECASE,
)


def _us_location(loc: str) -> bool:
    """US phrase, home region, premium metro, or a US state / "City, ST"."""
    if _US_ELIGIBLE_RE.search(loc):
        return True
    if classify_location(loc)["kind"] in ("home", "premium_metro"):
        return True
    suffix = loc.lower().rstrip(" ,.").rsplit(",", 1)[-1].strip()
    return suffix in _US_SUFFIXES


def _non_us_location(loc: str) -> bool:
    """Positively non-US: a known region/country, or "City, Country"."""
    return bool(_NON_US_RE and _NON_US_RE.search(loc)) or (
        classify_location(loc)["kind"] == "international"
    )


def remote_board_scope(posting: Posting) -> Optional[str]:
    """Candidate-location scope for a remote-board posting:
    "anywhere" (no restriction / worldwide), "us" (US-eligible, possibly
    among others), "overseas" (locked to non-US countries or regions), or
    "unknown" (free text we can't place, e.g. a bare "Redwood City"; not
    treated as overseas). None for every other source."""
    if posting.source not in REMOTE_BOARD_SOURCES:
        return None
    locs = location_strings(posting)
    if not locs or any(_ANYWHERE_RE.search(loc) for loc in locs):
        return "anywhere"
    if any(_us_location(loc) for loc in locs):
        return "us"
    if any(_non_us_location(loc) for loc in locs):
        return "overseas"
    return "unknown"


def preferred_country_lock(posting: Posting) -> bool:
    """Remote-board posting locked to Japan, Vietnam, or Thailand."""
    return remote_board_scope(posting) == "overseas" and any(
        _PREFERRED_LOCK_RE.search(loc) for loc in location_strings(posting)
    )


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
    if posting.source in REMOTE_BOARD_SOURCES:
        return "full"
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
_LANE3 = LANE_BY_ID[3]
_LANE3_EXCLUDE_RE = _phrase_re(_LANE3.get("excluded_title_keywords", []))

# Word variants for lane-3 fit TITLES, so "engineer" catches "Head of
# Engineering" and "marketing" catches "Growth Marketer". A closed suffix
# list, not open prefix matching: words shorter than _MIN_STEM stay exact
# ("IT", "QA", "data"), and a stem never matches bare ("marketing" -> marketer,
# never "market"). Phrases only take a plural. No "-ment": "development" would
# pull in "Management Development". Descriptions stay exact: variants there
# ("auditing", "development", "architects") let the false positives back in.
_MIN_STEM = 5
_VARIANT_SUFFIXES = ("s", "es", "ing", "ed")
_STEM_ENDINGS = ("ing", "er")
_STEM_SUFFIXES = ("er", "ers", "ing")


def _word_variants(keyword: str) -> set:
    out = {keyword}
    if " " in keyword:
        out.add(keyword + "s")
        return out
    if len(keyword) < _MIN_STEM:
        return out
    out.update(keyword + s for s in _VARIANT_SUFFIXES)
    for end in _STEM_ENDINGS:
        stem = keyword[:-len(end)]
        if keyword.endswith(end) and len(stem) >= _MIN_STEM:
            out.update(stem + s for s in _STEM_SUFFIXES)
            break
    return out


def _variants(keywords: Iterable[str]) -> List[str]:
    return [v for k in keywords for v in _word_variants(k)]


_LANE3_FIT_TITLE_RE = _phrase_re(_variants(_LANE3.get("fit_title_keywords", [])))
# Description fit: core + fit keywords, minus words too common in job bodies
# to mean anything there. "IT" would match "it" (matching is case-insensitive);
# the rest appear in almost every description (a quantity surveyor's posting
# mentions "project manager", a support rep's mentions "product"). All of
# these still count in TITLES.
_LANE3_TITLE_ONLY = frozenset({
    "IT", "data", "process", "content",
    "sales", "product", "systems", "technical", "research", "revenue",
    "project manager", "program manager", "operations", "marketing", "analyst",
})
_LANE3_TEXT_FIT_RE = _phrase_re(
    k for k in _LANE3["title_keywords"] + _LANE3.get("fit_title_keywords", [])
    if k not in _LANE3_TITLE_ONLY
)


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

    # 2. Overseas: any non-US location. Remote boards go by candidate
    # location instead: only a recognized non-US lock is overseas; worldwide,
    # US-eligible, or unplaceable roles go on to lane 3.
    scope = remote_board_scope(posting)
    if scope == "overseas":
        return out(2, "remote, locked to " + ", ".join(location_strings(posting)))
    if region and scope is None:
        return out(2, f"overseas location ({region})")

    # 3. Remote knowledge work: fully remote (worldwide / US-eligible; lane 2
    # already took overseas) AND a core knowledge-work title.
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

    # 3 (wider fit gate). Fully remote, not an excluded field (clinical,
    # legal counsel, trades), fit keyword in the title, else in the
    # description. After lanes 4-5 so their title evidence wins.
    if rtype == "full" and not _hits(_LANE3_EXCLUDE_RE, title, classification):
        k = _hits(_LANE3_FIT_TITLE_RE, title, classification)
        if k:
            return out(3, f"fully remote + fit title {k[0]!r}")
        k = _hits(_LANE3_TEXT_FIT_RE, posting.raw_text)
        if k:
            return out(3, f"fully remote + description {k[0]!r}")

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
