"""Himalayas (public JSON API). See SPEC.md "Sources".

The full feed is 100k+ jobs, newest first, 20 per page, cursor-paged:

    https://himalayas.app/jobs/api?limit=20[&cursor=<nextCursor>]

We take the newest MAX_JOBS per run (25 pages, 2s apart), following
nextCursor (offset paging is deprecated).

Every posting is fully remote. locationRestrictions is the list of
countries the CANDIDATE may live in: empty = anywhere (lane 3 eligible),
US included = US-eligible (lane 3), non-US only = overseas (lane 2); see
lanes.remote_board_scope. Salary is structured (minSalary / maxSalary /
currency / salaryPeriod) and goes straight through pay.py, so a missing or
unknown currency stays pay unverified. No estimate is ever applied.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests

from crawler.base import Posting, Source
from crawler.sources.nike import strip_html

log = logging.getLogger(__name__)

API_URL = "https://himalayas.app/jobs/api"
PAGE_LIMIT = 20  # server caps limit at 20
MAX_JOBS = 500

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# --- Parsing ---------------------------------------------------------------

def _epoch_date(value: Any) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_posting(job: Dict[str, Any], source_name: str = "himalayas") -> Posting:
    """One Himalayas job -> Posting."""
    locs: List[str] = [c for c in (job.get("locationRestrictions") or []) if c]
    lo, hi = job.get("minSalary"), job.get("maxSalary")
    if lo is None:
        lo = hi
    return Posting(
        source=source_name,
        source_job_id=job["guid"],
        title=(job.get("title") or "").strip(),
        employer=(job.get("companyName") or "").strip(),
        url=job.get("applicationLink") or job["guid"],
        raw_text=strip_html(html.unescape(job.get("description") or "")),
        salary_min=lo,
        salary_max=hi,
        currency=job.get("currency"),
        pay_period=job.get("salaryPeriod"),
        location=", ".join(locs) if locs else None,
        all_locations=locs or None,
        posted_date=_epoch_date(job.get("pubDate")),
        remote_type="full",
    )


# --- Source -----------------------------------------------------------------

class HimalayasSource(Source):
    """Newest MAX_JOBS Himalayas jobs, cursor-paged, 2s between requests."""

    name = "himalayas"
    delay_seconds = 2.0

    def __init__(
        self,
        max_jobs: int = MAX_JOBS,
        session: Optional[requests.Session] = None,
        request_timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._max_jobs = max_jobs
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._sleep = sleep

    def _page(self, cursor: Optional[str]) -> Optional[Dict[str, Any]]:
        """One page, or None to stop. Raises _BackOff on 429/5xx."""
        params: Dict[str, Any] = {"limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = self._session.get(API_URL, params=params, headers=_HEADERS, timeout=self._timeout)
        except requests.RequestException as e:
            log.error("himalayas: request error: %s", e)
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("himalayas: %d; backing off", resp.status_code)
            raise _BackOff()
        if resp.status_code != 200:
            log.error("himalayas: unexpected status %d", resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            log.error("himalayas: non-JSON response")
            return None

    def fetch_listings(self) -> Iterator[Posting]:
        cursor: Optional[str] = None
        seen: set[str] = set()
        pages = 0
        try:
            while len(seen) < self._max_jobs:
                if pages:
                    self._sleep(self.delay_seconds)
                page = self._page(cursor)
                pages += 1
                if page is None:
                    if pages == 1:
                        raise RuntimeError("himalayas: first page failed")
                    break
                jobs = page.get("jobs") or []
                for job in jobs:
                    if len(seen) >= self._max_jobs:
                        break
                    guid = job.get("guid")
                    if not guid or guid in seen:
                        continue
                    seen.add(guid)
                    try:
                        yield parse_posting(job, self.name)
                    except Exception:
                        log.exception("himalayas: parse failed for %s; skipping", guid)
                cursor = page.get("nextCursor")
                if not jobs or not cursor:
                    break
        except _BackOff:
            log.warning("himalayas: skipping rest of source this run after back-off")
        log.info("himalayas: %d jobs over %d pages", len(seen), pages)


class _BackOff(Exception):
    """429/5xx: stop this source for the run."""
