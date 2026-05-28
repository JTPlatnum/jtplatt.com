"""USAJobs API client. See SPEC.md and notes/usajobs-recon.md."""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests
import yaml

from crawler.base import Posting, Source

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CONFIG_PATH = REPO_ROOT / "data" / "sources.yaml"

# Sections of UserArea.Details to concat into Posting.raw_text (recon §4).
# Excluded as boilerplate (low scoring signal): Evaluations, HowToApply,
# WhatToExpectNext, RequiredDocuments, Benefits.
_RAW_TEXT_SECTIONS = [
    ("Job Summary", "JobSummary"),
    ("Major Duties", "MajorDuties"),
    ("Requirements", "Requirements"),
    ("Education", "Education"),
    ("Other Information", "OtherInformation"),
]

# Telework keyword fallback (recon §4: structured flag is unreliable).
_TELEWORK_KEYWORDS = ("telework", "remote", "hybrid")


class USAJobsAuthError(RuntimeError):
    pass


class USAJobsConfigError(RuntimeError):
    pass


def _build_raw_text(mod: Dict[str, Any]) -> str:
    parts: List[str] = []
    qs = mod.get("QualificationSummary") or ""
    if qs:
        parts.append(f"=== Qualification Summary ===\n{qs}")
    details = (mod.get("UserArea") or {}).get("Details") or {}
    for label, key in _RAW_TEXT_SECTIONS:
        v = details.get(key)
        if not v:
            continue
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v)
        parts.append(f"=== {label} ===\n{v}")
    return "\n\n".join(parts)


def _strip_port(url: str) -> str:
    return url.replace(":443/", "/", 1) if url else url


