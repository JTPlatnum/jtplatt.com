"""CalCareers scraper (CA state jobs portal).

Uses headless Playwright Chromium for both search and detail pages: search
results render via DevExpress JS callbacks driven by window.location.hash, and
the detail print page populates `span#lbl*` fields via the same callback layer.
Plain `requests` returns empty spans for both — see notes/calcareers-recon.md
and the Phase 1 / Phase 2a probes.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml
from bs4 import BeautifulSoup

from crawler.base import Posting, Source

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CONFIG_PATH = REPO_ROOT / "data" / "sources.yaml"

SEARCH_URL = "https://calcareers.ca.gov/CalHRPublic/Search/AdvancedJobSearch.aspx"
DETAIL_PRINT_URL = "https://calcareers.ca.gov/CalHrPublic/Jobs/JobPostingPrint.aspx?jcid={jcid}"
WEB_VIEW_URL = "https://calcareers.ca.gov/CalHrPublic/Jobs/JobPosting.aspx?JobControlId={jcid}"

KEYWORD_INPUT_SELECTOR = "input[name='ctl00$cphMainContent$txtKeyword']"
SUBMIT_BUTTON_SELECTOR = "input[name='ctl00$cphMainContent$btnSearch']"
GRID_WAIT_SELECTOR = "a[href*='JobControlId='], a[href*='jcid='], tr.dxgvDataRow_"
JCID_HREF_SELECTOR = "a[href*='JobControlId='], a[href*='jcid=']"
WORKING_TITLE_SELECTOR = "span#lblWorkingTitle"

STUB_MARKER = "This Job Posting is no longer available"

NAV_TIMEOUT_MS = 30_000
GRID_WAIT_MS = 20_000
DETAIL_WAIT_MS = 20_000
NETWORKIDLE_WAIT_MS = 8_000

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

_JCID_HREF_RE = re.compile(r"(?:JobControlId|jcid)=(\d+)", re.IGNORECASE)

# Telework keyword fallback (matches structured-field-is-unreliable pattern
# from notes/usajobs-recon.md §4).
_TELEWORK_KEYWORDS = ("telework", "remote", "hybrid")

# Roman numeral tokens preserved during classification title-casing.
_ROMAN_NUMERALS = frozenset(
    {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
)

# Detail-page panels concatenated into Posting.raw_text. Order matches the
# visible page so a human reading raw_text sees the same flow.
#
# Minimum Requirements is not a panel on the print page — CalCareers punts that
# content out to the separate CalHR class-spec page (lblMinimumReqsInClassSpec
# links to it). Fetching the class spec is deferred to v1.1 (planned to land
# with Tier 2) so the keyword matcher can see those bullet points.
_RAW_TEXT_PANELS: List[Tuple[str, str]] = [
    ("Job Description and Duties", "pnlJobDescription"),
    ("Working Conditions", "pnlWorkingConditions"),
    ("Position Details", "pnlPositionDetails"),
    ("Department Information", "pnlDepartmentInfo"),
    ("Special Requirements", "pnlSpecialRequirements"),
    ("Desirable Qualifications", "pnlDesirableQualifications"),
]

# Salary line shape per probe: "$6,513.00 - $8,729.00 per Month".
_SALARY_MONTHLY_RE = re.compile(
    r"\$([\d,]+(?:\.\d{1,2})?)\s*-\s*\$([\d,]+(?:\.\d{1,2})?)\s*per\s*Month",
    re.IGNORECASE,
)


class CalCareersError(RuntimeError):
    pass


class CalCareersConfigError(RuntimeError):
    pass


# --- Parser helpers (pure functions; no Playwright, fully testable) --------

def _text(soup: BeautifulSoup, element_id: str) -> str:
    el = soup.find(id=element_id)
    if el is None:
        return ""
    return el.get_text(strip=True)


def _normalize_classification(s: str) -> str:
    """Title-case a classification string. Preserves trailing roman numerals so
    'INFORMATION TECHNOLOGY SPECIALIST II' -> 'Information Technology Specialist II',
    not 'Information Technology Specialist Ii'."""
    if not s:
        return s
    out = []
    for word in s.split():
        if word.upper() in _ROMAN_NUMERALS:
            out.append(word.upper())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def _parse_salary(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Return (monthly_min, monthly_max) as floats. Anything other than
    'per Month' -> (None, None) with a log line; CalCareers publishes ITS-style
    monthly bands but other classifications can be annual."""
    if not text:
        return None, None
    m = _SALARY_MONTHLY_RE.search(text)
    if not m:
        log.info("calcareers: salary not parseable as 'per Month': %r", text)
        return None, None
    lo = float(m.group(1).replace(",", ""))
    hi = float(m.group(2).replace(",", ""))
    return lo, hi


def _parse_telework(structured: str, raw_text: str) -> Optional[bool]:
    """Resolve telework boolean from lblTelework + raw_text fallback.
    'Yes'/'Hybrid' -> True. 'No' + keyword in raw_text -> True (description
    overrides structured field, matching the USAJobs convention). 'No' alone ->
    False. Empty/missing + keyword -> True. Empty/missing + no keyword -> None."""
    s = (structured or "").strip().lower()
    text = (raw_text or "").lower()
    has_kw = any(kw in text for kw in _TELEWORK_KEYWORDS)
    if s in ("yes", "hybrid"):
        return True
    if s == "no":
        return True if has_kw else False
    return True if has_kw else None


