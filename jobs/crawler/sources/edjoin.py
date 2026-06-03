"""EdJoin Careers scraper (CA K-12 + community college + COE job board).

PageUp competitor running Microsoft IIS / ASP.NET MVC 5.3 + Azure Cognitive
Search backend. Listings come from a public JSON API; detail pages are SSR
HTML with an embedded `<script type="application/ld+json">` schema.org
JobPosting block. Uses `requests` + `BeautifulSoup` — no Playwright.

See notes/edjoin-recon.md "Decisions (locked 2026-06-03)" for the locked
design (5-query lanes, listing-API-first field precedence, pagination loop).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from crawler.base import Posting, Source

log = logging.getLogger(__name__)

BASE_URL = "https://www.edjoin.org"
LOADJOBS_URL = f"{BASE_URL}/Home/LoadJobs"
DETAIL_URL_TEMPLATE = f"{BASE_URL}/Home/JobPosting/{{posting_id}}"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

ROWS_PER_PAGE = 50
PAGE_SAFETY_CAP = 20  # 1,000 records per query — beyond JT's expected volume

# All LoadJobs params must be present even when "empty" (recon §11.2 — empty
# params cause 500 NullReferenceException on the .NET side). Defaults below;
# query-config values override.
_LOADJOBS_DEFAULTS: Dict[str, Any] = {
    "rows": ROWS_PER_PAGE,
    "page": 1,
    "sort": "postingDate",   # camelCase required — Azure Search is case-sensitive
    "sortVal": "0",
    "order": "desc",
    "keywords": "",
    "location": "",
    "searchType": "all",
    "regions": "0",
    "jobTypes": "0",
    "days": "0",
    "empType": "0",
    "catID": "0",
    "onlineApps": "0",
    "recruitmentCenterID": "0",
    "stateID": "0",
    "regionID": "0",
    "districtID": "0",
    "searchID": "0",
}

# Microsoft JSON date format: /Date(epoch_ms)/, e.g. /Date(1780444800000)/.
# Sentinel /Date(-62135568000000)/ == 0001-01-01 = "unset".
_MS_DATE_RE = re.compile(r"^/Date\((-?\d+)\)/$")
_MS_DATE_SENTINEL = -62135568000000

# Salary regex pack (recon §11.5). Patterns tried in priority order; first
# match wins. Monthly → as-is; annual → ÷ 12; hourly / placement / "dependent"
# → (None, None) and let the salary-floor filter handle.
_DASH = r"[-–]"
_AMOUNT = r"\$([\d,]+(?:\.\d{1,2})?)"

_SALARY_PATTERNS: List[Tuple[str, "re.Pattern[str]", str]] = [
    ("monthly_range",
     re.compile(rf"{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*per\s*month", re.IGNORECASE),
     "both_monthly"),
    ("annual_range",
     re.compile(
         rf"{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*(?:per\s*year|annually|annual)",
         re.IGNORECASE),
     "both_annual"),
    ("hourly_range",
     re.compile(rf"{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*per\s*hour", re.IGNORECASE),
     "none"),
    ("placement_schedule",
     re.compile(r"Placement on .* Salary Schedule", re.IGNORECASE),
     "none"),
    ("dependent_on_experience",
     re.compile(r"Pay dependent on experience", re.IGNORECASE),
     "none"),
]

_TELEWORK_KEYWORDS = ("remote", "virtual", "hybrid", "telework")


# --- Pure parsers (no I/O) -------------------------------------------------

def parse_search_results(json_text: str) -> List[Dict[str, Any]]:
    """Decode a LoadJobs JSON response into a list of per-record dicts.

    Preserves the listing-API fields needed downstream by `parse_posting`'s
    precedence chain. Missing optional fields are kept as None so the parse
    side can detect absence and fall back to JSON-LD / DOM per recon §11.6.
    """
    payload = json.loads(json_text)
    data = payload.get("data") or []
    out: List[Dict[str, Any]] = []
    for rec in data:
        out.append({
            "postingID": rec.get("postingID"),
            "positionTitle": rec.get("positionTitle"),
            "districtName": rec.get("districtName"),
            "city": rec.get("city"),
            "countyName": rec.get("countyName"),
            "postingDate": rec.get("postingDate"),
            "PayRangeFrom": rec.get("PayRangeFrom"),
            "PayRangeTo": rec.get("PayRangeTo"),
            "beginningSalary": rec.get("beginningSalary"),
            "endingSalary": rec.get("endingSalary"),
            "salaryInfo": rec.get("salaryInfo"),
            "JobSummary": rec.get("JobSummary"),
            "jobType": rec.get("jobType"),
            "FullTimePartTime": rec.get("FullTimePartTime"),
        })
    return out


def _parse_microsoft_json_date(value: Any) -> Optional[date]:
    """Parse Microsoft JSON date format `/Date(epoch_ms)/` to a date.

    Returns None on the sentinel `/Date(-62135568000000)/` (= 0001-01-01,
    "unset"), and None on any parse failure.
    """
    if not isinstance(value, str) or not value:
        return None
    m = _MS_DATE_RE.match(value)
    if not m:
        return None
    try:
        ts_ms = int(m.group(1))
    except ValueError:
        return None
    if ts_ms == _MS_DATE_SENTINEL:
        return None
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def _parse_jsonld_date(value: Any) -> Optional[date]:
    """Parse schema.org JSON-LD ISO 8601 datetime (e.g. `2026-06-03T07:00:00Z`)
    to a date. Returns None on missing/malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _to_int(s: str) -> int:
    return int(float(s.replace(",", "")))


