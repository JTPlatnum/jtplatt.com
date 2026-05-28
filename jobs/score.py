"""Tier 1 rule-based scoring. See SPEC.md "Soft Scoring -> Tier 1".

Pure function: Posting in, score dict out. No I/O, no DB, no scraping.

Weights (sum to 100 at the cap; title can subtract 25):

    inventory keyword match   40    distinct \\b-bounded matches of inventory
                                    skill keywords, scaled min(40, n*4)
    title pattern match       25    +25 if posting.title matches any YES title,
                                    -25 if it matches any NO title (NO wins).
                                    YES/NO come from inventory.PREFERENCES.
    flexibility bonus         10    telework_flag OR raw_text mentions any of
                                    {telework, remote, hybrid, flexible}
    person-facing bonus       10    raw_text mentions any of {training,
                                    instruction, teaching, liaison,
                                    requirements, consulting, facilitation}
    international bonus       10    all_locations has a non-US suffix OR
                                    raw_text mentions {overseas, international,
                                    Foreign Service, DODEA}
    recency bonus              5    posted_date within 7 days

Final score is clamped to [0, 100].

title_patterns.yaml is intentionally NOT loaded. inventory.PREFERENCES is the
single source of truth for YES/NO titles; duplicating into a yaml would drift.
The stub file remains in data/ for future granular patterns that don't fit
inventory (regex bias tokens, etc.); none exist today.
"""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from crawler.base import Posting
from data import inventory

# --- Keyword preprocessing --------------------------------------------------

# Strip anything after "(" and lowercase. E.g.
#   "Azure (website publishing/deployment)" -> "azure"
# This is deliberate scoping: inventory entries can carry a human-readable
# qualifier that doesn't participate in matching. Tier 2 reads the full
# qualified entry and applies nuance.
def _match_key(skill: str) -> str:
    return skill.split("(", 1)[0].strip().lower()


def _build_keyword_patterns() -> List[Tuple[str, re.Pattern]]:
    seen = set()
    out: List[Tuple[str, re.Pattern]] = []
    for skill in inventory.all_skill_keywords():
        key = _match_key(skill)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE)))
    return out


_KEYWORD_PATTERNS = _build_keyword_patterns()


# --- Title preprocessing ----------------------------------------------------

def _bare_title(t: str) -> str:
    return t.split("(", 1)[0].strip().lower()


def _build_title_patterns(titles: List[str]) -> List[Tuple[str, re.Pattern]]:
    out: List[Tuple[str, re.Pattern]] = []
    for t in titles:
        bare = _bare_title(t)
        if not bare:
            continue
        out.append((t, re.compile(r"\b" + re.escape(bare) + r"\b", re.IGNORECASE)))
    return out


_YES_TITLES = _build_title_patterns(inventory.PREFERENCES["target_titles_yes"])
_NO_TITLES = _build_title_patterns(inventory.PREFERENCES["target_titles_no"])


# --- Bonus keyword sets -----------------------------------------------------

_FLEX_WORDS = ["telework", "remote", "hybrid", "flexible"]
_PERSON_WORDS = [
    "training", "instruction", "teaching", "liaison",
    "requirements", "consulting", "facilitation",
]
_INTL_WORDS = ["overseas", "international", "foreign service", "dodea"]

_FLEX_RE = re.compile(r"\b(" + "|".join(_FLEX_WORDS) + r")\b", re.IGNORECASE)
_PERSON_RE = re.compile(r"\b(" + "|".join(_PERSON_WORDS) + r")\b", re.IGNORECASE)
_INTL_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _INTL_WORDS) + r")\b",
    re.IGNORECASE,
)


# --- US state/territory suffixes (for non-US location detection) ------------
# Used to detect overseas locations from all_locations strings of the form
# "City, <Suffix>". Anything NOT ending with one of these is treated as
# overseas. Heuristic; the durable fix is an is_overseas field on Posting,
# populated at the source-mapping layer where CountryCode is available.

_US_SUFFIXES = frozenset(s.lower() for s in [
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
])


def _has_overseas_location(all_locations: Optional[List[str]]) -> bool:
    if not all_locations:
        return False
    for loc in all_locations:
        if "," not in loc:
            continue
        suffix = loc.rsplit(",", 1)[1].strip().lower()
        if suffix and suffix not in _US_SUFFIXES:
            return True
    return False


# --- Component scorers ------------------------------------------------------

def _score_keywords(raw_text: str) -> Dict[str, Any]:
    text = raw_text or ""
    matched: List[str] = []
    for key, pat in _KEYWORD_PATTERNS:
        if pat.search(text):
            matched.append(key)
    points = min(40, len(matched) * 4)
    return {"points": points, "matched_keywords": matched}


def _score_title(posting: Posting) -> Dict[str, Any]:
    """Title-pattern match against BOTH posting.title and posting.classification.
    NO wins over YES. A YES that matches both fields counts once (single +25),
    not twice — the function returns on the first hit it finds."""
    candidates = [c for c in (posting.title, posting.classification) if c]
    for original, pat in _NO_TITLES:
        for cand in candidates:
            if pat.search(cand):
                return {"points": -25, "matched": original, "list": "no"}
    for original, pat in _YES_TITLES:
        for cand in candidates:
            if pat.search(cand):
                return {"points": 25, "matched": original, "list": "yes"}
    return {"points": 0, "matched": None, "list": None}


def _score_flexibility(posting: Posting) -> Dict[str, Any]:
    if posting.telework_flag:
        return {"points": 10, "fired_by": "telework_flag"}
    hits = sorted({m.group(1).lower() for m in _FLEX_RE.finditer(posting.raw_text or "")})
    if hits:
        return {"points": 10, "fired_by": ",".join(hits)}
    return {"points": 0, "fired_by": None}


def _score_person_facing(raw_text: str) -> Dict[str, Any]:
    hits = sorted({m.group(1).lower() for m in _PERSON_RE.finditer(raw_text or "")})
    return {"points": 10 if hits else 0, "fired_by": hits}


def _score_international(posting: Posting) -> Dict[str, Any]:
    sources: List[str] = []
    if _has_overseas_location(posting.all_locations):
        sources.append("non-US location")
    hits = sorted({m.group(1).lower() for m in _INTL_RE.finditer(posting.raw_text or "")})
    sources.extend(hits)
    return {"points": 10 if sources else 0, "fired_by": sources}


def _score_recency(posted_date: Optional[date], now: date) -> Dict[str, Any]:
    if posted_date is None:
        return {"points": 0, "age_days": None}
    age = (now - posted_date).days
    return {"points": 5 if 0 <= age <= 7 else 0, "age_days": age}


# --- Main entry -------------------------------------------------------------

def score_posting(posting: Posting, now: Optional[date] = None) -> Dict[str, Any]:
    """Score a posting 0..100 with component breakdown.

    Args:
        posting: a crawler.base.Posting
        now: reference date for recency check; defaults to date.today()

    Returns:
        {"score": int, "components": {...}}

    The components dict keys: keyword_match, title_match, flexibility,
    person_facing, international, recency. Each carries at minimum a
    "points" int plus per-component fields describing what fired.
    """
    now = now or date.today()
    components = {
        "keyword_match": _score_keywords(posting.raw_text),
        "title_match": _score_title(posting),
        "flexibility": _score_flexibility(posting),
        "person_facing": _score_person_facing(posting.raw_text),
        "international": _score_international(posting),
        "recency": _score_recency(posting.posted_date, now),
    }
    total = sum(c["points"] for c in components.values())
    clamped = max(0, min(100, total))
    return {"score": clamped, "components": components}
