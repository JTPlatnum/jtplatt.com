# CalCareers Reconnaissance

**Date:** 2026-05-27
**Goal:** Map the site before writing `crawler/sources/calcareers.py`. Findings here drive scraper architecture and inform every scraper that follows.
**Status:** Investigation only — no code written.

---

## TL;DR

CalCareers is the canonical CA state jobs portal — and it is **the hardest of our v1 sources to scrape cleanly**. It's an ASP.NET WebForms app with DevExpress controls and ViewState; the search form requires stateful POSTs, not GETs with query params. No public JSON API, no RSS, no sitemap. Posting detail pages are individually addressable by integer `JobControlId` and are server-side HTML (scrapeable). robots.txt is empty (effectively unrestricted). Covered California postings **are** in the system. A Blazor Server replacement system is being stood up in parallel at `eservices.calhr.ca.gov` — when CalHR migrates, this scraper will break entirely.

Recommendation: start with `requests` + `BeautifulSoup` and emulate the ViewState POST. Plan for Playwright as fallback if emulation proves too fragile. Flag the Blazor migration as a known kill-shot.

---

## 1. Site Structure

### Base URLs

- Public site: `https://calcareers.ca.gov/` (the `www.` subdomain 301-redirects to bare)
- Likely future replacement: `https://eservices.calhr.ca.gov/enterprisehrblazorpublic` — Blazor Server SPA (uses `_framework/blazor.server.js` + SignalR/WebSockets). Currently appears to be a separate HR-portal, not a job-search replacement, but worth watching.

### Search endpoints

| Page | URL | Method |
|---|---|---|
| Advanced Search form | `/CalHRPublic/Search/AdvancedJobSearch.aspx` | GET to fetch form, **POST to submit** |
| Geographical Search | `/CalHRPublic/Search/GeographicalJobSearch.aspx` | Same pattern |
| Exam Search | `/CalHRPublic/Search/ExamSearch.aspx` | Same pattern |
| Search Results | `/CalHRPublic/Search/JobSearchResults.aspx` | Reached via POST from a search form |

**GET with query params does not populate results.** Confirmed: `GET /CalHRPublic/Search/JobSearchResults.aspx?JobTitle=Information+Technology+Specialist` returns the search-form shell (~2.2k lines) with no job rows. Search is stateful and requires the full ASP.NET WebForms POST.

### Search form fields (from `AdvancedJobSearch.aspx`)

The page is ASP.NET WebForms with DevExpress UI components. Form `action="./AdvancedJobSearch.aspx"`, `method="post"`. Required hidden state fields:

```
__VIEWSTATE
__VIEWSTATEGENERATOR
__VIEWSTATEENCRYPTED
__EVENTVALIDATION
__EVENTTARGET
__EVENTARGUMENT
__SCROLLPOSITIONX
__SCROLLPOSITIONY
```

User-facing field names (DevExpress dropdowns — `ddl*_VI` is the DevExpress "Value Index" hidden input that holds the selected value):

```
cphMainContent_ddlClassification_VI    # Classification (e.g., ITS II)
cphMainContent_ddlDepartment_VI        # Department
cphMainContent_ddlJobCategories_VI     # Job Categories
cphMainContent_ddlLocation_VI          # Location
cphMainContent_ddlTelework_VI          # Telework dropdown (Yes/No/Eligible)
cphMainContent_ddlPostedIn_VI          # "Posted in last N days"
cphMainContent_ddlSalaryRange_VI       # Minimum Salary
cphMainContent_ddlWorkType_VI          # Permanent / Limited-term / etc.
cphMainContent_ddlWorkSchedlue_VI      # [sic — "Schedlue"] FT/PT
cphMainContent_ddlApplicationMethod_VI
ctl00$cphMainContent$btnSearch         # Submit button
```

There's also a "Keyword" text input and a "Job Code" text input (the `JC-` number) — these surface in the form labels but their `name` attributes weren't quotable from my fetch and need a real browser inspection.

### Posting detail URLs

Two endpoints, both server-rendered HTML, both work without auth:

- **Web view:** `https://calcareers.ca.gov/CalHrPublic/Jobs/JobPosting.aspx?JobControlId={ID}`
- **Print view:** `https://calcareers.ca.gov/CalHrPublic/Jobs/JobPostingPrint.aspx?jcid={ID}`

The print view tends to be cleaner HTML (single-column, less chrome) and is the better scrape target.

`JobControlId` is a sequential integer. Current open postings as of 2026-05-27 appear to live in the **~500k–520k range** (probing IDs 520k–600k all returned the "never existed" shell at exactly 53,271 bytes; IDs 488k–516k returned the "expired" shell at ~54.1–54.3kb).

### Expired-posting signal