def _parse_salary(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Walk the salary-pattern pack in priority order; first match wins.
    Monthly stays as-is; annual ÷ 12 to monthly; hourly / placement / pay-
    dependent / no-match all return (None, None) and defer to salary-floor.
    """
    if not text:
        return None, None
    for _name, pat, mode in _SALARY_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if mode == "both_monthly":
            return _to_int(m.group(1)), _to_int(m.group(2))
        if mode == "both_annual":
            return _to_int(m.group(1)) // 12, _to_int(m.group(2)) // 12
        if mode == "none":
            return None, None
    return None, None


def _extract_jsonld_jobposting(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """Find the schema.org JobPosting JSON-LD block on a detail page.
    Returns None if missing, unparseable, or @type doesn't match."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _telework_from_text(raw_text: str) -> bool:
    """Case-insensitive substring scan of raw_text for telework signals.
    EdJoin has no structured telework field (recon §11.9)."""
    if not raw_text:
        return False
    low = raw_text.lower()
    return any(k in low for k in _TELEWORK_KEYWORDS)


def _build_raw_text(soup: BeautifulSoup) -> str:
    """Concatenate visible labeled sections from a detail page for keyword
    scoring. Includes:

    - `<h5 class="botspace">` short labeled fields (Salary, Location, Employment Type…)
    - `<h3 class="printHide">` body sections (Job Summary, Requirements / Qualifications)

    Skips `<h3 class="printShow">` print-only duplicates and chrome `<h3>`
    elements (modal titles, page headers).
    """
    parts: List[str] = []

    # Short labeled fields
    for h5 in soup.find_all("h5", class_="botspace"):
        label = h5.get_text(strip=True)
        if not label:
            continue
        sib = h5.find_next_sibling("div")
        if sib is None:
            continue
        val = sib.get_text(" ", strip=True)
        if val:
            parts.append(f"{label}: {val}")

    # Body sections (skip printShow duplicates and unrelated h3 chrome)
    for h3 in soup.find_all("h3"):
        classes = h3.get("class") or []
        if "printShow" in classes:
            continue
        if "printHide" not in classes and "botspace" not in classes:
            continue
        label = h3.get_text(strip=True)
        if not label:
            continue
        chunks: List[str] = []
        for sib in h3.next_siblings:
            name = getattr(sib, "name", None)
            if name in ("h2", "h3"):
                # `<h3 class="printShow">` is the immediately-following print-
                # only duplicate of the same heading — skip it and keep
                # collecting body siblings beyond. Any other h2/h3 marks a
                # real section break — stop.
                sib_classes = sib.get("class") if hasattr(sib, "get") else None
                if sib_classes and "printShow" in sib_classes:
                    continue
                break
            if hasattr(sib, "get_text"):
                text = sib.get_text(" ", strip=True)
            else:
                text = str(sib).strip()
            if text:
                chunks.append(text)
        body = " ".join(chunks).strip()
        if body:
            parts.append(f"=== {label} ===\n{body}")

    return "\n\n".join(parts).strip()


# --- Field precedence resolvers (recon §11.6) ------------------------------

def _resolve_title(listing: Dict[str, Any], jsonld: Dict[str, Any],
                   soup: BeautifulSoup) -> str:
    """listing-API positionTitle → JSON-LD title → DOM <h2> last resort."""
    val = listing.get("positionTitle")
    if val:
        return str(val).strip()
    val = jsonld.get("title")
    if val:
        return str(val).strip()
    h2 = soup.find("h2")
    return h2.get_text(strip=True) if h2 else ""


def _resolve_source_job_id(listing: Dict[str, Any],
                           jsonld: Dict[str, Any]) -> str:
    pid = listing.get("postingID")
    if pid is not None:
        return str(pid)
    ident = jsonld.get("identifier") or {}
    if isinstance(ident, dict):
        v = ident.get("value")
        if v is not None:
            return str(v)
    return ""


def _resolve_employer(listing: Dict[str, Any],
                      jsonld: Dict[str, Any]) -> str:
    val = listing.get("districtName")
    if val:
        return str(val).strip()
    org = jsonld.get("hiringOrganization") or {}
    if isinstance(org, dict):
        name = org.get("name")
        if name:
            return str(name).strip()
    return "EdJoin"


def _resolve_posted_date(listing: Dict[str, Any],
                         jsonld: Dict[str, Any]) -> Optional[date]:
    d = _parse_microsoft_json_date(listing.get("postingDate"))
    if d is not None:
        return d
    return _parse_jsonld_date(jsonld.get("datePosted"))


def _resolve_salary(listing: Dict[str, Any], jsonld: Dict[str, Any],
                    raw_text: str) -> Tuple[Optional[int], Optional[int]]:
    """listing-API PayRangeFrom/To → JSON-LD baseSalary → regex on body."""
    lo = listing.get("PayRangeFrom")
    hi = listing.get("PayRangeTo")
    if lo and hi and str(lo).strip() and str(hi).strip():
        try:
            return _to_int(str(lo)), _to_int(str(hi))
        except (ValueError, TypeError):
            pass

    base = jsonld.get("baseSalary") or {}
    if isinstance(base, dict):
        val = base.get("value") or {}
        if isinstance(val, dict):
            minv = val.get("minValue")
            maxv = val.get("maxValue")
            if minv is not None and maxv is not None:
                try:
                    return int(float(minv)), int(float(maxv))
                except (ValueError, TypeError):
                    pass

    return _parse_salary(raw_text)


def _resolve_location(listing: Dict[str, Any]) -> Optional[str]:
    """Listing-API city + districtName. Single-element location for v1 per
    locked decision §11.6 / §10 item 8."""
    parts: List[str] = []
    city = listing.get("city")
    if city and str(city).strip():
        parts.append(str(city).strip())
    district = listing.get("districtName")
    if district and str(district).strip():
        parts.append(str(district).strip())
    if not parts:
        return None
    return ", ".join(parts)


# --- Top-level parser ------------------------------------------------------

def parse_posting(html_text: str, listing_fields: Dict[str, Any]) -> Posting:
    """Parse an EdJoin detail page into a Posting, following the locked
    field-precedence chain (recon §11.6). `listing_fields` comes from
    `parse_search_results` — the listing-API JSON, which is the first source
    for fields it covers.

    Raises nothing structural — missing pieces fall back through the chain
    and end up as empty/None values rather than exceptions.
    """
    soup = BeautifulSoup(html_text, "lxml")
    jsonld = _extract_jsonld_jobposting(soup) or {}

    source_job_id = _resolve_source_job_id(listing_fields, jsonld)
    title = _resolve_title(listing_fields, jsonld, soup)
    employer = _resolve_employer(listing_fields, jsonld)
    posted_date = _resolve_posted_date(listing_fields, jsonld)
    location = _resolve_location(listing_fields)
    raw_text = _build_raw_text(soup)
    telework_flag = _telework_from_text(raw_text)
    salary_min, salary_max = _resolve_salary(listing_fields, jsonld, raw_text)

    url = DETAIL_URL_TEMPLATE.format(posting_id=source_job_id) if source_job_id else ""

    return Posting(
        source="edjoin",
        source_job_id=source_job_id,
        title=title,
        employer=employer,
        url=url,
        raw_text=raw_text,
        classification=None,  # locked §11.6 — no class-code system
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        location=location,
        all_locations=[location] if location else None,
        telework_flag=telework_flag,
        posted_date=posted_date,
    )


# --- Source class ----------------------------------------------------------

class EdJoinSource(Source):
    """EdJoin Careers source. JSON API for listings, light HTML parse for
    detail-page body and salary. Pagination loops until LoadJobs returns
    fewer than `rows=50` records OR the 20-page safety cap is hit (locked
    §11.7). 2s throttle before every HTTP request after the first.

    One instance per run. Throttle state is on the instance — re-using a
    source across runs would still throttle correctly but the state isn't
    intended for that.
    """

    name = "edjoin"
    delay_seconds = 2.0

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        request_timeout: float = 30.0,
    ):
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._nav_count = 0
        self._headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/",
        }

    def _throttle(self) -> None:
        """Sleep `delay_seconds` before every request after the first."""
        if self._nav_count > 0:
            time.sleep(self.delay_seconds)
        self._nav_count += 1

    def _query_to_url(self, query: Dict[str, Any]) -> str:
        """Merge query-config with LoadJobs defaults and URL-encode.
        All defaults must be present per recon §11.2 (.NET 500s on missing
        params). Query-config values override defaults; `page` is injected
        by the pagination loop."""
        merged: Dict[str, Any] = {**_LOADJOBS_DEFAULTS, **query}
        return f"{LOADJOBS_URL}?{urlencode(merged)}"

    def _fetch_listings_page(
        self, query_url: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Fetch one LoadJobs page. Returns (records, totalRecords).
        Per-page failure → ([], 0) + log warning, don't kill the run."""
        try:
            resp = self._session.get(
                query_url, headers=self._headers, timeout=self._timeout
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("edjoin: search request failed: %s; url=%s", e, query_url)
            return [], 0
        try:
            records = parse_search_results(resp.text)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            log.warning("edjoin: search-result parse failed: %s", e)
            return [], 0
        try:
            payload = json.loads(resp.text)
            total = int(payload.get("totalRecords") or 0)
        except (json.JSONDecodeError, ValueError, TypeError):
            total = 0
        return records, total

    def _fetch_all_listings(
        self, base_query: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Paginate one LoadJobs query. Stops when a page returns fewer than
        `rows=50` records OR the 20-page safety cap (recon §11.7)."""
        out: List[Dict[str, Any]] = []
        for page in range(1, PAGE_SAFETY_CAP + 1):
            self._throttle()
            url = self._query_to_url({**base_query, "page": page})
            records, _total = self._fetch_listings_page(url)
            out.extend(records)
            if len(records) < ROWS_PER_PAGE:
                break
        else:
            log.warning(
                "edjoin: hit %d-page safety cap on query %r", PAGE_SAFETY_CAP, base_query
            )
        return out

    def _fetch_detail(self, posting_id: str) -> Optional[str]:
        """Fetch a posting detail page. Returns HTML text on success, None
        on request failure (logs warning)."""
        if not posting_id:
            return None
        self._throttle()
        url = DETAIL_URL_TEMPLATE.format(posting_id=posting_id)
        try:
            resp = self._session.get(
                url, headers=self._headers, timeout=self._timeout
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            log.warning(
                "edjoin: detail fetch failed for postingID=%s: %s", posting_id, e
            )
            return None

    def fetch_listings(
        self, queries: List[Dict[str, Any]]
    ) -> Iterator[Posting]:
        """Orchestrator. Paginated fetch per query; dedups by postingID
        across queries so a posting matching Lane 2 + Lane 4 gets detail-
        fetched and yielded once (locked §11.2). Per-posting parse failures
        log + continue.
        """
        seen: set = set()
        for query in queries:
            qlabel = _query_label(query)
            log.info("edjoin: searching %s", qlabel)
            records = self._fetch_all_listings(query)
            log.info(
                "edjoin: query %s yielded %d listing records",
                qlabel, len(records),
            )
            for rec in records:
                pid = str(rec.get("postingID") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)

                detail_html = self._fetch_detail(pid)
                if detail_html is None:
                    continue
                try:
                    yield parse_posting(detail_html, rec)
                except Exception:
                    log.exception(
                        "edjoin: parse_posting failed for postingID=%s; skipping",
                        pid,
                    )


def _query_label(query: Dict[str, Any]) -> str:
    kw = query.get("keywords")
    jt = query.get("jobTypes")
    regions = query.get("regions")
    bits = []
    if kw:
        bits.append(f"keywords={kw!r}")
    if jt and str(jt) != "0":
        bits.append(f"jobTypes={jt}")
    if regions and str(regions) != "0":
        bits.append(f"regions={regions}")
    return ", ".join(bits) if bits else "<defaults>"
