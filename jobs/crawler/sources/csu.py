"""CSU Careers scraper (California State University, 23 campuses + Chancellor's Office).

PageUp People ATS (tenant 873). Server-rendered HTML, GET-based search.
Uses **Playwright (headless Chromium)** for both search and detail pages —
csucareers.calstate.edu is fronted by CloudFront + AWS WAF with a JS-challenge
integration that trips at ~2 fetches/2s on automated `requests` traffic.
Playwright executes the WAF JS, obtains a token, and subsequent navigations
pass. See notes/csu-recon.md §5 (AWS WAF finding, 2026-06-02) and the locked
decisions there for the revised design.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml
from bs4 import BeautifulSoup

from crawler.base import Posting, Source

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CONFIG_PATH = REPO_ROOT / "data" / "sources.yaml"

BASE_URL = "https://csucareers.calstate.edu"
SEARCH_URL = f"{BASE_URL}/en-us/search/"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Selectors for Playwright waits.
SEARCH_RESULTS_SELECTOR = "a.job-link"
JOB_CONTENT_SELECTOR = "#job-content"

# Navigation / wait timeouts (milliseconds). Generous because the WAF JS
# challenge can add several seconds before the real page resolves.
NAV_TIMEOUT_MS = 45_000
SEARCH_WAIT_MS = 30_000
DETAIL_WAIT_MS = 30_000

_JOB_HREF_RE = re.compile(r"/en-us/job/(\d+)/")
_CLASSIFICATION_RE = re.compile(r"\(([^)]+)\)\s*$")

# Telework signal: CSU's structured telework lives in the comma-separated
# Categories string. No keyword-scan fallback per Decision #6.
_TELEWORK_PHRASES = [
    "telecommute eligible",
    "remote in-state eligible",
    "remote out-of-state eligible",
]


class CSUParseError(RuntimeError):
    pass


class CSUConfigError(RuntimeError):
    pass


# --- Salary patterns (priority order — Decision #5) ------------------------

_DASH = r"[-–—]"          # hyphen-minus, en-dash, em-dash
_AMOUNT = r"\$([\d,]+(?:\.\d{1,2})?)"

_SALARY_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    ("anticipated_monthly",
     re.compile(
         rf"Anticipated\s+Salary\s+Range:\s*{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*per\s*month",
         re.IGNORECASE),
     "both_monthly"),
    ("annual_range",
     re.compile(rf"{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*annually", re.IGNORECASE),
     "both_annual"),
    ("csu_classification_monthly",
     re.compile(
         rf"CSU\s+Classification\s+Salary\s+Range:\s*{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*per\s*month",
         re.IGNORECASE),
     "both_monthly"),
    ("step1_ceiling",
     re.compile(
         rf"Initial\s+step\s+placement\s+is\s+not\s+expected\s+to\s+exceed\s+Step\s*1\s*\({_AMOUNT}\s*/\s*month\)",
         re.IGNORECASE),
     "min_only_monthly"),
    ("hourly",
     re.compile(rf"{_AMOUNT}\s*{_DASH}\s*{_AMOUNT}\s*per\s*hour", re.IGNORECASE),
     "none"),
    ("commensurate",
     re.compile(r"Salary\s+commensurate\s+with\s+experience", re.IGNORECASE),
     "none"),
]


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_salary(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Return (monthly_min, monthly_max). Tries patterns in priority order;
    first match wins. Logs INFO on no-match so we can iterate the pack."""
    if not text:
        return None, None
    for name, pat, mode in _SALARY_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if mode == "both_monthly":
            return _to_float(m.group(1)), _to_float(m.group(2))
        if mode == "both_annual":
            return _to_float(m.group(1)) / 12.0, _to_float(m.group(2)) / 12.0
        if mode == "min_only_monthly":
            return _to_float(m.group(1)), None
        if mode == "none":
            return None, None
    log.info("csu: no salary pattern matched; first 200 chars: %r", text[:200])
    return None, None


# --- Field helpers (pure functions) ----------------------------------------

def _extract_classification(title: str) -> Optional[str]:
    if not title:
        return None
    m = _CLASSIFICATION_RE.search(title)
    return m.group(1).strip() if m else None


def _telework_from_categories(categories_text: str) -> bool:
    if not categories_text:
        return False
    low = categories_text.lower()
    return any(p in low for p in _TELEWORK_PHRASES)


def _parse_posted_date(time_el) -> Optional[date]:
    if time_el is None:
        return None
    dt = time_el.get("datetime", "")
    if not dt:
        return None
    try:
        return date.fromisoformat(dt[:10])
    except ValueError:
        return None


# --- Campus → employer (23 campuses + Chancellor's Office) -----------------