When a posting is past its Final Filing Date (or its ID never existed), the URL returns **HTTP 200** but the body is a stub containing:

```html
<div class="col-md-12">
    This Job Posting is no longer available.<br/><br/>
</div>
```

Important: scraper must check for this string (or for missing structural elements) and treat as "no longer available" rather than parse error. The page `<title>` element on these stubs is literally `<title>Meta Tags — Preview, Edit and Generate</title>` (broken / placeholder content from the site — not useful as a signal but worth noting it's not a real title tag).

### Pagination

Not directly observable without a populated search-results POST. Given DevExpress + ViewState, pagination is almost certainly **`__doPostBack` callbacks**, where clicking "Next" sets `__EVENTTARGET` to the pager control name and re-POSTs the form. Will need to inspect a live results page once we've gotten the POST working. **Don't try to construct page=N GET URLs — they won't work.**

### Feeds / APIs

Probed and confirmed missing:

| Probe | Result |
|---|---|
| `/sitemap.xml` | not present |
| `/rss`, `/feed`, `/jobs.xml` | not present |
| `/api/jobs`, `/api/JobPostings`, `/api/v1/jobs` | not present |
| `eservices.calhr.ca.gov/_framework/blazor.boot.json` | (loop quoting failed — manual re-probe needed but Blazor Server doesn't expose a JSON job feed regardless) |

There is no public CalCareers JSON API. CalHR / CDT publish no developer documentation for one.

### robots.txt

```
#robots file
#may be needed in beta site to exclude crawlers

#User-agent: *
#Disallow: /
```

Every line is commented out. There are zero active directives. We're not restricted, but we should still hold to our own 2-second between-request default and stop on 429/5xx.

### Covered California presence — CONFIRMED

Covered California (CA's ACA marketplace) posts directly into CalCareers. Examples found in Google's index:

- `jcid=355982` — "Join the Covered California team!"
- `jcid=319707` — "Covered California - Lead Business Analyst"
- `jcid=331186` — "Covered California - Contract Analyst"
- `jcid=126160` — "Chief Technology Officer - Covered California"

Spec assumption ("CA state departments + Covered California") holds. No separate Covered CA scraper needed.

---

## 2. Sample Posting

### Probe corrections (2026-05-28)

After the Phase 2a Playwright probe captured a real live posting (jcid=505623, .Net Developer, saved at `tests/fixtures/calcareers_sample.html`), the field-mapping table below was verified end-to-end. One discrepancy from the initial inference:

- **`pnlMinimumRequirements` does not exist as a wrapper div on the print page.** The page contains only an `<h3 class="postingHeader">Minimum Requirements</h3>` followed by `<span id="lblMinimumReqsInClassSpec">` containing a link to the separate CalHR class-spec page. The requirements bullets themselves live on that other URL. Capturing them requires a second Playwright fetch per classification. **Deferred to v1.1, planned to land with Tier 2.** The `_RAW_TEXT_PANELS` list in `crawler/sources/calcareers.py` is 6 panels, not 7.

Everything else in the verified mapping below matches the live DOM.

**Important caveat:** I was unable to confirm an open posting via curl during this recon. I probed JobControlIds at 488k, 492k, 496k, 500k, 504k, 508k, 512k, 516k, 520k, 525k, 528k, 530k, 532k, 534k, 536k, 540k, 545k, 550k, 555k, 560k, 570k, 580k, 590k, 600k, plus 502478 (a recent IT Specialist I per Google). **Every one** returned the "no longer available" stub. Open postings exist (the CalCareers homepage advertises 1,000+ jobs) — they're just sparse in the ID space and the ones Google has indexed have all aged out.

The first thing the scraper should do during initial build: a real search POST will return a results page with currently-open JCs. Fetch one of those and verify the selectors below against ground truth. The structure below is **inferred** from URL patterns, Google snippets of historical postings, and standard ASP.NET WebForms conventions — not confirmed against a live posting.

### URL pattern (confirmed)

```
https://calcareers.ca.gov/CalHrPublic/Jobs/JobPostingPrint.aspx?jcid={JobControlId}
```

### Posting field mapping (verified 2026-05-28 against fixture jcid=505623)

| Posting field | Source page | Selector / extraction |
|---|---|---|
| `source_job_id` | Print detail page | `span#lblDetailsJobControlNumber` text (keeps `JC-` prefix, e.g. `JC-505623`) |
| `title` | Print detail page | `span#lblWorkingTitle` — working title, populated post-load by the DevExpress JS layer |
| `classification` | Print detail page | `span#lblPrimaryClassification`, title-cased with Roman numerals preserved (e.g. `Information Technology Specialist II`) |
| `employer` | Print detail page | `span#lblDepartmentName` |
| `salary_min` / `salary_max` | Print detail page | `span#lblPrimarySalary`, regex `\$([\d,]+(?:\.\d{1,2})?)\s*-\s*\$([\d,]+(?:\.\d{1,2})?)\s*per\s*Month`. Salary is already monthly (no /12). Anything other than "per Month" → `(None, None)` + log. Range A is the primary salary field; alternate ranges are not parsed (Range-A convention from decision §1). |
| `location` | Print detail page | `span#lblWorkLocation` literal text. `all_locations` is a single-element list `[location]` for v1. |
| `telework_flag` | Print detail page | `span#lblTelework`: `"Yes"` or `"Hybrid"` → True; `"No"` → keyword scan over raw_text (overrides if `telework`/`remote`/`hybrid` present); missing/empty → keyword scan only |
| `raw_text` | Print detail page | Concatenated panels with `=== Header ===` separators, in visible-page order: `pnlJobDescription`, `pnlWorkingConditions`, `pnlPositionDetails`, `pnlDepartmentInfo`, `pnlSpecialRequirements`, `pnlDesirableQualifications`. Classification text prepended so the keyword matcher sees it. **6 panels total** — Minimum Requirements punted to class-spec page (deferred to v1.1; see Probe corrections above). |
| `posted_date` | Print detail page | Not present on print page — set to `None` and rely on `first_seen` at store time |
| `url` | Constructed | Web view: `https://calcareers.ca.gov/CalHrPublic/Jobs/JobPosting.aspx?JobControlId={INT_ID}` (integer only, no `JC-` prefix). Print URL is parse-time-only. |

**Fields that vary across postings (per inferred reading):**
- Salary: monthly vs. annual depending on classification; multiple range bands for some classes
- Location: single city, county only, "Statewide," "Multiple," or specific street address
- Telework: phrased many ways ("telework eligible," "hybrid up to 3 days," "in-office only," etc.) — keyword scan only; do not try to parse a structured value
- "Working title" vs. "Classification title" — postings often advertise a working title in the H1 and a separate classification label below

### Decision needed
We will need to **fetch one live posting and adjust the field mapping** before the scraper goes live. This is the first task when implementation starts. Don't sink time into the parser until we have a ground-truth sample.

---

## 3. Scoping — Which Subset to Pull

### Why scope at all
CalCareers shows 1,000+ open jobs at any time. Pulling everything means burning POST/pagination requests on noise that hard filters will reject anyway. Scope by **classification** — that's CA's structural unit for jobs and maps cleanly to `target_titles_yes` in `data/inventory.py`.

### Confirmed classification codes
- **1402** — Information Technology Specialist I
- **1414** — Information Technology Specialist II
- (Higher in the IT series: 1415 = ITS III; 1572 = IT Supervisor II — out of scope per JT's preferences)

### Classifications we need to map
Per `data/inventory.py` → `target_titles_yes`:

| `target_titles_yes` entry | Likely CA class | Status |
|---|---|---|
| Information Technology Specialist I | 1402 | ✓ confirmed |
| Information Technology Specialist II | 1414 | ✓ confirmed |
| Business Systems Analyst | unclear — likely "Associate Information Systems Analyst (Specialist)" or "Staff Information Systems Analyst (Specialist)" | needs lookup |
| Applications Analyst | same series as BSA | needs lookup |
| Training Officer | distinct class | needs lookup |
| Education Programs Consultant | code 5739 (commonly cited; verify) | needs verification |
| Adult Education Instructor | likely teaching credential dependent | needs lookup |
| IT Consultant (CSU) | **CSU classification, not state — does not appear on CalCareers**, covered by the separate CSU Careers scraper | n/a here |
| Instructional Designer | not a standard CA class title — may map to ITS series | needs lookup |
| Staff Services Analyst (training focus) | code 5157 (SSA-General) — applies broadly across departments | likely ✓, verify |
| Educational Programs Assistant | needs lookup | needs lookup |

There's a public PDF — `https://calcareers.ca.gov/pdf/IT-Classification-Mapping.pdf` — that maps IT classifications by domain. Once we have a working search POST, we should also pull the full classification list and store it in `data/sources.yaml` (or a sibling) for reference. **Not doing that in this recon — flagging as a build-time task.**

### Sample target search (proposed)

A single search filtered to `Classification ∈ {1402, 1414}` is the right starting subset. We can't form this as a GET URL (search is POST-only), so the conceptual filter is:

```
POST /CalHRPublic/Search/AdvancedJobSearch.aspx
  cphMainContent_ddlClassification_VI = "1402,1414"   # syntax TBD
  + full ViewState payload
  + ctl00$cphMainContent$btnSearch = "Search"
```

(DevExpress `_VI` fields may take CSV, may require one classification per submit — confirm at implementation time.)

### Volume estimate
- Total CA state open postings at any time: ~1,000–2,000 (CalHR has cited this figure historically)
- ITS I + ITS II together: rough estimate ~30–80 open at any given time, statewide
- New ITS I/II/BSA postings per day: educated guess **5–15**, with most clearing hard filters after the employer allow-list narrows by department
- This is a **guess** and needs to be re-measured after the first week of live runs. Build the source assuming the right order of magnitude and refine.

---

## 4. Risks

### High-impact, likely
- **ViewState fragility.** Any CalHR-side template change can invalidate the `__VIEWSTATE` / `__EVENTVALIDATION` serialization our scraper assumes. We'd see opaque 500s or empty results. Mitigation: always fetch a fresh form before POSTing, and log the full response body on parse failure.
- **Postings expire fast — must scrape daily.** "Final Filing Date" can be 2 weeks out for some postings and continuous for others. Empty-scrape protection (already in CLAUDE.md) matters: if a daily run returns zero ITS postings, treat as scraper failure, not site state.

### Medium-impact, plausible
- **DevExpress callback POSTs for pagination.** Pagination clicks are `__doPostBack` events that may use a slightly different POST shape than the initial search. May require per-page state forwarding. Could double our effort budget for this source.
- **Blazor Server migration.** `eservices.calhr.ca.gov/enterprisehrblazorpublic` is live and uses Blazor Server (SignalR/WebSockets). If CalHR migrates job search to this stack, the scraper dies overnight — Blazor Server doesn't render meaningful HTML and can't be POST-emulated. Mitigation: monitor for a redirect from `calcareers.ca.gov` and have a plan to switch to Playwright at that point.
- **Azure Front Door rate limits.** The site sits behind Azure Front Door (`x-azure-ref` header observed). Aggressive scraping risks IP-level throttling. Stay at 2s default delay, sequential pagination, back off hard on 429.

### Low-impact / monitor
- **Salary band ambiguity.** Some classifications publish A/L/B range variants (different alternate-range pay scales). Need a documented convention: store the maximum across all variants? Range-A only? Decision needed when we build the parser. Recommend Range-A as default since most postings use it.
- **No JS, no CAPTCHAs observed** on detail-page fetches. Search-form submissions haven't been tested — possible CAPTCHA on the search POST that I can't see without trying.

### Out of scope to mitigate now
- We are **not** installing Playwright in this build. If/when the requests-based approach proves untenable, **that's a separate decision** to bring back to JT with evidence (specific failures, frequency, time spent debugging).

---

## Open Items for JT

1. **Salary band convention.** When a posting lists Range A and Range L (e.g., ITS II: `$8,625 - $11,557 (A)` and `$8,881 - $11,905 (L)`), which range do we store as `salary_min` / `salary_max`? Recommend Range A as default.
2. **Canonical URL.** Store `JobPosting.aspx?JobControlId=` (web view, friendlier for click-through) or `JobPostingPrint.aspx?jcid=` (cleaner for re-parsing)? Recommend storing the web URL and re-fetching the print URL at parse time if needed.
3. **Classification list completeness.** Should the scraper pull every classification in `target_titles_yes`, or start with a tighter subset (ITS I + ITS II + SSA) and expand based on early signal? Recommend starting tight.
4. **Build-order consideration.** PROJECT_STATUS.md ranks CalCareers as the "highest yield" first scraper. After this recon, **USAJobs (official documented API, no auth dance for read)** is technically the easiest source. CalCareers will likely take 3–4× the effort. Worth considering whether to do USAJobs first as a faster end-to-end proof, then CalCareers — but this changes the spec. **Flagging, not deciding.**

---

## Decisions (locked 2026-05-27)

1. **Salary band convention.** Use Range A for both `salary_min` and `salary_max`. Conservative — prevents inflating displayed salary vs. what JT would actually start at. If a posting lists Range A and Range L (or A and B), the higher band is ignored. Revisit if false negatives surface in audit view.

2. **Canonical URL.** Store `JobPosting.aspx?JobControlId={ID}` (web view) as the `url` field. Print URL is implementation detail — re-derive at parse time when needed.

3. **Classification scope at first launch.** Start with 1402 (ITS I), 1414 (ITS II), and 5157 (SSA-General). Other `target_titles_yes` classifications deferred until the narrow scope is observed for 2 weeks. Expand based on signal.

4. **Build order.** USAJobs replaces CalCareers as the first real scraper. CalCareers deferred until the pipeline (filter, score, store, render) is proven end-to-end against the simpler source. Rationale: CalCareers is POST-only ASP.NET with ViewState and a Blazor migration risk; USAJobs is a documented REST API. Validate architecture against the easy source first.
