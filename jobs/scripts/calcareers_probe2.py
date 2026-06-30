"""Phase-1B: second-POST attempt. After AdvancedJobSearch POST lands on
JobSearchResults.aspx with empty grid, try to emulate the browser-JS step that
fills hdnSearchCriteria from window.location.hash and triggers the callback.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE = "https://calcareers.ca.gov"
SEARCH_URL = f"{BASE}/CalHRPublic/Search/AdvancedJobSearch.aspx"
RESULTS_URL = f"{BASE}/CalHRPublic/Search/JobSearchResults.aspx"

KEYWORD = "Information Technology Specialist"
DELAY = 2.0
HEADERS = {
    "User-Agent": "jobs.jtplatt.com recon (James.T.Platt@gmail.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def hidden_fields(soup: BeautifulSoup) -> Dict[str, str]:
    return {
        inp.get("name"): inp.get("value", "")
        for inp in soup.select("input[type=hidden]")
        if inp.get("name")
    }


def main() -> int:
    s = requests.Session()
    s.headers.update(HEADERS)

    print("=== 1. GET AdvancedJobSearch.aspx")
    r1 = s.get(SEARCH_URL, timeout=30)
    print(f"   {r1.status_code} bytes={len(r1.content)}")
    payload = hidden_fields(BeautifulSoup(r1.text, "lxml"))
    payload["ctl00$cphMainContent$txtKeyword"] = KEYWORD
    payload["ctl00$cphMainContent$btnSearch"] = "Search Jobs"

    time.sleep(DELAY)

    print("=== 2. POST search; expect redirect to JobSearchResults.aspx#kw=...")
    r2 = s.post(
        SEARCH_URL, data=payload, timeout=30,
        headers={"Referer": SEARCH_URL, "Origin": BASE},
        allow_redirects=True,
    )
    print(f"   {r2.status_code} final_url={r2.url}")
    print(f"   bytes={len(r2.content)}")
    soup2 = BeautifulSoup(r2.text, "lxml")
    state2 = hidden_fields(soup2)
    print(f"   results-page hidden fields: {len(state2)}")
    print(f"   hdnSearchCriteria initially: {state2.get('ctl00$cphMainContent$hdnSearchCriteria')!r}")
    print(f"   hdnInit: {state2.get('ctl00$cphMainContent$hdnInit')!r}")

    # The hash fragment IS the search criteria payload. Fill hdnSearchCriteria
    # exactly like the page's JS would.
    criteria = f"kw={KEYWORD}".replace(" ", "%20")
    state2["ctl00$cphMainContent$hdnSearchCriteria"] = criteria
    state2["ctl00$cphMainContent$hdnInit"] = "true"
    # Best guess for the postback target: empty (regular form post) or the
    # search criteria handler. Try empty first.
    state2["__EVENTTARGET"] = state2.get("__EVENTTARGET", "")
    state2["__EVENTARGUMENT"] = state2.get("__EVENTARGUMENT", "")

    time.sleep(DELAY)

    print("\n=== 3. Second POST to JobSearchResults.aspx with hdnSearchCriteria filled")
    r3 = s.post(
        RESULTS_URL, data=state2, timeout=30,
        headers={"Referer": r2.url, "Origin": BASE},
        allow_redirects=True,
    )
    print(f"   {r3.status_code} final_url={r3.url}")
    print(f"   bytes={len(r3.content)}")

    # Hunt for any sign of results
    body3 = r3.text
    soup3 = BeautifulSoup(body3, "lxml")

    # 1) jcid anchors
    jcids = set()
    for a in soup3.find_all("a", href=True):
        href = a["href"]
        for sep in ("JobControlId=", "jcid="):
            if sep in href:
                v = href.split(sep, 1)[1].split("&", 1)[0]
                if v.isdigit():
                    jcids.add(v)
    print(f"   jcid anchors in second-POST response: {len(jcids)}")
    if jcids:
        print(f"   first few: {sorted(list(jcids))[:8]}")

    # 2) job title cells (DevExpress grid rows have classes like dxgvDataRow)
    grid_rows = soup3.select(".dxgvDataRow_") + soup3.select(".dxgvDataRow") + soup3.select("tr.dxgvDataRow")
    print(f"   DevExpress grid rows (dxgvDataRow*): {len(grid_rows)}")

    # 3) any 'Information Technology Specialist' text in body?
    cnt = body3.lower().count("information technology specialist")
    print(f"   'information technology specialist' occurrences: {cnt}")

    # 4) result count text
    for needle in ("jobs found", "results found", "No matching", "No results", "of 1", "of 0"):
        idx = body3.lower().find(needle.lower())
        if idx >= 0:
            ctx = body3[max(0, idx-50): idx+120]
            print(f"   '{needle}' @ {idx}: …{ctx!r}…")

    diag = REPO_ROOT / "tests" / "fixtures" / "calcareers_results_post2.html"
    diag.write_text(body3, encoding="utf-8")
    print(f"\n   saved diagnostic: {diag.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
