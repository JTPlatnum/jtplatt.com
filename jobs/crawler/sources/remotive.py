"""Remotive (public JSON API). See SPEC.md "Sources".

One GET returns the whole free feed (jobs delayed 24h by Remotive):

    https://remotive.com/api/remote-jobs   -> {"jobs": [...], ...}

Terms: link back to the Remotive job URL and name Remotive as the source
(the digest shows "via Remotive"); at most 4 calls a day (we make 1).

Every posting is fully remote. candidate_required_location says where the
CANDIDATE may live ("Europe, USA, Canada"), split on commas into
all_locations; lanes.remote_board_scope decides lane 2 vs lane 3 from it.

Salary is free text. parse_salary reads "$Xk - $Yk" / "$170,000 - $200,000"
ranges (annual) and "$X/hour" / "$50-$75 /hour" (hourly). Skipped (pay
unverified): "OTE" (commission), "per task", and anything without "$"
("70K-80K" has no currency). No estimate is ever applied to this source.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from crawler.base import Posting, Source
from crawler.sources.nike import strip_html

log = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_SKIP_RE = re.compile(r"\bOTE\b|per\s+task", re.IGNORECASE)
_HOURLY_RE = re.compile(r"/\s*h(?:ou)?r\b|per\s+hour|hourly", re.IGNORECASE)
# "170,000" (thousands) | "35,3" / "35.3" (decimal) | "170", each with optional k.
_AMOUNT_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)\s*(k)?\b", re.IGNORECASE)


# --- Parsing ---------------------------------------------------------------

def _amount(num: str, k: Optional[str]) -> float:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", num):
        value = float(num.replace(",", ""))
    else:
        value = float(num.replace(",", "."))
    return value * 1000 if k else value


def parse_salary(text: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """(lo, hi, pay_period) from Remotive's free-text salary, or
    (None, None, None)."""
    s = (text or "").strip()
    if "$" not in s or _SKIP_RE.search(s):
        return None, None, None
    amounts = [_amount(m.group(1), m.group(2)) for m in _AMOUNT_RE.finditer(s)][:2]
    if not amounts:
        return None, None, None
    lo, hi = amounts[0], amounts[-1]
    if hi < lo:
        return None, None, None
    if _HOURLY_RE.search(s):
        return lo, hi, "hourly"
    if len(amounts) == 2 and lo >= 10_000:
        return lo, hi, "annual"
    return None, None, None


def split_locations(text: Optional[str]) -> List[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s or len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_posting(job: Dict[str, Any], source_name: str = "remotive") -> Posting:
    """One Remotive job -> Posting."""
    lo, hi, period = parse_salary(job.get("salary"))
    locs = split_locations(job.get("candidate_required_location"))
    return Posting(
        source=source_name,
        source_job_id=str(job["id"]),
        title=(job.get("title") or "").strip(),
        employer=(job.get("company_name") or "").strip(),
        url=job.get("url") or "",
        raw_text=strip_html(html.unescape(job.get("description") or "")),
        salary_min=lo,
        salary_max=hi,
        currency="USD",
        pay_period=period or "annual",
        location=job.get("candidate_required_location") or None,
        all_locations=locs or None,
        posted_date=_parse_iso_date(job.get("publication_date")),
        remote_type="full",
    )


# --- Source -----------------------------------------------------------------

class RemotiveSource(Source):
    """Remotive free feed. One request per run."""

    name = "remotive"
    delay_seconds = 2.0

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        request_timeout: float = 60.0,
    ):
        self._session = session or requests.Session()
        self._timeout = request_timeout

    def fetch_listings(self) -> Iterator[Posting]:
        try:
            resp = self._session.get(API_URL, headers=_HEADERS, timeout=self._timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"remotive: request error: {e}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"remotive: unexpected status {resp.status_code}")
        try:
            jobs = resp.json().get("jobs") or []
        except ValueError as e:
            raise RuntimeError("remotive: non-JSON response") from e
        if not jobs:
            log.warning("remotive: 0 jobs")
        parsed = 0
        for job in jobs:
            try:
                yield parse_posting(job, self.name)
                parsed += 1
            except Exception:
                log.exception("remotive: parse failed for %s; skipping", job.get("id"))
        log.info("remotive: %d jobs", parsed)
