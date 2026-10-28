"""Nike careers (Workday CxS JSON API). See SPEC.md "Sources".

Endpoints (tenant "nike", site "nke" = corporate + international stores;
"nke2" = US retail associates, deliberately skipped):

    list:   POST https://nike.wd1.myworkdayjobs.com/wday/cxs/nike/nke/jobs
            body {"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": ""}
    detail: GET  https://nike.wd1.myworkdayjobs.com/wday/cxs/nike/nke{externalPath}

The list response is thin (title, externalPath, locationsText, relative
postedOn, req id). Description, ISO posted date, country, and canonical URL
come from the detail call, which is fetched ONLY for job ids not already in
the store (or stored without a description). Known jobs are re-yielded with
their stored detail fields so store.upsert_job refreshes last_seen_at without
blanking raw_text.

Workday facets are include-only, so "exclude Retail Stores" is done by
reading the jobFamilyGroup facet from an unfiltered first page and applying
every other family id.

Nike does not post salary in structured fields. Pay is parsed from the
description only when a clear "$X ... $Y" salary range appears (US
pay-transparency states); otherwise salary is None -> "pay unverified".
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from crawler.base import Posting, Source

log = logging.getLogger(__name__)

HOST = "https://nike.wd1.myworkdayjobs.com"
TENANT = "nike"
SITE = "nke"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_BASE = f"{HOST}/wday/cxs/{TENANT}/{SITE}"
PUBLIC_BASE = f"{HOST}/{SITE}"

PAGE_LIMIT = 20  # Workday caps limit at 20
PROGRESS_EVERY = 50  # log a progress line every N detail fetches
EXCLUDED_FAMILIES = frozenset({"Retail Stores"})
EMPLOYER_FALLBACK = "NIKE, Inc."
US_COUNTRY = "United States of America"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": PUBLIC_BASE,
}

# Workday multi-location placeholder in locationsText, e.g. "2 Locations".
_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)

# "$123,400.00 ... $185,000.00" within a salary/pay sentence.
_SALARY_CONTEXT_RE = re.compile(
    r"(salary|pay range|base pay|compensation)[^$]{0,200}"
    r"\$\s?([\d,]+(?:\.\d{2})?)[^$]{0,80}?\$\s?([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE | re.DOTALL,
)


# --- Parsing ---------------------------------------------------------------

def strip_html(html: str) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def with_country(loc: str, country: Optional[str]) -> str:
    """Append the Workday country descriptor when the location text doesn't
    already name it. US locations ("Beaverton, Oregon") are left alone —
    Workday writes them City, State."""
    if not loc or not country or country == US_COUNTRY:
        return loc
    if country.lower() in loc.lower():
        return loc
    return f"{loc}, {country}"


def parse_salary(raw_text: str) -> Tuple[Optional[float], Optional[float]]:
    """Annual USD range from description text, or (None, None). Amounts
    under 10,000 are treated as hourly/other and ignored (pay unverified)."""
    m = _SALARY_CONTEXT_RE.search(raw_text or "")
    if not m:
        return None, None
    try:
        lo = float(m.group(2).replace(",", ""))
        hi = float(m.group(3).replace(",", ""))
    except ValueError:
        return None, None
    if lo < 10_000 or hi < lo:
        return None, None
    return lo, hi


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s or len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def job_id(item: Dict[str, Any]) -> Optional[str]:
    bullets = item.get("bulletFields") or []
    if bullets:
        return str(bullets[0])
    path = item.get("externalPath") or ""
    m = re.search(r"_(R-\d+(?:-\d+)?)$", path)
    return m.group(1) if m else None


def parse_posting(
    item: Dict[str, Any], detail: Dict[str, Any], source_name: str = "nike",
) -> Posting:
    """List item + detail response -> Posting."""
    info = detail.get("jobPostingInfo") or {}
    country = (info.get("country") or {}).get("descriptor")

    locs_text = item.get("locationsText") or info.get("location") or ""
    if _MULTI_LOC_RE.match(locs_text) or not locs_text:
        raw_locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    else:
        raw_locs = [locs_text]
    all_locations = [with_country(l, country) for l in raw_locs if l]

    raw_text = strip_html(info.get("jobDescription") or "")
    salary_min, salary_max = parse_salary(raw_text)

    return Posting(
        source=source_name,
        source_job_id=job_id(item) or info.get("jobReqId") or "",
        title=item.get("title") or info.get("title") or "",
        employer=(detail.get("hiringOrganization") or {}).get("name") or EMPLOYER_FALLBACK,
        url=info.get("externalUrl") or f"{PUBLIC_BASE}{item.get('externalPath', '')}",
        raw_text=raw_text,
        salary_min=salary_min,
        salary_max=salary_max,
        currency="USD",
        pay_period="annual",
        location=all_locations[0] if len(all_locations) == 1 else (locs_text or None),
        all_locations=all_locations or None,
        posted_date=_parse_iso_date(info.get("startDate")),
    )


def posting_from_stored(item: Dict[str, Any], row: Dict[str, Any], source_name: str = "nike") -> Posting:
    """Known job: fresh title from the list, detail fields from the store.
    Stored salary is already USD-monthly, so the Posting defaults apply."""
    return Posting(
        source=source_name,
        source_job_id=row["source_job_id"],
        title=item.get("title") or row["title"],
        employer=row["employer"],
        url=row["url"],
        raw_text=row["raw_text"],
        classification=row.get("classification"),
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        location=row.get("location"),
        all_locations=row.get("all_locations"),
        telework_flag=row.get("telework_flag"),
        posted_date=row.get("posted_date"),
    )


def family_ids(facets: List[Dict[str, Any]]) -> List[str]:
    """jobFamilyGroup ids for every family except EXCLUDED_FAMILIES."""
    for f in facets or []:
        if f.get("facetParameter") == "jobFamilyGroup":
            return [
                v["id"] for v in f.get("values") or []
                if v.get("id") and v.get("descriptor") not in EXCLUDED_FAMILIES
            ]
    return []


def _default_known_jobs() -> Dict[str, Dict[str, Any]]:
    """Nike rows already in the store, keyed by source_job_id. Empty when the
    store isn't initialized (e.g. a standalone fetch)."""
    import store  # noqa: PLC0415
    try:
        rows = store.get_active_jobs(seen_within_days=3650)
    except RuntimeError:
        return {}
    return {r["source_job_id"]: r for r in rows if r["source"] == "nike"}


