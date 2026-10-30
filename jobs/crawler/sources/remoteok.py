"""Remote OK (public JSON API). See SPEC.md "Sources".

One GET returns the newest jobs as an array; element 0 is metadata (terms
of service), skipped:

    https://remoteok.com/api   -> [{"legal": ...}, {job}, {job}, ...]

Needs a browser-like User-Agent or it may 403. Terms: link back to the
Remote OK job URL and name Remote OK as the source (the digest shows
"via Remote OK").

Every posting is fully remote. `location` is free text and often empty
(empty = no restriction). salary_min / salary_max are integers, 0 = not
posted; treated as USD annual. No estimate is ever applied to this source.
"""
from __future__ import annotations

import html
import logging
from datetime import date
from typing import Any, Dict, Iterator, Optional

import requests

from crawler.base import Posting, Source
from crawler.sources.nike import strip_html

log = logging.getLogger(__name__)

API_URL = "https://remoteok.com/api"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# --- Parsing ---------------------------------------------------------------

def _salary(value: Any) -> Optional[float]:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s or len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_posting(job: Dict[str, Any], source_name: str = "remoteok") -> Posting:
    """One Remote OK job -> Posting."""
    lo, hi = _salary(job.get("salary_min")), _salary(job.get("salary_max"))
    if lo is None:
        lo, hi = hi, hi
    location = (job.get("location") or "").strip()
    return Posting(
        source=source_name,
        source_job_id=str(job["id"]),
        title=(job.get("position") or "").strip(),
        employer=(job.get("company") or "").strip(),
        url=job.get("url") or "",
        raw_text=strip_html(html.unescape(job.get("description") or "")),
        salary_min=lo,
        salary_max=hi,
        currency="USD",
        pay_period="annual",
        location=location or None,
        all_locations=[location] if location else None,
        posted_date=_parse_iso_date(job.get("date")),
        remote_type="full",
    )


# --- Source -----------------------------------------------------------------

class RemoteOKSource(Source):
    """Remote OK feed. One request per run."""

    name = "remoteok"
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
            raise RuntimeError(f"remoteok: request error: {e}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"remoteok: unexpected status {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError("remoteok: non-JSON response") from e
        # Element 0 is the terms-of-service metadata; jobs carry an id.
        jobs = [j for j in data if isinstance(j, dict) and j.get("id")]
        if not jobs:
            log.warning("remoteok: 0 jobs")
        parsed = 0
        for job in jobs:
            try:
                yield parse_posting(job, self.name)
                parsed += 1
            except Exception:
                log.exception("remoteok: parse failed for %s; skipping", job.get("id"))
        log.info("remoteok: %d jobs", parsed)