def _build_raw_text(soup: BeautifulSoup, classification: Optional[str]) -> str:
    parts: List[str] = []
    if classification:
        parts.append(f"=== Classification ===\n{classification}")
    for label, panel_id in _RAW_TEXT_PANELS:
        el = soup.find(id=panel_id)
        if el is None:
            continue
        text = el.get_text(" ", strip=True)
        if text:
            parts.append(f"=== {label} ===\n{text}")
    return "\n\n".join(parts)


def parse_posting(html: str, jcid: int, source_name: str = "calcareers") -> Posting:
    """Parse JobPostingPrint.aspx rendered HTML -> Posting. Pure function.

    Args:
        html: rendered HTML from the detail print page (post-JS span fills)
        jcid: integer JobControlId, used to construct the canonical web URL

    Returns:
        Posting with classification populated from `span#lblPrimaryClassification`.
    """
    soup = BeautifulSoup(html, "lxml")

    title = _text(soup, "lblWorkingTitle")
    classification_raw = _text(soup, "lblPrimaryClassification")
    classification = _normalize_classification(classification_raw) or None
    employer = _text(soup, "lblDepartmentName")
    location = _text(soup, "lblWorkLocation") or None
    source_job_id = _text(soup, "lblDetailsJobControlNumber")

    salary_min, salary_max = _parse_salary(_text(soup, "lblPrimarySalary"))

    raw_text = _build_raw_text(soup, classification)
    telework_flag = _parse_telework(_text(soup, "lblTelework"), raw_text)

    url = WEB_VIEW_URL.format(jcid=int(jcid))

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
        posted_date=None,
    )


# --- Config loading --------------------------------------------------------

def _load_config(path: Path = SOURCES_CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise CalCareersConfigError(f"sources.yaml not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    cfg = data.get("calcareers")
    if not cfg or not cfg.get("queries"):
        raise CalCareersConfigError("sources.yaml missing calcareers.queries")
    return cfg


# --- Source class ----------------------------------------------------------

class CalCareersSource(Source):
    """Playwright-driven CalCareers source. Spin-up-per-run: one headless
    Chromium for the whole fetch, closes at the end."""

    name = "calcareers"
    delay_seconds = 2.0

    def __init__(self, config_path: Path = SOURCES_CONFIG_PATH):
        self._config = _load_config(config_path)

    def fetch_listings(self) -> Iterator[Posting]:
        try:
            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )
        except ImportError as e:
            raise CalCareersError(
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

                seen_jcids: set[int] = set()
                detail_fetched = 0
                for query in queries:
                    keyword = query["keyword"]
                    log.info("calcareers: searching keyword=%r", keyword)
                    try:
                        jcids = self._fetch_jcids(page, keyword, PlaywrightTimeoutError)
                    except CalCareersError:
                        raise
                    log.info(
                        "calcareers: keyword=%r yielded %d jcids (page 1 only)",
                        keyword, len(jcids),
                    )
                    for jcid in jcids:
                        if jcid in seen_jcids:
                            continue
                        seen_jcids.add(jcid)
                        if detail_fetched > 0:
                            time.sleep(self.delay_seconds)
                        try:
                            posting = self._fetch_detail(page, jcid, PlaywrightTimeoutError)
                        except Exception:
                            log.exception(
                                "calcareers: detail fetch/parse failed for jcid=%d; skipping",
                                jcid,
                            )
                            detail_fetched += 1
                            continue
                        detail_fetched += 1
                        if posting is not None:
                            postings.append(posting)
            finally:
                browser.close()

        yield from postings

    def _fetch_jcids(
        self,
        page,
        keyword: str,
        TimeoutError_,
    ) -> List[int]:
        """Drive the AdvancedJobSearch form for one keyword. Returns page-1
        JobControlIds. Pagination deferred until results consistently exceed
        10/query — re-evaluate after a week of runs."""
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        kw = page.locator(KEYWORD_INPUT_SELECTOR)
        kw.wait_for(state="visible", timeout=10_000)
        kw.fill(keyword)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS):
            page.locator(SUBMIT_BUTTON_SELECTOR).click()
        try:
            page.wait_for_selector(GRID_WAIT_SELECTOR, state="attached", timeout=GRID_WAIT_MS)
        except TimeoutError_ as e:
            raise CalCareersError(
                f"search grid never populated for keyword={keyword!r}"
            ) from e
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_WAIT_MS)
        except Exception:
            pass

        hrefs = page.eval_on_selector_all(
            JCID_HREF_SELECTOR,
            "els => els.map(e => e.getAttribute('href'))",
        )
        jcids: List[int] = []
        seen: set[int] = set()
        for href in hrefs:
            m = _JCID_HREF_RE.search(href or "")
            if not m:
                continue
            jcid = int(m.group(1))
            if jcid in seen:
                continue
            seen.add(jcid)
            jcids.append(jcid)
        return jcids

    def _fetch_detail(self, page, jcid: int, TimeoutError_) -> Optional[Posting]:
        url = DETAIL_PRINT_URL.format(jcid=jcid)
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                WORKING_TITLE_SELECTOR, state="visible", timeout=DETAIL_WAIT_MS
            )
        except TimeoutError_:
            # Could be the 'no longer available' stub — check before raising.
            content = page.content()
            if STUB_MARKER in content:
                log.info(
                    "calcareers: jcid=%d is the 'no longer available' stub; skipping",
                    jcid,
                )
                return None
            raise

        content = page.content()
        if STUB_MARKER in content:
            log.info(
                "calcareers: jcid=%d is the 'no longer available' stub; skipping",
                jcid,
            )
            return None
        return parse_posting(content, jcid=jcid, source_name=self.name)
