"""THROWAWAY CalCareers Phase-1 probe. Do NOT commit.

Goal: confirm ViewState POST works from `requests` and verify recon §2 mapping
against a real live posting. Stops at the first sign of DevExpress callback
gymnastics — that's JT's requests-vs-Playwright decision, not ours.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "calcareers_sample.html"

BASE = "https://calcareers.ca.gov"
SEARCH_URL = f"{BASE}/CalHRPublic/Search/AdvancedJobSearch.aspx"
RESULTS_URL = f"{BASE}/CalHRPublic/Search/JobSearchResults.aspx"
DETAIL_PRINT = f"{BASE}/CalHrPublic/Jobs/JobPostingPrint.aspx"

DELAY = 2.0
TIMEOUT = 30.0

HEADERS = {
    "User-Agent": "jobs.jtplatt.com recon (James.T.Platt@gmail.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def step(msg: str) -> None:
    print(f"\n=== {msg}")


def get_hidden_fields(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for inp in soup.select("input[type=hidden]"):
        name = inp.get("name")
        if not name:
            continue
        out[name] = inp.get("value", "")
    return out


def find_keyword_field(soup: BeautifulSoup) -> Optional[str]:
    """Search the form for a visible text input near a label like 'Keyword'."""
    # First, find any input whose ID/name contains 'Keyword' (case-insensitive).
    for inp in soup.find_all("input"):
        name = inp.get("name") or ""
        iid = inp.get("id") or ""
        if "keyword" in name.lower() or "keyword" in iid.lower():
            if inp.get("type") in (None, "text"):
                return name
    return None


def list_named_inputs(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Return all named form inputs with type + visible label hint."""
    rows: List[Dict[str, str]] = []
    for inp in soup.find_all(["input", "select", "textarea"]):
        name = inp.get("name")
        if not name:
            continue
        iid = inp.get("id") or ""
        rows.append({
            "name": name,
            "id": iid,
            "type": inp.get("type") or inp.name,
            "value": (inp.get("value") or "")[:40],
        })
    return rows