def _parse_salary(mod: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """Lowest min / highest max across Per-Annum entries, monthly. Non-annual
    rates (PH=hourly, WC=without compensation, FB=fee basis) -> (None, None).
    """
    remun = mod.get("PositionRemuneration") or []
    pa = [r for r in remun if r.get("RateIntervalCode") == "PA"]
    if not pa:
        return None, None
    try:
        annual_min = min(float(r["MinimumRange"]) for r in pa)
        annual_max = max(float(r["MaximumRange"]) for r in pa)
    except (KeyError, ValueError, TypeError):
        return None, None
    return annual_min / 12.0, annual_max / 12.0


def _parse_locations(mod: Dict[str, Any]) -> tuple[Optional[str], Optional[List[str]]]:
    locs = mod.get("PositionLocation") or []
    all_locations = [l.get("LocationName") for l in locs if l.get("LocationName")]
    if not all_locations:
        return None, None
    if len(all_locations) == 1:
        return all_locations[0], all_locations
    display = mod.get("PositionLocationDisplay") or f"{len(all_locations)} locations"
    return display, all_locations


def _parse_telework(mod: Dict[str, Any], raw_text: str) -> Optional[bool]:
    details = (mod.get("UserArea") or {}).get("Details") or {}
    structured = details.get("TeleworkEligible")
    if structured is True:
        return True
    text = raw_text.lower()
    if any(kw in text for kw in _TELEWORK_KEYWORDS):
        return True
    # structured False with no keyword hit -> respect the structured False.
    # structured missing with no keyword hit -> None (unknown).
    if structured is False:
        return False
    return None


def _parse_posted_date(mod: Dict[str, Any]) -> Optional[date]:
    pub = mod.get("PublicationStartDate") or ""
    if len(pub) < 10:
        return None
    try:
        return date.fromisoformat(pub[:10])
    except ValueError:
        return None


def parse_posting(mod: Dict[str, Any], source_name: str = "usajobs") -> Posting:
    """USAJobs MatchedObjectDescriptor -> Posting. Mapping per recon §4."""
    pid = mod["PositionID"]
    title = mod.get("PositionTitle") or ""
    url = _strip_port(mod.get("PositionURI") or "")

    dept = mod.get("DepartmentName") or ""
    org = mod.get("OrganizationName") or ""
    employer = " / ".join(p for p in [dept, org] if p)

    salary_min, salary_max = _parse_salary(mod)
    location, all_locations = _parse_locations(mod)
    raw_text = _build_raw_text(mod)
    telework_flag = _parse_telework(mod, raw_text)
    posted_date = _parse_posted_date(mod)

    return Posting(
        source=source_name,
        source_job_id=pid,
        title=title,
        employer=employer,
        url=url,
        raw_text=raw_text,
        salary_min=salary_min,
        salary_max=salary_max,
        location=location,
        all_locations=all_locations,
        telework_flag=telework_flag,
        posted_date=posted_date,
    )


def _load_config(path: Path = SOURCES_CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise USAJobsConfigError(f"sources.yaml not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    usajobs = data.get("usajobs")
    if not usajobs or not usajobs.get("queries"):
        raise USAJobsConfigError("sources.yaml missing usajobs.queries")
    return usajobs


def _build_headers() -> Dict[str, str]:
    api_key = os.environ.get("USAJOBS_API_KEY")
    user_agent = os.environ.get("USAJOBS_USER_AGENT")
    if not api_key:
        raise USAJobsAuthError("USAJOBS_API_KEY not set in environment")
    if not user_agent:
        raise USAJobsAuthError("USAJOBS_USER_AGENT not set in environment")
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent,
        "Authorization-Key": api_key,
    }


def _build_params(query: Dict[str, Any], page: int) -> Dict[str, Any]:
    codes = query.get("job_category_codes") or []
    params: Dict[str, Any] = {
        "JobCategoryCode": ";".join(str(c) for c in codes),
        "ResultsPerPage": query.get("results_per_page", 250),
        "Page": page,
    }
    if query.get("location_name"):
        params["LocationName"] = query["location_name"]
    if query.get("radius") is not None:
        params["Radius"] = query["radius"]
    if query.get("who_may_apply"):
        params["WhoMayApply"] = query["who_may_apply"]
    if query.get("sort_field"):
        params["SortField"] = query["sort_field"]
    if query.get("sort_direction"):
        params["SortDirection"] = query["sort_direction"]
    if query.get("organization"):
        params["Organization"] = query["organization"]
    return params


class USAJobsSource(Source):
    """USAJobs Search API source. Reads query configs from data/sources.yaml,
    paginates each, dedups by PositionID across queries+pages."""

    name = "usajobs"
    delay_seconds = 2.0

    def __init__(
        self,
        config_path: Path = SOURCES_CONFIG_PATH,
        session: Optional[requests.Session] = None,
        request_timeout: float = 30.0,
    ):
        self._config = _load_config(config_path)
        self._headers = _build_headers()
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._api_base = self._config["api_base"]

    def fetch_listings(self) -> Iterator[Posting]:
        seen: set[str] = set()
        for query in self._config["queries"]:
            qname = query.get("name", "<unnamed>")
            yielded_query = 0
            for mod in self._fetch_query(query, qname):
                pid = mod.get("PositionID")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                try:
                    posting = parse_posting(mod, source_name=self.name)
                except Exception:
                    log.exception("usajobs: parse failed for %s; skipping", pid)
                    continue
                yielded_query += 1
                yield posting
            log.info("usajobs: query %s yielded %d new postings", qname, yielded_query)

    def _fetch_query(self, query: Dict[str, Any], qname: str) -> Iterator[Dict[str, Any]]:
        per_page = int(query.get("results_per_page", 250))
        page = 1
        first_request = True
        while True:
            if not first_request:
                time.sleep(self.delay_seconds)
            first_request = False
            params = _build_params(query, page)
            try:
                resp = self._session.get(
                    self._api_base,
                    headers=self._headers,
                    params=params,
                    timeout=self._timeout,
                )
            except requests.RequestException as e:
                log.error("usajobs: request error on query %s page %d: %s; skipping rest of query", qname, page, e)
                return
            if resp.status_code == 401:
                raise USAJobsAuthError("usajobs API returned 401 — check USAJOBS_API_KEY")
            if resp.status_code == 429 or resp.status_code >= 500:
                log.warning("usajobs: %d on query %s page %d; backing off and skipping rest of query", resp.status_code, qname, page)
                return
            if resp.status_code != 200:
                log.error("usajobs: unexpected status %d on query %s page %d: %s", resp.status_code, qname, page, resp.text[:300])
                return
            try:
                data = resp.json()
            except ValueError:
                log.error("usajobs: non-JSON response on query %s page %d", qname, page)
                return
            items = (data.get("SearchResult") or {}).get("SearchResultItems") or []
            for item in items:
                mod = item.get("MatchedObjectDescriptor")
                if mod:
                    yield mod
            if len(items) < per_page:
                return
            page += 1
