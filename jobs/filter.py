"""Hard filters. See SPEC.md "Hard Filters".

Drops postings that violate baseline rules BEFORE scoring. The scorer (and
JT) only see what survives this stage. Pure function: Posting in,
(passes, reason) out. Single short-circuit return on the first failing rule.

Rules in order — cheap structural checks first, pattern checks last:

    1. salary floor       verified USD-monthly pay only. SALARY_FLOOR env
                          (monthly USD) for every lane; sneaker lane also
                          needs $100k/yr. Unverified pay passes (flagged),
                          except lane 1 with a title-level estimate
                          (estimate.py): estimate_min must clear $100k.
    2. current employer   FI$CAL / FISCal — JT is leaving
    3. source allow-list  structural placeholder; widens as sources land
    4. disqualifying req  data/reject_requirements.yaml substrings
    5. authentication     sneaker-domain authenticator roles need (fully
                          remote OR supervisory) AND verified $100k+/yr
    6. location           home region | fully remote | international with
                          verified pay | SF/NYC at SALARY_FLOOR_PREMIUM_METRO.
                          Lane 1 (sneakers) only: Beaverton/Boston/LA accepted, and
                          international / SF / NYC pass with unverified pay.

Salary is already USD-monthly (or None = unverified) by the time it gets
here: crawler.base.Posting normalizes at construction via pay.py.

Read env vars directly, not by importing dotenv here. Callers that need .env
loaded should call load_dotenv() before importing this module.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

import lanes
from crawler.base import Posting
from pay import pay_verified

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
REJECT_REQUIREMENTS_PATH = REPO_ROOT / "data" / "reject_requirements.yaml"


# --- Config -----------------------------------------------------------------

def _env_float(name: str, default: Optional[str]) -> Optional[float]:
    raw = os.getenv(name, default)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("filter: %s=%r not parseable; treating as unset", name, raw)
        return None


SALARY_FLOOR = _env_float("SALARY_FLOOR", "0") or 0.0

# SF / NYC premium floor, monthly USD. Unset -> SF/NYC postings are rejected
# (the premium can't be checked without a number).
SALARY_FLOOR_PREMIUM_METRO = _env_float("SALARY_FLOOR_PREMIUM_METRO", None)

_LANE1 = lanes.LANE_BY_ID[1]
SNEAKER_FLOOR_ANNUAL = float(_LANE1["pay_floor_annual_usd"])
SNEAKER_FLOOR_MONTHLY = SNEAKER_FLOOR_ANNUAL / 12
_LANE1_EXTRA_LOCATIONS = [s.lower() for s in _LANE1.get("extra_locations", [])]
_LANE1_UNVERIFIED_OK = bool(_LANE1.get("unverified_pay_ok_everywhere"))

# Sources whose postings are eligible for filtering. New scrapers land here as
# they're implemented. Anything else is rejected — keeps stray test postings
# or future-source bleed out of results.
# "edjoin" removed 2026-07-08: source DEACTIVATED (LoadJobs endpoint
# decommissioned, needs rebuild). Dropping it here stops stale EdJoin rows
# already in jobs.db from leaking into results while the source is inactive.
ALLOWED_SOURCES = frozenset({"usajobs", "calcareers", "csu", "nike"})

# Current employer JT is leaving — never surface a posting from FI$CAL.
# Both spellings observed in the wild: "FI$CAL" (official) and "FISCal"
# (informal in some department docs).
_CURRENT_EMPLOYER_RE = re.compile(r"FI\$CAL|FISCal", re.IGNORECASE)


# --- Disqualifying requirements --------------------------------------------

def _load_reject_patterns(path: Path = REJECT_REQUIREMENTS_PATH) -> List[str]:
    if not path.exists():
        log.warning("filter: reject_requirements.yaml not found at %s", path)
        return []
    data = yaml.safe_load(path.read_text()) or []
    if not isinstance(data, list):
        raise ValueError(
            f"{path} must be a YAML list of substring patterns, "
            f"got {type(data).__name__}"
        )
    return [str(p).strip() for p in data if str(p).strip()]


def _compile_reject_pattern(pat: str) -> Tuple[str, re.Pattern]:
    """Wrap with \\b only where it's safe — i.e., when the boundary char is a
    word char. 'PE' becomes \\bPE\\b (avoids matching 'people'); 'TS/SCI'
    becomes \\bTS/SCI\\b (only the alphanumeric ends get \\b)."""
    escaped = re.escape(pat)
    left = r"\b" if pat[0:1].isalnum() else ""
    right = r"\b" if pat[-1:].isalnum() else ""
    return pat, re.compile(left + escaped + right, re.IGNORECASE)


_REJECT_PATTERNS = [_compile_reject_pattern(p) for p in _load_reject_patterns()]


# --- Location rule ----------------------------------------------------------

def _location_check(posting: Posting, lane: Optional[int]) -> Tuple[bool, str]:
    """Pass if ANY location is acceptable. When none is, report the most
    specific reason seen (premium/international) over the generic one.

    Lane 1 is lane-aware: its extra_locations (Beaverton, Boston, Los Angeles)
    are accepted, and
    unverified pay is enough for international / premium metros. A VERIFIED
    lane-1 salary below the premium floor still fails in SF/NYC."""
    classes = lanes.classified_locations(posting)
    if any(c["kind"] == "home" for c in classes):
        return True, ""
    if lanes.remote_type(posting) == "full":
        return True, ""
    if lane == 1 and any(
        extra in c["location"].lower()
        for c in classes for extra in _LANE1_EXTRA_LOCATIONS
    ):
        return True, ""

    verified = pay_verified(posting)
    if lane == 1 and _LANE1_UNVERIFIED_OK and not verified:
        if any(c["kind"] in ("international", "premium_metro") for c in classes):
            return True, ""
    reason = "outside target locations"
    for c in classes:
        if c["kind"] == "premium_metro":
            if SALARY_FLOOR_PREMIUM_METRO is None:
                reason = "premium metro: SALARY_FLOOR_PREMIUM_METRO not set"
            elif not verified:
                reason = "premium metro: pay unverified"
            elif posting.salary_min >= SALARY_FLOOR_PREMIUM_METRO:
                return True, ""
            else:
                reason = "premium metro: below premium floor"
        elif c["kind"] == "international":
            # Verified pay already cleared rule 1 after USD conversion.
            if verified:
                return True, ""
            reason = "international: pay unverified"
    return False, reason


# --- Public API ------------------------------------------------------------

def should_keep(posting: Posting) -> Tuple[bool, str]:
    """Apply hard filters in order. Returns (passes, reason).

    reason is "" when passes; otherwise a short human-readable reject reason
    suitable for log/footer surfacing. Single short-circuit on first
    failing rule.
    """
    # 1. Salary floor — only enforced when pay is verified (USD-monthly
    # known). Unverified pay passes here; the page flags it.
    lane = lanes.assign_lane(posting)["lane"]
    if pay_verified(posting):
        if posting.salary_min < SALARY_FLOOR:
            return False, "below salary floor"
        if lane == 1 and posting.salary_min < SNEAKER_FLOOR_MONTHLY:
            return False, "below sneaker-lane floor"
    elif lane == 1 and posting.estimate_min is not None:
        # No verified pay: the title-level estimate (estimate.py, annual
        # USD) stands in for the sneaker floor only.
        if posting.estimate_min < SNEAKER_FLOOR_ANNUAL:
            return False, "below sneaker-lane floor (estimated)"

    # 2. Current employer.
    if posting.employer and _CURRENT_EMPLOYER_RE.search(posting.employer):
        return False, "current employer"

    # 3. Source allow-list.
    if posting.source not in ALLOWED_SOURCES:
        return False, f"unknown source: {posting.source}"

    # 4. Disqualifying requirements.
    text = posting.raw_text or ""
    for pat, regex in _REJECT_PATTERNS:
        if regex.search(text):
            return False, f"disqualifying: {pat}"

    # 5. Authentication roles (sneaker domain only).
    if lanes.is_authentication_role(posting) and not lanes.authentication_qualifies(posting):
        return False, "authentication role: needs remote or supervisory at $100k+"

    # 6. Location.
    return _location_check(posting, lane)