# --- Source -----------------------------------------------------------------

class NikeSource(Source):
    """Nike corporate careers via Workday CxS. Sequential, 2s between requests."""

    name = "nike"
    delay_seconds = 2.0

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        request_timeout: float = 30.0,
        known_jobs_loader: Callable[[], Dict[str, Dict[str, Any]]] = _default_known_jobs,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._known_jobs_loader = known_jobs_loader
        self._sleep = sleep
        self._first_request = True

    # Every request goes through here: enforces the inter-request delay and
    # the 429/5xx back-off rule. Returns parsed JSON or None to stop.
    def _request(self, method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._first_request:
            self._sleep(self.delay_seconds)
        self._first_request = False
        try:
            resp = self._session.request(method, url, headers=_HEADERS, json=body, timeout=self._timeout)
        except requests.RequestException as e:
            log.error("nike: request error %s %s: %s", method, url, e)
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("nike: %d on %s %s; backing off", resp.status_code, method, url)
            raise _BackOff()
        if resp.status_code != 200:
            log.error("nike: unexpected status %d on %s %s: %s", resp.status_code, method, url, resp.text[:300])
            return None
        try:
            return resp.json()
        except ValueError:
            log.error("nike: non-JSON response on %s %s", method, url)
            return None

    def _list_page(self, offset: int, facets: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = {"appliedFacets": facets, "limit": PAGE_LIMIT, "offset": offset, "searchText": ""}
        return self._request("POST", LIST_URL, body)

    def _list_items(self) -> Iterator[Dict[str, Any]]:
        first = self._list_page(0, {})
        if first is None:
            raise RuntimeError("nike: first list page failed")
        ids = family_ids(first.get("facets") or [])
        if not ids:
            raise RuntimeError("nike: jobFamilyGroup facet missing; refusing to crawl unfiltered")
        facets = {"jobFamilyGroup": ids}

        offset = 0
        total: Optional[int] = None
        while total is None or offset < total:
            page = self._list_page(offset, facets)
            if page is None:
                return
            if total is None:
                # Workday reports total reliably only on the first page.
                total = int(page.get("total") or 0)
                log.info("nike: %d postings after excluding %s", total, sorted(EXCLUDED_FAMILIES))
            items = page.get("jobPostings") or []
            if not items:
                return
            yield from items
            offset += PAGE_LIMIT

    def fetch_listings(self) -> Iterator[Posting]:
        known = self._known_jobs_loader()
        seen: set[str] = set()
        details_fetched = 0
        try:
            items = list(self._list_items())
            need_detail = len({
                jid for jid in (job_id(i) for i in items)
                if jid and not (known.get(jid) or {}).get("raw_text")
            })
            log.info("nike: %d listed, %d need detail fetch", len(items), need_detail)
            for item in items:
                jid = job_id(item)
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                row = known.get(jid)
                if row and row.get("raw_text"):
                    yield posting_from_stored(item, row, self.name)
                    continue
                detail = self._request("GET", f"{DETAIL_BASE}{item.get('externalPath', '')}")
                if not detail:
                    log.warning("nike: detail failed for %s; skipping", jid)
                    continue
                details_fetched += 1
                if details_fetched % PROGRESS_EVERY == 0:
                    log.info("nike: %d/%d details", details_fetched, need_detail)
                try:
                    yield parse_posting(item, detail, self.name)
                except Exception:
                    log.exception("nike: parse failed for %s; skipping", jid)
        except _BackOff:
            log.warning("nike: skipping rest of source this run after back-off")
        log.info("nike: %d listed, %d detail fetches, %d reused from store",
                 len(seen), details_fetched, len(seen) - details_fetched)


class _BackOff(Exception):
    """429/5xx: stop this source for the run."""
