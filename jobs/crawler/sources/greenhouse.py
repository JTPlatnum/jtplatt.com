"""Greenhouse job boards (public JSON API). See SPEC.md "Sources".

One source, several boards. Each board is one GET that returns every job
with its HTML description (no paging; meta.total is checked against the
job count):

    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Boards (token -> employer). Sneaker / collectibles brands, so the employer
name carries the lane-1 domain keyword:

    stockx                StockX
    fanaticsinc           Fanatics
    fanaticscollectibles  Fanatics Collectibles
    goatgroup             GOAT Group
    (fanaticsfbg — Fanatics Betting & Gaming — deliberately skipped)

Pay: US postings usually state a range in the description (pay-transparency
laws). parse_salary reads the FIRST range after a salary/pay keyword:
"Salary Range $150,000 — $200,000 USD", "base salary range ... is $120,000
to $130,000", tiered "Tier 1 ... $166,900 - $208,600 USD" (Tier 1 = highest-
cost markets), hourly "Hiring Range: $20 — $20 USD". The currency code after
the range is passed to pay.py, so a CAD/AUD range stays pay unverified.
No range -> pay unverified, and the lane-1 estimate layer applies.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests

from crawler.base import Posting, Source
from crawler.sources.nike import strip_html

log = logging.getLogger(__name__)

API_BASE = "https://boards-api.greenhouse.io/v1/boards"

BOARDS: Dict[str, str] = {
    "stockx": "StockX",
    "fanaticsinc": "Fanatics",
    "fanaticscollectibles": "Fanatics Collectibles",
    "goatgroup": "GOAT Group",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Keyword, then (within 250 chars, no other "$" in between) "$lo[unit]",
# optionally "<dash|to> $hi[unit]", then an optional currency code. A single
# figure only counts with an explicit unit ("$15/hr", "$24.54/hour").
_AMOUNT = r"\$\s?([\d,]+(?:\.\d{1,2})?)"
_UNIT = r"(?:\s*(?:/|per\s+)(hr|hour|yr|year|annum)\b)?"
_SALARY_RE = re.compile(
    r"(salary|hiring range|compensation|\bpay\b)[^$]{0,250}"
    + _AMOUNT + _UNIT
    + r"(?:\s*(?:-|–|—|to)\s*" + _AMOUNT + _UNIT + r")?"
    + r"(?:\s*((?-i:[A-Z]{3}))\b)?",
    re.IGNORECASE | re.DOTALL,
)
_HOURLY_UNITS = {"hr", "hour"}

# Without a unit: below this the figure is hourly ("$20 — $20 USD");
# 1,000-10,000 is ambiguous (monthly? typo?) and left unverified.
_HOURLY_MAX = 1_000
_ANNUAL_MIN = 10_000
# Hourly figures below the US federal minimum wage are placeholders
# (Fanatics posts "Salary Range $1 — $1 USD"): pay unverified.
_HOURLY_MIN = 7.25


# --- Parsing ---------------------------------------------------------------

def parse_salary(raw_text: str) -> Tuple[Optional[float], Optional[float], str, Optional[str]]:
    """(lo, hi, currency, pay_period) from description text. (None, None,
    "USD", None) when no usable range is found."""
    none: Tuple[Optional[float], Optional[float], str, Optional[str]] = (None, None, "USD", None)
    for m in _SALARY_RE.finditer(raw_text or ""):
        _kw, lo_s, lo_unit, hi_s, hi_unit, currency = m.groups()
        unit = (hi_unit or lo_unit or "").lower()
        if hi_s is None and not unit:
            continue  # lone "$5 million" etc.: not pay
        try:
            lo = float(lo_s.replace(",", ""))
            hi = float((hi_s or lo_s).replace(",", ""))
        except ValueError:
            return none
        if hi < lo:
            return none
        currency = (currency or "USD").upper()
        if unit:
            period = "hourly" if unit in _HOURLY_UNITS else "annual"
        elif hi < _HOURLY_MAX:
            period = "hourly"
        elif lo < _ANNUAL_MIN:
            return none
        else:
            period = "annual"
        if period == "hourly" and lo < _HOURLY_MIN:
            return none
        return lo, hi, currency, period
    return none


def split_locations(name: Optional[str]) -> List[str]:
    """Greenhouse joins multiple locations with ';'."""
    return [p.strip() for p in (name or "").split(";") if p.strip()]


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s or len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_posting(job: Dict[str, Any], token: str, source_name: str = "greenhouse") -> Posting:
    """One Greenhouse job (content=true) -> Posting. `content` arrives
    entity-escaped ("&lt;p&gt;"), so unescape before stripping tags."""
    raw_text = strip_html(html.unescape(job.get("content") or ""))
    lo, hi, currency, period = parse_salary(raw_text)
    locs = split_locations((job.get("location") or {}).get("name"))
    return Posting(
        source=source_name,
        source_job_id=f"{token}-{job['id']}",
        title=(job.get("title") or "").strip(),
        employer=BOARDS.get(token) or job.get("company_name") or token,
        url=job.get("absolute_url") or "",
        raw_text=raw_text,
        salary_min=lo,
        salary_max=hi,
        currency=currency,
        pay_period=period or "annual",
        location=locs[0] if len(locs) == 1 else ((job.get("location") or {}).get("name") or None),
        all_locations=locs or None,
        posted_date=_parse_iso_date(job.get("first_published") or job.get("updated_at")),
    )


# --- Source -----------------------------------------------------------------

class GreenhouseSource(Source):
    """All configured Greenhouse boards, sequential, 2s between requests."""

    name = "greenhouse"
    delay_seconds = 2.0

    def __init__(
        self,
        boards: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
        request_timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._boards = boards if boards is not None else BOARDS
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._sleep = sleep

    def _fetch_board(self, token: str) -> Optional[List[Dict[str, Any]]]:
        """Jobs for one board, or None on a non-retryable failure. Raises
        _BackOff on 429/5xx."""
        url = f"{API_BASE}/{token}/jobs?content=true"
        try:
            resp = self._session.get(url, headers=_HEADERS, timeout=self._timeout)
        except requests.RequestException as e:
            log.error("greenhouse.%s: request error: %s", token, e)
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("greenhouse.%s: %d; backing off", token, resp.status_code)
            raise _BackOff()
        if resp.status_code != 200:
            log.error("greenhouse.%s: unexpected status %d", token, resp.status_code)
            return None
        try:
            data = resp.json()
        except ValueError:
            log.error("greenhouse.%s: non-JSON response", token)
            return None
        jobs = data.get("jobs") or []
        total = (data.get("meta") or {}).get("total")
        if total is not None and total != len(jobs):
            log.warning("greenhouse.%s: meta.total=%s but %d jobs returned", token, total, len(jobs))
        return jobs

    def fetch_listings(self) -> Iterator[Posting]:
        failed: List[str] = []
        try:
            for i, token in enumerate(self._boards):
                if i:
                    self._sleep(self.delay_seconds)
                jobs = self._fetch_board(token)
                if jobs is None:
                    failed.append(token)
                    continue
                if not jobs:
                    log.warning("greenhouse.%s: 0 jobs", token)
                parsed = 0
                for job in jobs:
                    try:
                        yield parse_posting(job, token, self.name)
                        parsed += 1
                    except Exception:
                        log.exception("greenhouse.%s: parse failed for %s; skipping",
                                      token, job.get("id"))
                log.info("greenhouse.%s: %d jobs", token, parsed)
        except _BackOff:
            log.warning("greenhouse: skipping rest of source this run after back-off")
            return
        if failed and len(failed) == len(self._boards):
            raise RuntimeError(f"greenhouse: every board failed: {failed}")
        if failed:
            log.error("greenhouse: boards failed this run: %s", failed)


class _BackOff(Exception):
    """429/5xx: stop this source for the run."""