def main() -> int:
    session = requests.Session()
    session.headers.update(HEADERS)

    step("1. GET advanced search form")
    r = session.get(SEARCH_URL, timeout=TIMEOUT)
    print(f"   status={r.status_code} bytes={len(r.content)}")
    if r.status_code != 200:
        print(f"   body[:400]: {r.text[:400]}")
        return 1

    soup = BeautifulSoup(r.text, "lxml")
    hidden = get_hidden_fields(soup)
    print(f"   hidden field count: {len(hidden)}")
    for name in sorted(hidden):
        v = hidden[name]
        snippet = (v[:50] + "…") if len(v) > 50 else v
        print(f"     {name}  =  {snippet!r}")

    keyword_field = find_keyword_field(soup)
    print(f"\n   keyword field name: {keyword_field!r}")

    # Find the submit button name
    submit_buttons = [b for b in soup.find_all("input") if b.get("type") == "submit"]
    print(f"   submit-type inputs: {len(submit_buttons)}")
    for b in submit_buttons:
        print(f"     name={b.get('name')!r} value={b.get('value')!r}")

    # Also dump anything with 'btn' or 'search' in id/name
    print("\n   text/select inputs in form (preview):")
    seen = 0
    for row in list_named_inputs(soup):
        # Skip the noisy DevExpress internal hidden states
        if row["name"].startswith("__"):
            continue
        if any(s in row["name"].lower() for s in ("keyword", "jobcode", "btn", "ddl", "txt")):
            print(f"     {row['type']:<6} {row['name']:<70} id={row['id']}")
            seen += 1
            if seen >= 40:
                break

    if not keyword_field:
        print("\n   STOP: could not locate the Keyword text input. Manual browser-inspection needed.")
        return 2

    time.sleep(DELAY)

    step("2. POST a keyword search ('Information Technology Specialist')")
    payload = dict(hidden)
    payload[keyword_field] = "Information Technology Specialist"
    # The documented main search button (recon §1). Avoid the unrelated
    # ucUtilityHeader1$btnGoogleSiteSearch which would redirect to SiteSearch.
    submit_name = "ctl00$cphMainContent$btnSearch"
    submit_value = "Search Jobs"
    for b in submit_buttons:
        if b.get("name") == submit_name:
            submit_value = b.get("value") or submit_value
            break
    payload[submit_name] = submit_value
    print(f"   submit field used: {submit_name!r} (value={submit_value!r})")

    r2 = session.post(
        SEARCH_URL,
        data=payload,
        timeout=TIMEOUT,
        allow_redirects=True,
        headers={"Referer": SEARCH_URL, "Origin": BASE},
    )
    print(f"   status={r2.status_code} final_url={r2.url}")
    print(f"   bytes={len(r2.content)}")

    if r2.status_code != 200:
        print(f"   STOP: POST returned non-200")
        print(f"   body[:600]: {r2.text[:600]}")
        return 3

    step("3. Extract JobControlIds from results page")
    soup2 = BeautifulSoup(r2.text, "lxml")
    # Hunt for any anchor referencing JobControlId or jcid
    jcids: List[str] = []
    for a in soup2.find_all("a", href=True):
        href = a["href"]
        if "JobControlId=" in href:
            jcid = href.split("JobControlId=", 1)[1].split("&", 1)[0]
            if jcid.isdigit() and jcid not in jcids:
                jcids.append(jcid)
        elif "jcid=" in href:
            jcid = href.split("jcid=", 1)[1].split("&", 1)[0]
            if jcid.isdigit() and jcid not in jcids:
                jcids.append(jcid)
    print(f"   JobControlIds found: {len(jcids)}")
    if jcids:
        print(f"   first 10: {jcids[:10]}")
    else:
        # Maybe the results landed on JobSearchResults.aspx via redirect with no parse
        print("   STOP: zero job IDs parsed from response")
        print(f"   results URL was {r2.url}")
        print(f"   body[:1500]: {r2.text[:1500]}")
        # Save the response for diagnosis
        diag = REPO_ROOT / "tests" / "fixtures" / "calcareers_results_debug.html"
        diag.parent.mkdir(parents=True, exist_ok=True)
        diag.write_text(r2.text, encoding="utf-8")
        print(f"   saved diagnostic: {diag}")
        return 4

    time.sleep(DELAY)

    step("4. Fetch ONE live detail page (print view)")
    jcid = jcids[0]
    detail_url = f"{DETAIL_PRINT}?jcid={jcid}"
    r3 = session.get(detail_url, timeout=TIMEOUT)
    print(f"   url: {detail_url}")
    print(f"   status={r3.status_code} bytes={len(r3.content)}")

    body = r3.text
    if "no longer available" in body.lower():
        print(f"   WARN: got the 'no longer available' stub for jcid={jcid}. Try the next one.")
        for jcid in jcids[1:6]:
            time.sleep(DELAY)
            r3 = session.get(f"{DETAIL_PRINT}?jcid={jcid}", timeout=TIMEOUT)
            body = r3.text
            print(f"   try jcid={jcid}  status={r3.status_code} bytes={len(r3.content)}")
            if "no longer available" not in body.lower():
                print(f"   got a live posting at jcid={jcid}")
                break
        else:
            print("   STOP: all sampled jcids returned the 'no longer available' stub")
            return 5

    step("5. Save fixture")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(body, encoding="utf-8")
    print(f"   saved: {FIXTURE.relative_to(REPO_ROOT)}  ({len(body):,} bytes)")

    step("6. Quick mapping snapshot")
    soup3 = BeautifulSoup(body, "lxml")
    title_tag = soup3.find("title")
    h1 = soup3.find("h1")
    print(f"   <title>: {title_tag.get_text(strip=True) if title_tag else None!r}")
    print(f"   first <h1>: {h1.get_text(strip=True) if h1 else None!r}")

    h_tags = soup3.find_all(["h1", "h2", "h3"])
    print(f"   heading tags: {len(h_tags)}")
    for h in h_tags[:8]:
        print(f"     <{h.name}>: {h.get_text(' ', strip=True)[:90]!r}")

    # Search for salary text
    text = soup3.get_text(" ", strip=True)
    for needle in ("per month", "per year", "salary", "Salary"):
        idx = text.find(needle)
        if idx >= 0:
            ctx = text[max(0, idx - 60): idx + 80]
            print(f"   salary cue ({needle!r}): …{ctx!r}…")
            break

    for needle in ("Telework", "telework", "Hybrid", "remote"):
        idx = text.find(needle)
        if idx >= 0:
            ctx = text[max(0, idx - 40): idx + 60]
            print(f"   telework cue ({needle!r}): …{ctx!r}…")
            break

    # Look for the City / County labels recon expected
    for label in ("Working Title", "Classification", "Department", "Job Code", "Position Number",
                  "Working Hours", "Final Filing Date", "Posting Type", "City", "County",
                  "Telework", "Position Details"):
        if label in text:
            idx = text.find(label)
            ctx = text[max(0, idx - 5): idx + 120].replace("\n", " ")
            print(f"   label {label!r}: …{ctx[:140]!r}…")

    print("\nProbe complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