_CAMPUS_TO_EMPLOYER: Dict[str, str] = {
    "Bakersfield": "California State University, Bakersfield",
    "Cal Poly - San Luis Obispo Campus": "California Polytechnic State University, San Luis Obispo",
    "Cal Poly - Mustang Business Park (San Luis Obispo)": "California Polytechnic State University, San Luis Obispo",
    "Cal Poly - Solano Campus (Vallejo)": "California Polytechnic State University, San Luis Obispo",
    "Chancellor's Office": "CSU Chancellor's Office",
    "Chancellor's Office - Sacramento": "CSU Chancellor's Office",
    "Channel Islands": "California State University Channel Islands",
    "Chico": "California State University, Chico",
    "Dominguez Hills": "California State University, Dominguez Hills",
    "East Bay": "California State University, East Bay",
    "Fresno": "California State University, Fresno",
    "Fullerton": "California State University, Fullerton",
    "Humboldt": "Cal Poly Humboldt",
    "Long Beach": "California State University, Long Beach",
    "Los Angeles": "California State University, Los Angeles",
    "Maritime Academy": "California State University Maritime Academy",
    "Monterey Bay": "California State University, Monterey Bay",
    "Northridge": "California State University, Northridge",
    "Pomona": "California State Polytechnic University, Pomona",
    "Sacramento": "California State University, Sacramento",
    "San Bernardino - Palm Desert Campus": "California State University, San Bernardino",
    "San Bernardino - San Bernardino Campus": "California State University, San Bernardino",
    "San Diego": "San Diego State University",
    "San Diego - Imperial Valley": "San Diego State University - Imperial Valley",
    "San Francisco": "San Francisco State University",
    "San José": "San José State University",
    "San José - Moss Landing Marine Lab": "San José State University",
    "San Marcos": "California State University San Marcos",
    "Sonoma": "Sonoma State University",
    "Stanislaus - Stockton": "California State University, Stanislaus",
    "Stanislaus - Turlock": "California State University, Stanislaus",
}

_DEFAULT_EMPLOYER = "California State University"


def _campus_to_employer(location: Optional[str]) -> str:
    if not location:
        return _DEFAULT_EMPLOYER
    return _CAMPUS_TO_EMPLOYER.get(location.strip(), _DEFAULT_EMPLOYER)


# --- Raw text assembly -----------------------------------------------------

def _build_raw_text(
    classification: Optional[str], categories_text: str, body_text: str
) -> str:
    parts: List[str] = []
    if classification:
        parts.append(f"=== Classification ===\n{classification}")
    if categories_text:
        parts.append(f"=== Categories ===\n{categories_text}")
    if body_text:
        parts.append(f"=== Position Details ===\n{body_text}")
    return "\n\n".join(parts)


# --- Detail-page parser (pure) ---------------------------------------------

def parse_posting(html: str, *, url: str, source_name: str = "csu") -> Posting:
    """Parse a CSU posting detail page (under #job-content). Pure function.

    Raises CSUParseError if the #job-content wrapper or <h2> title is missing —
    those signal a PageUp template change that the caller should treat as a
    structural break, not a per-posting skip.
    """
    soup = BeautifulSoup(html, "lxml")
    content = soup.find(id="job-content")
    if content is None:
        raise CSUParseError("missing #job-content wrapper")

    h2 = content.find("h2")
    if h2 is None:
        raise CSUParseError("missing <h2> title inside #job-content")
    title = h2.get_text(strip=True)

    classification = _extract_classification(title)

    sid_span = content.find("span", class_="job-externalJobNo")
    source_job_id = sid_span.get_text(strip=True) if sid_span else ""

    loc_span = content.find("span", class_="location")
    location = loc_span.get_text(strip=True) if loc_span else None

    cat_span = content.find("span", class_="categories")
    categories_text = cat_span.get_text(strip=True) if cat_span else ""
    telework_flag = _telework_from_categories(categories_text)

    open_date_span = content.find("span", class_="open-date")
    time_el = open_date_span.find("time") if open_date_span else None
    posted_date = _parse_posted_date(time_el)

    details = content.find(id="job-details")
    body_text = details.get_text(" ", strip=True) if details else ""

    salary_min, salary_max = _parse_salary(body_text)
    employer = _campus_to_employer(location)
    raw_text = _build_raw_text(classification, categories_text, body_text)

    return Posting(
        source=source_name,
        source_job_id=source_job_id,
        title=title,
        employer=employer,
        url=url,
        raw_text=raw_text,
        classification=classification,
        salary_min=salary_min,
        salary_max=salary_max,
        location=location,
        all_locations=[location] if location else None,
        telework_flag=telework_flag,
        posted_date=posted_date,
    )


# --- Search-results parser (pure) ------------------------------------------

