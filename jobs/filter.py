"""Hard filters. See SPEC.md.

Drops postings that violate baseline rules BEFORE scoring. The scorer (and
JT) only see what survives this stage. Pure function: Posting in,
(passes, reason) out. Single short-circuit return on the first failing rule.

Rules in order — cheap structural checks first, yaml-loaded pattern checks
last:

    1. salary floor       SALARY_FLOOR env (monthly USD)
    2. current employer   FI$CAL / FISCal — JT is leaving
    3. source allow-list  structural placeholder; widens as sources land
    4. disqualifying req  data/reject_requirements.yaml substrings
    5. location           target_locations OR telework

Read SALARY_FLOOR via env, not by importing dotenv here. Callers that need
.env loaded should call load_dotenv() before importing this module.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Tuple

import yaml

from crawler.base import Posting
from data import inventory

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
REJECT_REQUIREMENTS_PATH = REPO_ROOT / "data" / "reject_requirements.yaml"


# --- Config -----------------------------------------------------------------

def _salary_floor() -> float:
    raw = os.getenv("SALARY_FLOOR", "0")
    try:
        return float(raw)
    except ValueError:
        log.warning("filter: SALARY_FLOOR=%r not parseable; defaulting to 0", raw)
        return 0.0


SALARY_FLOOR = _salary_floor()

# Sources whose postings are eligible for filtering. New scrapers land here as
# they're implemented (governmentjobs, edjoin). Anything else is rejected —
# keeps stray test postings or future-source bleed out of results.
ALLOWED_SOURCES = frozenset({"usajobs", "calcareers", "csu"})

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


# --- Location matching -----------------------------------------------------

_TARGET_LOCATIONS = [
    s.lower() for s in inventory.PREFERENCES.get("target_locations", [])
]


def _location_strings(posting: Posting) -> List[str]:
    if posting.all_locations:
        return list(posting.all_locations)
    return [posting.location] if posting.location else []


def _matches_target_location(posting: Posting) -> bool:
    if not _TARGET_LOCATIONS:
        return False
    for loc in _location_strings(posting):
        if not loc:
            continue
        low = loc.lower()
        for target in _TARGET_LOCATIONS:
            if target in low:
                return True
    return False


# --- Public API ------------------------------------------------------------

def should_keep(posting: Posting) -> Tuple[bool, str]:
    """Apply hard filters in order. Returns (passes, reason).

    reason is "" when passes; otherwise a short human-readable reject reason
    suitable for log/footer surfacing. Single short-circuit on first
    failing rule.
    """
    # 1. Salary floor — only enforced when salary_min is known. Postings with
    # missing salary data pass this rule and get evaluated later (scorer may
    # downrank them on its own merits).
    if posting.salary_min is not None and posting.salary_min < SALARY_FLOOR:
        return False, "below salary floor"

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

    # 5. Location — telework OR any target-location substring match.
    if posting.telework_flag is True or _matches_target_location(posting):
        return True, ""
    return False, "outside target locations"
