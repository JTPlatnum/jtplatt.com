"""Tier 1 rule-based scoring. See SPEC.md "Soft Scoring -> Tier 1".

Pure function: Posting in, score dict out. No I/O, no DB, no scraping.

Every posting is first assigned a lane (lanes.assign_lane). Output ranks by
lane, then by this score; the score itself ranks WITHIN a lane.

Weights (sum to 100 at the cap; title can subtract 20):

    lane fit                  30    20 for having a lane + 2 per distinct lane
                                    keyword hit (cap 10). Unlaned = 0.
                                    Keywords: inventory.LANE_PREFERENCES.
    background match          20    distinct \\b-bounded matches of inventory
                                    SKILLS keywords, min(20, n*2). Honest
                                    record only — never lane keywords.
    title pattern match       20    +20 YES title, -20 NO title (NO wins).
                                    YES/NO from inventory.PREFERENCES.
    pay                       15    15 verified >= $100k/yr; 5 verified
                                    below that (it already cleared the floor);
                                    0 unverified.
    location                  10    10 fully remote or SE Asia/Europe;
                                    7 other overseas; 5 hybrid; else 0.
    recency                    5    posted_date within 7 days
    preferred country lock    10    lane 2 only: remote-board role locked to
                                    Japan / Vietnam / Thailand, so it ranks
                                    above other country-locked roles
    AI resilience            +4/-3  re-rank only (never a filter): +4 roles
                                    where human judgment, trust, relationships,
                                    or physical presence is the product; -3
                                    routine solo knowledge work with no
                                    senior/lead/management signal; else 0.
                                    See ai_resilience().

Final score is clamped to [0, 100].

title_patterns.yaml is intentionally NOT loaded. inventory.PREFERENCES is the
single source of truth for YES/NO titles; duplicating into a yaml would drift.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import lanes
from crawler.base import Posting
from data import inventory
from pay import pay_verified

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


# --- Component scorers ------------------------------------------------------

def _score_lane_fit(posting: Posting, lane: Dict[str, Any]) -> Dict[str, Any]:
    if lane["lane"] is None:
        return {"points": 0, "matched_lane_keywords": []}
    hits = lanes.lane_text_hits(posting, lane["lane"])
    return {"points": 20 + min(10, len(hits) * 2), "matched_lane_keywords": hits}


def _score_background(raw_text: str) -> Dict[str, Any]:
    text = raw_text or ""
    matched: List[str] = []
    for key, pat in _KEYWORD_PATTERNS:
        if pat.search(text):
            matched.append(key)
    return {"points": min(20, len(matched) * 2), "matched_keywords": matched}


def _score_title(posting: Posting) -> Dict[str, Any]:
    """Title-pattern match against BOTH posting.title and posting.classification.
    NO wins over YES. A YES that matches both fields counts once (single +20),
    not twice — the function returns on the first hit it finds."""
    candidates = [c for c in (posting.title, posting.classification) if c]
    for original, pat in _NO_TITLES:
        for cand in candidates:
            if pat.search(cand):
                return {"points": -20, "matched": original, "list": "no"}
    for original, pat in _YES_TITLES:
        for cand in candidates:
            if pat.search(cand):
                return {"points": 20, "matched": original, "list": "yes"}
    return {"points": 0, "matched": None, "list": None}


def _score_pay(posting: Posting) -> Dict[str, Any]:
    if not pay_verified(posting):
        return {"points": 0, "verified": False, "note": posting.pay_note}
    strong = posting.salary_min >= lanes.STRONG_MONTHLY_USD
    return {"points": 15 if strong else 5, "verified": True, "strong": strong}


def _score_location(lane: Dict[str, Any]) -> Dict[str, Any]:
    region = lane["overseas_region"]
    rtype = lane["remote_type"]
    if rtype == "full":
        return {"points": 10, "fired_by": "fully remote"}
    if region in ("southeast_asia", "europe"):
        return {"points": 10, "fired_by": region}
    if region == "other":
        return {"points": 7, "fired_by": "overseas (other)"}
    if rtype == "hybrid":
        return {"points": 5, "fired_by": "hybrid"}
    return {"points": 0, "fired_by": None}


def _score_preferred_lock(posting: Posting, lane: Dict[str, Any]) -> Dict[str, Any]:
    if lane["lane"] == 2 and lanes.preferred_country_lock(posting):
        return {"points": 10, "fired_by": "remote locked to Japan/Vietnam/Thailand"}
    return {"points": 0, "fired_by": None}


# --- AI resilience ---------------------------------------------------------
#
# Decided from the TITLE. Body text is not scanned for these keywords:
# boilerplate ("EEO compliance", "training provided", "operations") would
# give nearly every posting the bonus. raw_text is used only to tell QA of
# physical goods from software QA.

AI_RESILIENT_POINTS = 4
AI_EXPOSED_POINTS = -3

_RESILIENT_TITLE_RE = re.compile(
    r"\b("
    # judgment / trust / physical verification
    r"authenticat\w*|verification|verifier|inspect(?:ion|or)s?|apprais\w*|"
    r"grad(?:ing|er)|fraud|trust (?:&|and) safety|compliance|audit\w*|"
    # relationships / negotiation
    r"partnerships?|business development|bd|account (?:manager|management|executive)|"
    r"sales|negotiat\w*|"
    # physical world / supervisory / people
    r"operations|supervisor|supervision|plant|floor|foreman|superintendent|"
    r"people manager|manager|director|head of|trainer|training"
    r")\b",
    re.IGNORECASE,
)
_QA_TITLE_RE = re.compile(r"\b(qa|quality (?:assurance|control|inspector|technician))\b", re.IGNORECASE)
_PHYSICAL_GOODS_RE = re.compile(
    r"\b(footwear|sneakers?|apparel|garments?|factory|manufacturing|materials|"
    r"physical products?|goods|merchandise|warehouse|production line|trading cards?)\b",
    re.IGNORECASE,
)
_EXPOSED_TITLE_RE = re.compile(
    r"\b("
    r"software (?:engineer|developer)|developer|programmer|coder|"
    r"(?:front|back)[- ]?end|full[- ]?stack|web developer|"
    r"data entry|data analyst|reporting analyst|report writer|"
    r"tier[- ]?(?:1|one|i)|help ?desk|service desk|customer support|"
    r"support (?:specialist|representative|rep|agent)|first[- ]line|"
    r"content writer|copywriter|writer|bookkeep\w*"
    r")\b",
    re.IGNORECASE,
)
# Managing the work beats doing it: cancels the exposed penalty.
_SENIOR_SIGNAL_RE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|architect|manager|management|"
    r"director|head|chief|vp|supervisor)(?!\w)",
    re.IGNORECASE,
)


def ai_resilience(posting: Posting) -> Dict[str, Any]:
    """Re-rank signal for how exposed a role is to AI automation. Adjusts
    points only; never drops a posting. +4 resilient, -3 exposed, 0 neutral."""
    title = posting.title or ""
    m = _RESILIENT_TITLE_RE.search(title)
    if m:
        return {"points": AI_RESILIENT_POINTS, "tier": "resilient", "matched": m.group(1).lower()}
    if _QA_TITLE_RE.search(title) and _PHYSICAL_GOODS_RE.search(posting.raw_text or ""):
        return {"points": AI_RESILIENT_POINTS, "tier": "resilient", "matched": "physical-goods QA"}
    m = _EXPOSED_TITLE_RE.search(title)
    if m and not _SENIOR_SIGNAL_RE.search(title):
        return {"points": AI_EXPOSED_POINTS, "tier": "exposed", "matched": m.group(1).lower()}
    return {"points": 0, "tier": "neutral", "matched": None}


def _score_recency(posted_date: Optional[date], now: date) -> Dict[str, Any]:
    if posted_date is None:
        return {"points": 0, "age_days": None}
    age = (now - posted_date).days
    return {"points": 5 if 0 <= age <= 7 else 0, "age_days": age}


# --- Main entry -------------------------------------------------------------

def score_posting(posting: Posting, now: Optional[date] = None) -> Dict[str, Any]:
    """Score a posting 0..100 with lane + component breakdown.

    Args:
        posting: a crawler.base.Posting
        now: reference date for recency check; defaults to date.today()

    Returns:
        {"score": int, "lane": {...}, "components": {...}}

    lane: lanes.assign_lane() output (lane id 1-5 or None, name, reason,
    overseas_region, remote_type). components keys: lane_fit,
    background_match, title_match, pay, location, recency, preferred_lock, ai_resilience. Each carries at
    minimum a "points" int plus fields describing what fired.
    """
    now = now or date.today()
    lane = lanes.assign_lane(posting)
    components = {
        "lane_fit": _score_lane_fit(posting, lane),
        "background_match": _score_background(posting.raw_text),
        "title_match": _score_title(posting),
        "pay": _score_pay(posting),
        "location": _score_location(lane),
        "recency": _score_recency(posting.posted_date, now),
        "preferred_lock": _score_preferred_lock(posting, lane),
        "ai_resilience": ai_resilience(posting),
    }
    total = sum(c["points"] for c in components.values())
    clamped = max(0, min(100, total))
    return {"score": clamped, "lane": lane, "components": components}