def parse_search_results(html: str) -> List[Tuple[str, str]]:
    """Extract unique (jcid, href) pairs from a search-results page. Preserves
    listing order; deduplicates by jcid because PageUp also injects the same
    posting into the 'Current opportunities' sidebar on each page."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: List[Tuple[str, str]] = []
    for a in soup.find_all("a", class_="job-link"):
        href = a.get("href", "")
        m = _JOB_HREF_RE.search(href)
        if not m:
            continue
        jcid = m.group(1)
        if jcid in seen:
            continue
        seen.add(jcid)
        out.append((jcid, href))
    return out


# --- Config loading --------------------------------------------------------

def _load_config(path: Path = SOURCES_CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise CSUConfigError(f"sources.yaml not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    cfg = data.get("csu")
    if not cfg or not cfg.get("queries"):
        raise CSUConfigError("sources.yaml missing csu.queries")
    return cfg


def _query_label(query: Dict[str, Any]) -> str:
    if "category" in query:
        return f"category={query['category']!r}"
    if "search_keyword" in query:
        return f"keyword={query['search_keyword']!r}"
    return "<unnamed>"


# --- Source class ----------------------------------------------------------

def _query_to_url(query: Dict[str, Any]) -> str:
    """Build the search-results URL with query-string params. Playwright's
    page.goto() takes a single URL; encoding params via urlencode preserves the
    `&` separator and percent-encodes spaces / `&` symbols inside values."""
    from urllib.parse import urlencode  # noqa: PLC0415
    pairs: List[Tuple[str, Any]] = [
        ("page-items", query.get("page_items", 50)),
        ("page", 1),
    ]
    if "category" in query:
        pairs.append(("category", query["category"]))
    if "search_keyword" in query:
        pairs.append(("search-keyword", query["search_keyword"]))
    if "work_type" in query:
        pairs.append(("work-type", query["work_type"]))
    return f"{SEARCH_URL}?{urlencode(pairs)}"


class CSUSource(Source):
    """CSU Careers source. Playwright (headless Chromium) drives both search
    and detail navigations to clear the AWS WAF JS challenge.

    Spin-up-per-run: one Chromium for the whole fetch, closes at the end.
    Page 1 only for v1; pagination is deferred until results consistently
    exceed page-items=50 (Decision #7).
    """

    name = "csu"
    delay_seconds = 2.0

    def __init__(self, config_path: Path = SOURCES_CONFIG_PATH):
        self._config = _load_config(config_path)

    def fetch_listings(self) -> Iterator[Posting]:
        try:
            from playwright.sync_api import (  # noqa: PLC0415
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )
        except ImportError as e:
            raise RuntimeError(
                "playwright not installed; pip install playwright && playwright install chromium"
            ) from e

        queries = self._config["queries"]
        postings: List[Posting] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=UA, viewport={"width": 1280, "height": 900}
                )
                page = context.new_page()
                page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

                seen: set[str] = set()
                nav_count = 0

                def _throttle() -> None:
                    nonlocal nav_count
                    if nav_count > 0:
                        time.sleep(self.delay_seconds)
                    nav_count += 1

                for query in queries:
                    qlabel = _query_label(query)
                    search_url = _query_to_url(query)
                    log.info("csu: searching %s", qlabel)
                    _throttle()
                    try:
                        jobs = self._fetch_search(
                            page, search_url, PlaywrightTimeoutError
                        )
                    except Exception:
                        log.exception(
                            "csu: search nav/parse failed for %s; skipping query",
                            qlabel,
                        )
                        continue
                    log.info(
                        "csu: query %s yielded %d unique jcids (page 1 only)",
                        qlabel, len(jobs),
                    )

                    for jcid, slug_href in jobs:
                        if jcid in seen:
                            continue
                        seen.add(jcid)
                        detail_url = f"{BASE_URL}{slug_href}"
                        _throttle()
                        try:
                            posting = self._fetch_detail(
                                page, detail_url, jcid, PlaywrightTimeoutError
                            )
                        except Exception:
                            log.exception(
                                "csu: detail fetch/parse failed for jcid=%s; skipping",
                                jcid,
                            )
                            continue
                        if posting is not None:
                            postings.append(posting)
            finally:
                browser.close()

        yield from postings

    def _fetch_search(
        self, page, search_url: str, TimeoutError_,
    ) -> List[Tuple[str, str]]:
        """Navigate to a search URL, wait for posting links to render, return
        the deduped (jcid, href) list."""
        page.goto(search_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                SEARCH_RESULTS_SELECTOR, state="attached", timeout=SEARCH_WAIT_MS
            )
        except TimeoutError_:
            # Empty result set OR WAF reload-loop. Capture content for parser to
            # decide; parse_search_results returns [] on missing anchors.
            log.warning("csu: search selector never appeared at %s", search_url)
        return parse_search_results(page.content())

    def _fetch_detail(
        self, page, detail_url: str, jcid: str, TimeoutError_,
    ) -> Optional[Posting]:
        """Navigate to a detail URL, wait for #job-content, parse to Posting."""
        page.goto(detail_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                JOB_CONTENT_SELECTOR, state="attached", timeout=DETAIL_WAIT_MS
            )
        except TimeoutError_:
            log.warning(
                "csu: #job-content never appeared for jcid=%s at %s",
                jcid, detail_url,
            )
            return None
        return parse_posting(page.content(), url=detail_url, source_name=self.name)
