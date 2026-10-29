"""Salary ESTIMATE layer for sneaker-lane postings. See data/salary_estimates.yaml.

Sneaker employers (Nike) rarely post pay. For a lane-1 posting with NO
verified pay, the title's level ("Senior Manager", "Director", "Lead", ...)
maps to an estimated US base range (annual USD):

    estimate_min / estimate_max   annual USD, or None
    estimate_source               "level_table", or None

Estimates are kept separate from salary_min/salary_max on purpose: they are
never verified pay. filter.py uses estimate_min for the sneaker-lane floor;
notify.py shows them marked as estimates. Nothing else may read them, and
they must never flow into application or resume output.

Overseas (estimate_basis):
    "us"          US posting, full US band
    "expat"       overseas, no local-hire marker in the title: full US band,
                  on the assumption the role pays expat / US wages
    "local_hire"  overseas with a local-hire marker in the title ("Local
                  Creation", "GC Local", "ITC", ...): band x local_hire_multiplier

apply_estimate() always recomputes (deterministic from title + lane + pay),
so a posting whose pay becomes verified, or that leaves lane 1, loses its
estimate on the next pass.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent
ESTIMATE_TABLE_PATH = REPO_ROOT / "data" / "salary_estimates.yaml"
ESTIMATE_SOURCE = "level_table"
FALLBACK = "fallback"


def _load_table(path: Path = ESTIMATE_TABLE_PATH) -> Tuple[Dict[str, Tuple[float, float]], float]:
    data = yaml.safe_load(path.read_text()) or {}
    table = {k: (float(v["min"]), float(v["max"])) for k, v in data["levels"].items()}
    table[FALLBACK] = (float(data["fallback"]["min"]), float(data["fallback"]["max"]))
    return table, float(data["local_hire_multiplier"])


ESTIMATES, LOCAL_HIRE_MULTIPLIER = _load_table()

# Local-hire language in an overseas title: "Local Creation GC", "GC Local",
# "Local Hire", a bare "Local" token, or "ITC" (Nike's India tech center,
# local-hire tech roles).
_LOCAL_HIRE_RE = re.compile(r"\b(?:local|itc)\b", re.IGNORECASE)

_SENIOR = r"(?:senior|sr\.?)"

# Most senior first; first match wins. Lead/Principal/Expert are checked
# BEFORE plain "manager" so IC titles like "Lead Technical Product Manager"
# read as Lead, not Manager.
LEVEL_RULES: List[Tuple[str, re.Pattern]] = [
    ("senior_director_vp", re.compile(
        rf"\b(?:vice\s+president|s?vp|evp|{_SENIOR}\s+director)\b", re.IGNORECASE)),
    ("director", re.compile(r"\bdirector\b", re.IGNORECASE)),
    ("senior_manager", re.compile(rf"\b{_SENIOR}\s+manager\b", re.IGNORECASE)),
    ("lead", re.compile(r"\b(?:lead|principal|expert)\b|\bspecialist\s+(?:ii|2)\b",
                        re.IGNORECASE)),
    ("manager", re.compile(r"\bmanager\b", re.IGNORECASE)),
    ("senior_professional", re.compile(rf"\b{_SENIOR}(?!\w)", re.IGNORECASE)),
    ("professional", re.compile(r"\bprofessional\b", re.IGNORECASE)),
]

for _key, _ in LEVEL_RULES:
    if _key not in ESTIMATES:
        raise ValueError(f"{ESTIMATE_TABLE_PATH}: missing level {_key!r}")


def detect_level(title: Optional[str]) -> str:
    """Level key for a job title, or "fallback" when no level is readable."""
    for key, pattern in LEVEL_RULES:
        if title and pattern.search(title):
            return key
    return FALLBACK


def estimate_for_title(title: Optional[str]) -> Tuple[float, float]:
    """(min, max) annual USD for the title's level."""
    return ESTIMATES[detect_level(title)]


def is_local_hire(title: Optional[str]) -> bool:
    return bool(title and _LOCAL_HIRE_RE.search(title))


def estimate_basis(posting: Any) -> str:
    """"us" | "expat" | "local_hire" — what the estimate assumes."""
    import lanes  # noqa: PLC0415

    if lanes.overseas_region(posting) is None:
        return "us"
    return "local_hire" if is_local_hire(posting.title) else "expat"


def apply_estimate(posting: Any, lane: Optional[int] = None) -> None:
    """Set estimate_* on a lane-1 posting without verified pay; clear them
    otherwise. Pass `lane` when the caller already assigned it."""
    from pay import pay_verified  # noqa: PLC0415

    if lane is None:
        import lanes  # noqa: PLC0415
        lane = lanes.assign_lane(posting)["lane"]
    if lane == 1 and not pay_verified(posting):
        lo, hi = estimate_for_title(posting.title)
        if estimate_basis(posting) == "local_hire":
            lo, hi = lo * LOCAL_HIRE_MULTIPLIER, hi * LOCAL_HIRE_MULTIPLIER
        posting.estimate_min, posting.estimate_max = lo, hi
        posting.estimate_source = ESTIMATE_SOURCE
    else:
        posting.estimate_min = posting.estimate_max = posting.estimate_source = None
