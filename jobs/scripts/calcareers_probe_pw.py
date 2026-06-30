"""THROWAWAY CalCareers Phase-2a Playwright probe. Do NOT commit.

Headless Chromium drives the real JS, so the JobSearchResults
hash → hdnSearchCriteria → DevExpress callback happens naturally. We capture
live JobControlIds and one live detail-page HTML to verify recon §2 mapping.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "calcareers_sample.html"

SEARCH_URL = "https://calcareers.ca.gov/CalHRPublic/Search/AdvancedJobSearch.aspx"
DETAIL_PRINT = "https://calcareers.ca.gov/CalHrPublic/Jobs/JobPostingPrint.aspx?jcid={jcid}"

KEYWORD = "Information Technology Specialist"
NAV_TIMEOUT_MS = 30_000
WAIT_GRID_MS = 20_000

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def step(msg: str) -> None:
    print(f"\n=== {msg}")


def extract_jcids(page: Page) -> list[str]:
    """Pull JobControlIds from any anchor referencing the standard URLs."""
    hrefs = page.eval_on_selector_all(
        "a[href*='JobControlId='], a[href*='jcid=']",
        "els => els.map(e => e.getAttribute('href'))",
    )
    jcids: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        m = re.search(r"(?:JobControlId|jcid)=(\d+)", h or "")
        if not m:
            continue
        jcid = m.group(1)
        if jcid in seen:
            continue
        seen.add(jcid)
        jcids.append(jcid)
    return jcids


def run(p) -> int:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    try:
        step("1. GET AdvancedJobSearch.aspx")
        resp = page.goto(SEARCH_URL, wait_until="domcontentloaded")
        print(f"   status={resp.status if resp else '???'} url={page.url}")

        step("2. Fill keyword and click 'Search Jobs'")
        kw_locator = page.locator("input[name='ctl00$cphMainContent$txtKeyword']")
        kw_locator.wait_for(state="visible", timeout=10_000)
        kw_locator.fill(KEYWORD)
        # Click and wait for navigation to JobSearchResults.aspx
        with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS):
            page.locator("input[name='ctl00$cphMainContent$btnSearch']").click()
        print(f"   navigated to: {page.url}")

        step("3. Wait for the grid to populate (jcid anchors OR DevExpress rows)")
        try:
            page.wait_for_selector(
                "a[href*='JobControlId='], a[href*='jcid='], tr.dxgvDataRow_",
                state="attached",
                timeout=WAIT_GRID_MS,
            )
            print("   grid populated.")
        except Exception as e:
            print(f"   STOP: grid never populated within {WAIT_GRID_MS} ms: {e}")
            # Diagnostic dump for inspection
            diag = REPO_ROOT / "tests" / "fixtures" / "calcareers_pw_empty.html"
            diag.write_text(page.content(), encoding="utf-8")
            print(f"   saved diagnostic: {diag.relative_to(REPO_ROOT)}")
            return 1

        # Brief settle so any AJAX paging-init finishes
        page.wait_for_load_state("networkidle", timeout=10_000)

        jcids = extract_jcids(page)
        print(f"   live JobControlIds found: {len(jcids)}")
        if jcids:
            print(f"   first 10: {jcids[:10]}")
        if not jcids:
            print("   STOP: zero jcids parsed from populated grid")
            return 2

        step("4. Fetch one live detail page (print view)")
        # Try jcids in order until one isn't the 'no longer available' stub.
        target_url = None
        target_html = None
        for jcid in jcids[:6]:
            url = DETAIL_PRINT.format(jcid=jcid)
            print(f"   trying jcid={jcid} → {url}")
            r = page.goto(url, wait_until="domcontentloaded")
            html = page.content()
            if "no longer available" not in html.lower():
                target_url = url
                target_html = html
                print(f"   live posting at jcid={jcid} ({len(html):,} bytes)")
                break
            print(f"   jcid={jcid} returned the 'no longer available' stub")
        if not target_html:
            print("   STOP: all sampled jcids returned the stub")
            return 3

        step("5. Save fixture")
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(target_html, encoding="utf-8")
        print(f"   saved: {FIXTURE.relative_to(REPO_ROOT)}")
        print(f"   source url: {target_url}")

        step("6. Live mapping snapshot from rendered DOM")
        title = page.title()
        print(f"   document.title: {title!r}")

        # Use page locators to pull text, since the DOM is already rendered.
        # Heading scan:
        headings = page.eval_on_selector_all(
            "h1, h2, h3",
            "els => els.slice(0, 12).map(e => ({tag: e.tagName, text: (e.innerText || '').trim().slice(0, 100)}))",
        )
        print("   headings (first 12):")
        for h in headings:
            print(f"     <{h['tag'].lower()}>: {h['text']!r}")

        # Body text excerpt for cue scan
        body_text = page.evaluate("() => document.body.innerText")
        text = re.sub(r"[ \t]+", " ", body_text)

        def show_cue(label: str, needles: list[str]) -> None:
            for n in needles:
                idx = text.find(n)
                if idx >= 0:
                    ctx = text[max(0, idx - 40): idx + 160].replace("\n", " ⏎ ")
                    print(f"   {label} cue ({n!r}): …{ctx!r}…")
                    return
            print(f"   {label}: NO CUE FOUND for {needles!r}")

        show_cue("salary", ["per month", "per year", "$", "Salary"])
        show_cue("telework", ["Telework", "telework", "Hybrid", "Remote"])
        show_cue("department", ["Department:", "Department ", "Working Title", "Classification"])
        show_cue("location", ["City:", "County:", "Work Location", "Location:"])
        show_cue("dates", ["Final Filing", "Date Posted", "Posted", "Posting Date"])

        # Look for the labels recon expected
        labels_to_check = [
            "Working Title", "Classification", "Department", "Job Code", "Position Number",
            "Working Hours", "Final Filing Date", "Posting Type", "City", "County",
            "Telework", "Position Details", "Job Description and Duties",
            "Special Requirements", "Salary Information",
        ]
        print("\n   recon §2 label presence check:")
        for lbl in labels_to_check:
            present = lbl in text
            print(f"     {('YES' if present else ' no'):>3}  {lbl!r}")

        # Where does jobcontrolid live in detail HTML? Source-of-truth check.
        m = re.search(r"jcid=(\d+)|JobControlId=(\d+)", target_url)
        if m:
            jcid_from_url = m.group(1) or m.group(2)
            in_body = jcid_from_url in text
            print(f"\n   source_job_id from URL: {jcid_from_url} (appears in body text: {in_body})")

        print("\nProbe complete.")
        return 0
    finally:
        context.close()
        browser.close()


def main() -> int:
    with sync_playwright() as p:
        return run(p)


if __name__ == "__main__":
    sys.exit(main())
