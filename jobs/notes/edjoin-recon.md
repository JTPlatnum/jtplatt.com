# EdJoin Reconnaissance

**Date:** 2026-06-03
**Goal:** Map EdJoin (edjoin.org — CA K-12 + community college + county office of education job board) before writing `crawler/sources/edjoin.py`. JT's Adult Education / CTE credential makes this a primary teaching-track lane.
**Status:** Investigation only — no code written. Verified against the live site via curl + UA-spoofed probes from a real Chrome UA, including a 25-fetch sustained-pace burst at production cadence (2s intervals). One live detail-page sample saved to scratch (`/tmp/edjoin_recon_sample.html`, postingID 2232159 — Palo Alto Unified Elementary Teacher); NOT yet promoted to `tests/fixtures/`.

---

## TL;DR

**EdJoin is the easiest source we've recon'd so far.** It's a Microsoft IIS / ASP.NET MVC 5.3 app sitting on top of an **Azure Cognitive Search** index. The search endpoint is a public JSON API (`GET /Home/LoadJobs?<params>`) returning structured data — no DOM scraping for listings, no Playwright. Detail pages are server-rendered HTML at `/Home/JobPosting/{postingID}` and embed a **schema.org/JobPosting JSON-LD block** with the most useful fields already structured (title, hiringOrganization, jobLocation, datePosted, employmentType, etc.). Salary and full description body still need light HTML parsing, but the heavy lifting is JSON in, JSON out.

**No bot mitigation detected.** Bare IIS server header, no CloudFront, no Cloudflare, no AWS WAF, no Akamai. **25 sustained fetches at 2-second intervals (production cadence) — 25/25 returned 200.** Earlier 20-fetch burst at 1-second intervals — 19/20 returned 200, the 1 failure was a `500 NullReferenceException` from probing a non-existent postingID, not a rate-limit signal (verified by the body's stack trace mentioning `System.Web.Mvc.ControllerActionInvoker`, not a CDN challenge stub). This is the inverse of the CSU recon mistake: the CSU recon's one-off probes didn't detect AWS WAF that tripped under sustained load; here, sustained probes at real-run cadence confirm there's nothing to trip.

**No published `robots.txt`** (`/robots.txt` returns 404) and **no Terms of Service** link from the homepage — only a Privacy Policy. EdJoin's public-data, education-recruitment posture suggests scraping for personal job search is well within use intent, but JT may want to glance at /Home/Privacy before commit.

**One scope finding worth surfacing early:** EdJoin has **no structured remote-only filter at query time**. Keyword searches for "remote" / "virtual teacher" / "hybrid" return small candidate sets (29 / 130 / 48 records respectively), but there's no boolean toggle equivalent to USAJobs's `TeleworkEligible` field or CSU's `Telecommute eligible` category. Per question 12, the EdJoin "remote" track must be a keyword-augmented post-fetch scan, not a query filter.

Recommendation: build with **`requests` + `BeautifulSoup`**. Effort sits at the easy end — closer to USAJobs than CalCareers. Estimated 1.5–2 hours of scraper work plus 30 minutes of jobType/county-ID lookup tables.

---

## 1. Site Structure

### Base URLs

| URL | Serves | Use? |
|---|---|---|
| `https://edjoin.org/` | Apex; same content as www | ✅ ok (no redirect; both 200) |
| `https://www.edjoin.org/` | Primary public hostname | ✅ Primary |
| `https://www.edjoin.org/Home/Jobs?<params>` | Search-results page (renders empty shell + JS calls LoadJobs) | ⚠️ Don't scrape directly — see §3 |
| **`https://www.edjoin.org/Home/LoadJobs?<params>`** | **JSON API consumed by the search UI** | ✅ **Primary search entry** |
| `https://www.edjoin.org/Home/JobPosting/{postingID}` | Detail page (server-side HTML + JSON-LD) | ✅ Per-posting fetch |
| `https://www.edjoin.org/Home/DistrictJobPosting/{postingID}` | Same posting served via district-branded URL | Optional alias |
| `https://www.edjoin.org/Home/RegionSearch` | Region-browse landing | ❌ JSON LoadRegions is the data source |
| `https://www.edjoin.org/Home/LoadRegions?states=24` | County list with current posting counts (JSON) | ✅ Cache once for countyID lookup |
| `https://www.edjoin.org/Home/GetJobTypesCriteria?jobtypes={ID}` | Returns the label for a single jobTypeID (JSON) | ✅ Enumerate once for jobTypeID lookup |
| `https://edjoinprodstoragewest.blob.core.windows.net/...` | Azure Blob storage for district logos, attached PDFs | Read-only assets |
| `https://www.edjoin.org/Account/Login`, `/Account/Register` | Auth (NOT required for browsing/searching) | Out of scope |

### Headers / platform identifiers

From `GET /` and `GET /Home/JobPosting/2232159`:

```
Server: Microsoft-IIS/10.0
X-AspNetMvc-Version: 5.3
X-AspNet-Version: 4.0.30319
X-Powered-By: ASP.NET
Cache-Control: no-cache, no-store
```

No CDN headers (no `cf-ray`, no `x-amz-cf-id`, no `x-cache: ... from cloudfront`, no `x-azure-ref`). EdJoin appears to be served directly from origin servers — likely Azure-hosted given the Blob storage URLs but without a fronting CDN.

**Backend: Azure Cognitive Search.** Leaked via a 400 error body during sort-field probing: `Could not find a property named 'PostingDateValue' on type 'search.document'. ... client-request-id, x-ms-client-request-id, request-id ...`. Those are Microsoft Azure Search-specific markers. Implication: field names in `sort=` parameter are case-sensitive and only certain `search.document` properties are sortable. Verified working: `sort=postingDate` (camelCase). Failing: `PostingDate`, `PostingDateValue`, `Date`, `Relevance`.

### Auth, robots, rate limits, bot mitigation

- **Auth:** None for search or detail-page fetches. Login is only required for the apply form, the wishlist, and saved searches.
- **`robots.txt`:** 404 on both `edjoin.org/robots.txt` and `www.edjoin.org/robots.txt`. No published policy. By convention, absence = implicit allow.
- **Terms of Service:** No "Terms" or "TOS" link from the homepage. Only `/Home/Privacy`. JT may want a quick read before commit.
- **Rate limits:** No documented limits. No `Retry-After` / `X-RateLimit-*` headers in any response observed.
- **Bot mitigation (production-cadence probe, applying the CSU lesson):**
  - **25 sustained `GET /Home/LoadJobs?...` fetches at 2-second intervals → 25/25 returned 200.** No challenge page, no rate-limit response, no behavior change.
  - 20-fetch alternating burst at 1-second intervals (search + detail interleaved) → 19/20 returned 200; 1 returned 500 with a `NullReferenceException` stack trace from probing a non-existent postingID (`2232178`). NOT a bot-mitigation tell — verified by stack-trace body content. Subsequent fetches resumed cleanly at 200.
  - **No identical-body responses across failures** (CSU's WAF tell was 28 identical 2464-byte response bodies).
  - **No CDN headers anywhere in the response set.**
- **Conclusion:** No bot mitigation detected. Stack is bare IIS + ASP.NET, no fronting CDN. The CSU lesson is applied: this conclusion is based on sustained-pace probing at production cadence, not one-off curl invocations.

---

## 2. Search Mechanism

### The real endpoint

The visible search-results URL (`/Home/Jobs?keywords=teacher`) is a thin shell that loads `/Scripts/pages/jobs.js`, which in turn calls a JSON API:

```
GET /Home/LoadJobs?<params>
Headers:
  X-Requested-With: XMLHttpRequest      (required — without it, returns 500)
  Accept: application/json
  Referer: https://www.edjoin.org/      (any valid Referer; absence not tested)
  User-Agent: <realistic browser>
```

Returns JSON:

```json
{
  "search": { /* echo of query params */ },
  "totalPages": 408,
  "totalRecords": 10179,
  "totalOpenings": 0,
  "displayRecords": 25,
  "data": [ /* up to `rows` job objects */ ]
}
```

### Required query parameters (all of these, all the time)

From `buildSearchQueryString()` in `/Scripts/pages/jobs.js`:

| Param | Type | Notes |
|---|---|---|
| `rows` | int | Page size. Default 25; observed working up to 25. Larger not tested. |
| `page` | int | 1-indexed |
| `sort` | string | `postingDate` (camelCase, verified) is the OpenDate equivalent. Empty string also works (default order). Case-sensitive. |
| `sortVal` | string | `0` works. Purpose unclear; required to be present. |
| `order` | string | `asc` / `desc` |
| `keywords` | string | URL-encoded free-text |
| `location` | string | Free-text city/region; quality unclear, county filter is better |
| `searchType` | string | `all` is the default; other values not enumerated |
| `regions` | CSV of int countyIDs | Multi-county OR filter — e.g., `regions=34` (Sacramento) or `regions=27,34,44,58` |
| `jobTypes` | CSV of int jobTypeIDs | Multi-jobType OR filter — e.g., `jobTypes=6` (Adult Ed Teacher) |
| `days` | int | Posting-age filter; `0` = unrestricted |
| `empType` | string | `Full` / `Part` / `0` (any) |
| `catID` | int | Top-level category: `1`=Certificated, `2`=Certificated Mgmt, `3`=Classified, `4`=Classified Mgmt, `0`=any |
| `onlineApps` | int | `1` = only postings with online application; `0` = any |
| `recruitmentCenterID` | int | County office of education filter; `0` = any |
| `stateID` | int | `24` = California; `0` = all (EdJoin is CA-centric anyway) |
| `regionID` | int | Single-region; usually `0` (use `regions` CSV instead) |
| `districtID` | int | Single-district filter; `0` = any |
| `searchID` | int | For saved searches; `0` works |

**All params must be present even when "empty"** — sending an empty value or omitting one breaks the API. Use `0` for numeric empties and an empty string for string empties.

### Pagination

Standard `page=N&rows=R`. `totalPages` is returned in every response. Iterate page=1..totalPages with 2s delay; stop at `totalPages`. No "next-page-token" complexity.

### Response data shape (per-job object)

47 fields per job (full list verified in a live response). Highlights:

```json
{
  "postingID": 2232159,
  "positionTitle": "Elementary Teacher - 2nd Grade",
  "salaryInfo": "Placement on Teachers Salary",
  "beginningSalary": null,
  "endingSalary": null,
  "PayRangeFrom": "",
  "PayRangeTo": "",
  "SingleRate": "",
  "SalaryInfoSelect": "Dependent",
  "displayFlag": "Until Filled",
  "postingDate": "/Date(1780444800000)/",
  "displayUntil": "/Date(1796284800000)/",
  "date_startApplication": "/Date(-62135568000000)/",
  "CreationDate": "/Date(1780470426660)/",
  "countyName": "Santa Clara",
  "countyID": 43,
  "districtName": "Palo Alto Unified School District",
  "city": "Palo Alto",
  "zip": null,
  "State": 24,
  "stateName": "California",
  "fullCountyName": "Santa Clara County, CA   ",
  "categoryID": 1,
  "categoryName": null,
  "jobTypeID": 48,
  "jobType": "Teacher - K-6",
  "FullTimePartTime": "Full Time",
  "numberOpenings": 0,
  "isAdminJob": false,
  "isRecruitmentCenter": false,
  "isSummerSchool": false,
  "limitPosting": false,
  "JobSummary": null,
  "postingInformation": null,
  "portalURL": null,
  "districtLogo": null,
  "onlineApp": false,
  "app_status": null
}
```

Dates are in **Microsoft JSON date format** (`/Date(epoch_ms)/`) — strip the wrapper and parse the integer as milliseconds since 1970-01-01 UTC, then convert to `date`. The sentinel `/Date(-62135568000000)/` is `0001-01-01` (effectively "unset").

### Did NOT find a remote-only query filter

**Answering question 12:** No `remote`, `telework`, `virtual`, `workfromhome`, or `hybrid` filter parameter in either the search UI form (`/Home/Jobs?keywords=...`) or the LoadJobs query-string vocabulary. Keyword search returns small candidate sets:

- `keywords=remote` → 29 records (mostly "Special Education Teacher Mild/Mod - Remote" — hybrid/blended models, not fully remote)
- `keywords=virtual+teacher` → 130 records
- `keywords=online+teacher` → 16 records
- `keywords=hybrid` → 48 records
- `keywords=telework` → 0 records

For JT's "explicit remote queries across all sources" side task: EdJoin's contribution is **two keyword sweeps** (`keywords=remote` and `keywords=virtual+teacher`), dedup'd against the main lane by `postingID`. No structured flag to flip.

---

## 3. Posting URL Structure & Detail Page

### URL pattern

```
https://www.edjoin.org/Home/JobPosting/{postingID}
```

- `postingID` is a 7-digit integer (e.g., `2232159`), monotonically increasing per posting (EdJoin's internal sequence). Stable. Use as `source_job_id`.
- No slug suffix. The URL is purely numeric.
- Alternative alias `/Home/DistrictJobPosting/{postingID}` exists for district-branded landing but serves the same content. Not needed.

### Verification

`GET https://www.edjoin.org/Home/JobPosting/2232159` returns HTTP 200, 141KB HTML, no auth required, no JS challenge. Server: IIS, no CDN headers.

### Schema.org JSON-LD block (the goldmine)

Every detail page embeds a `<script type="application/ld+json">` block with structured `JobPosting` data. Verified against postingID 2232159:

```json
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Elementary Teacher - 2nd Grade",
  "employerOverview": "Palo Alto Unified School District",
  "jobSummary": "OVERVIEW:A teacher provides an educational program...",
  "description": "See attachment on original job posting",
  "identifier": { "@type": "PropertyValue", "name": "...", "value": 2232159 },
  "hiringOrganization": {
    "@type": "Organization",
    "name": "Palo Alto Unified School District",
    "sameAs": "https://www.edjoin.org/pausd",
    "logo": "...",
    "contactPoint": { "@type": "ContactPoint", "email": "..." }
  },
  "jobLocation": [{
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "25 Churchill Ave.",
      "addressLocality": "Palo Alto",
      "addressRegion": "California",
      "postalCode": "94306-1005",
      "addressCountry": "US"
    }
  }],
  "datePosted": "2026-06-03T07:00:00Z",
  "validThrough": "2026-12-03T08:00:00Z",
  "workHours": "187 Days",
  "employmentType": "FULL_TIME",
  "experienceRequirements": "Refer to the job posting...",
  "skills": "Refer to the job posting..."
}
```

**Caveats already visible:** `description`, `experienceRequirements`, `skills` are often boilerplate ("Refer to the job posting...") — districts don't always fill them in. For the actual `raw_text` body, parse the visible HTML body sections instead (see field mapping below).

### Visible HTML structure (for fields not in JSON-LD or with boilerplate values)

Looking for content via grep:

| Label | Approximate location | Sample value |
|---|---|---|
| `Date Posted` | `<h5>Date Posted</h5><div ...>` | `6/3/2026` |
| `Contact` | `<h5>Contact</h5><div id="contactContainer">` | `<a href="mailto:janderson@pausd.org">Jaime Anderson</a>` |
| `Number of Openings` | `<h5>Number of Openings</h5>` | `1` |
| `Salary` | `<h5>Salary</h5>` | `Pay dependent on experience` |
| `Add'l Salary Info` | `<h5>Add'l Salary Info</h5>` | `Placement on Teachers Salary` |
| `Length of Work Year` | `<h5>Length of Work Year</h5>` | `187 Days` |
| `Employment Type` | `<h5>Employment Type</h5>` | `Full Time` |
| `Location` | `<h5>Location</h5>` (inside `.jobPostingbody`) | `Walter Hays Elementary` (the school site) |
| `Department` | `<h5>Department</h5>` | `Education Services` |
| `Job Summary` | `<h3>Job Summary</h3><p class="indent">` | (multi-paragraph body) |
| `Requirements / Qualifications` | `<h3>Requirements / Qualifications</h3>` | (multi-paragraph body) |
| `Comments and Other Information` | similar pattern | (multi-paragraph body) |

The page title (`<title>`) and canonical link (`<link rel="canonical" href="...JobPosting/{ID}">`) both carry useful metadata too.

---

## 4. Field Mapping (Posting fields ← EdJoin sources)

The cleanest strategy is **JSON API for listings, JSON-LD + light HTML parse for body & salary**. Most fields are available from the listing API, so per-posting detail fetches can be skipped for fields that don't need body text.

| `Posting` field | Source | Notes |
|---|---|---|
| `source_job_id` | `data[].postingID` (int → str) | E.g., `"2232159"`. Stable identifier. |
| `title` | `data[].positionTitle` (or detail-page JSON-LD `title`) | Working title; full text. |
| `employer` | `data[].districtName` (or JSON-LD `hiringOrganization.name`) | E.g., `"Palo Alto Unified School District"`. |
| `url` | constructed: `https://www.edjoin.org/Home/JobPosting/{postingID}` | Canonical. JSON-LD has the same as `<link rel="canonical">`. |
| `salary_min` / `salary_max` | parse from `data[].salaryInfo`, `beginningSalary`, `endingSalary`, `PayRangeFrom`, `PayRangeTo`, `SingleRate` — **OR** regex over the detail page `Salary` / `Add'l Salary Info` text | Listing-API salary fields are often null or empty strings; detail-page text is often free-prose ("Pay dependent on experience", "Placement on Teachers Salary"). Need a regex pack like CSU. Expected patterns: explicit monthly range, annual range, hourly range, "Placement on X Salary Schedule" (= salary schedule URL link, no numbers), "Pay dependent on experience" (= None). |
| `location` | `data[].city + ", " + data[].countyName + ", California"` | Or use JSON-LD `jobLocation[0].address` for a structured address. Many postings list a single location. |
| `all_locations` | `[location]` (single-element list) | Multi-site postings exist (some districts post a role spanning sites); detect via the inner `<h5>Location</h5>` block. Defer multi-location handling for v1. |
| `telework_flag` | substring scan of `positionTitle` + `JobSummary` for `"remote"`, `"virtual"`, `"hybrid"`, `"telework"`; case-insensitive | **No structured field exists** (verified — see question 12 above). Same approach as USAJobs's `raw_text` keyword scan. |
| `classification` | `data[].jobType` (e.g., `"Teacher - K-6"`, `"Teacher - Adult Education"`) | NOT a parenthetical-in-title heuristic (unlike CSU). EdJoin tags the jobTypeID directly. JT-relevant jobTypes are enumerated in §5. |
| `raw_text` | concat with section headers: `positionTitle` + `salaryInfo` + JSON-LD `jobSummary` + detail-page body sections (`Job Summary`, `Requirements / Qualifications`, `Comments and Other Information`) | Heaviest field; requires per-posting detail fetch. |
| `posted_date` | `data[].postingDate`, parse via `/Date\((\d+)\)/` → epoch ms → `date` | Structured. Microsoft JSON date format. Verified format. |

### Why we still want the detail fetch (even with listing-API data)

Three reasons:
1. **Body text for keyword scoring.** `JobSummary` in the listing API is null for many postings; the visible page body has the real content.
2. **Salary detail.** `salaryInfo` in the listing is often a free-form string ("Pay dependent on experience", "Placement on Teachers Salary"); the detail page's `Salary` and `Add'l Salary Info` blocks sometimes have numbers (or a salary-schedule URL link we can follow later).
3. **Telework signal.** No structured flag — must keyword-scan the body.

**Per-posting detail fetch cost:** 141KB HTML × ~80-100 candidate postings per run × 2s delay = ~3-5 minutes wall-clock. Acceptable at daily cadence.

---

## 5. Scoping — Which Subset to Pull

### JT's lanes (per recon brief)

1. **CTE (Career Technical Education) teacher roles** — JT holds an Adult Education CTE credential (expires Feb 2027). Linchpin lane.
2. **Adult Education instructor roles** — direct CTE adjacency.
3. **Instructional Designer / Instructional Technology Coordinator** at districts or community colleges.
4. **Educational Programs / Curriculum Specialist.**
5. **Training Coordinator / Staff Development.**
6. **Community college IT / analyst roles** — pension-mixed (CalPERS some, CalSTRS others).

### What the EdJoin filter vocabulary supports

| Filter | Coverage |
|---|---|
| `catID=1` (Certificated) | Credentialed teaching positions — CalSTRS-covered |
| `catID=2` (Certificated Management) | Credentialed admin (principals, directors with credentials) |
| `catID=3` (Classified) | Non-credentialed staff (IT, clerical, custodial) — CalPERS-covered at K-12 |
| `catID=4` (Classified Management) | Non-credentialed managers |
| `jobTypeID=6` | **Teacher - Adult Education** ← primary lane match |
| `jobTypeID=17` | Coordinator (generic — too broad alone; needs keyword refinement) |
| `jobTypeID=66/67/68` | Adult School Vice Principal / Principal / Director |
| `jobTypeID=48` | Teacher - K-6 (NOT in JT's lane, but listed for context) |
| `regions=<countyID>` | County filter — CSV of countyIDs (Sacramento=34, Yolo=58, etc., full table in §7) |
| `days=N` | Posted within N days |
| `keywords=...` | Free-text against title + body |

### What EdJoin does NOT have

Verified via 200-attempt jobTypeID enumeration (jobTypeIDs 1-200, 76 returned non-empty):

- **No "Career Technical Education" jobTypeID** — CTE roles spread across multiple jobTypes by subject area
- **No "Instructional Designer" jobTypeID** — not in K-12 controlled vocabulary
- **No "Instructional Technologist" jobTypeID**
- **No "Training Coordinator" jobTypeID**
- **No "Curriculum Specialist" jobTypeID**
- **No remote-only filter** (see §2 and question 12)

Implication: JT's secondary/tertiary lanes must be keyword sweeps, not jobType filters. Volume per keyword (verified):

| Keyword | Records (live, 2026-06-03) |
|---|---|
| `Adult+Education` | 169 |
| `CTE` | 197 |
| `Career+Technical+Education` (full phrase) | 67 |
| `Curriculum+Specialist` | 16 |
| `Training+Coordinator` | 4 |
| `Education+Programs+Consultant` | 3 |
| `Instructional+Designer` | **0** |
| `Instructional+Technologist` | **0** |

`Instructional Designer` returning 0 confirms it's not a K-12 title — that lane is exclusively CSU/community-college work.

### Proposed query set (for JT to confirm)

Three queries per run, dedup across by `postingID`:

```
# Lane 1 — Adult Ed Teacher (primary CTE-credential match)
GET /Home/LoadJobs?rows=50&page=1&sort=postingDate&sortVal=0&order=desc
    &keywords=&location=&searchType=all
    &regions=9,27,29,31,34,37,44,58            # El Dorado, Monterey, Nevada, Placer, Sacramento, San Diego, Santa Cruz, Yolo
    &jobTypes=6                                  # Teacher - Adult Education
    &days=0&empType=0&catID=0&onlineApps=0&recruitmentCenterID=0
    &stateID=24&regionID=0&districtID=0&searchID=0

# Lane 2 — CTE keyword sweep (broader; same county scope)
GET /Home/LoadJobs?...&keywords=CTE&jobTypes=0&...

# Lane 3 — Career Technical Education (full phrase) keyword sweep
GET /Home/LoadJobs?...&keywords=Career%20Technical%20Education&jobTypes=0&...
```

Page 1 only for v1 (capped at `rows=50`); paginate when results consistently exceed 50/query.

Optional follow-on lanes (defer until JT signals):

- **Curriculum / Training Coordinator combined** — `keywords=Curriculum+Coordinator` or `keywords=Training+Coordinator`. Low volume (~4-16 records each). Could combine into Lane 2 as a single OR-by-keyword query if EdJoin supports it; unverified.
- **Community college IT roles** — `catID=3` (Classified) restricted to community college districts. EdJoin doesn't have a "community college" filter; requires post-fetch employer matching.

### Remote lane (per JT's between-recon-and-ops side task)

EdJoin contributes two keyword sweeps:

```
GET /Home/LoadJobs?...&keywords=remote&...           # 29 records, mostly Sped hybrid
GET /Home/LoadJobs?...&keywords=virtual%20teacher&...# 130 records
```

Dedup against the main lanes by `postingID`. No structured flag to flip.

---

## 6. Pension Implications

EdJoin spans employer types with different pension treatments. Routing per-posting through the right allow-list / disallow-list will need an employer-type column on `data/employers.yaml` over time. For now, recon-level observations:

| Employer type | Typical pension | EdJoin signal |
|---|---|---|
| K-12 districts — credentialed teachers / counselors / librarians | **CalSTRS** | `catID=1` or `2` + districtName ending in "Unified", "Elementary", "Joint", "Union", "High School District", etc. |
| K-12 districts — classified staff (IT, clerical, custodial) | **CalPERS** | `catID=3` or `4` at K-12 districts |
| County Offices of Education (e.g., "Sacramento County Office of Education") | CalPERS (most) or CalSTRS for credentialed admin staff | `isRecruitmentCenter=true` flag in API + districtName |
| Charter schools | Varies — many opt out of CalSTRS/CalPERS entirely | districtName usually contains "Charter" |
| Community colleges | CalSTRS (credentialed faculty) or CalPERS (classified) | districtName containing "Community College District" / "Community College" |
| Private schools | None | districtName + listing on EdJoin (rare; verify per-employer) |

**JT's status:** 2–3 years CalSTRS service from 2017–2020. CalSTRS gap exceeds the 6-month reciprocity window (currently >5 years gap). Verifying whether STRS↔PERS reciprocity is establishable for that account is a **separate to-do (not for recon)** — flagged in `data/inventory.py` PENSION block already.

**Implication for `filter.py` / `employers.yaml`:** v1 should NOT hard-reject any EdJoin posting on pension grounds. Add a per-employer pension-system tag over time. Risk that JT applies to a charter school with no pension is acceptable for v1 — those are rare in EdJoin and the scorer's title/keyword logic will deprioritize them anyway.

---

## 7. Geographic Scope

### JT-target counties (verified countyIDs from `LoadRegions?states=24`)

| countyID | County | Currently open postings | JT-relevance |
|---|---|---|---|
| 34 | Sacramento | 856 | Primary metro |
| 58 | Yolo | 245 | Sacramento metro |
| 31 | Placer | 383 | Sacramento metro |
| 9 | El Dorado | 166 | Sacramento metro |
| 27 | Monterey | 595 | CSUMB-adjacent (target) |
| 44 | Santa Cruz | 188 | UCSC town (target) |
| 37 | San Diego | 1,319 | Oceanside + San Marcos region |
| 29 | Nevada | 63 | Truckee/Tahoe corridor (target) |

**Total CA postings system-wide:** 19,011 across 59 counties. Hawaii is NOT covered by EdJoin (it's a CA-centric board, though `stateID` parameter exists and accepts non-24 values — out of scope).

`regions=` parameter accepts a CSV of countyIDs and applies OR logic. Single query covers JT's entire geographic footprint: `regions=9,27,29,31,34,37,44,58`.

---

## 8. Volume Estimate

| Slice | Live (2026-06-03) | Estimated daily-new | Notes |
|---|---|---|---|
| All CA EdJoin postings | 19,011 | ~150-300 new/day | Massive board; most out of JT's lane |
| JT-target counties (8 above), all jobTypes | ~3,815 currently open | ~30-60 new/day | Geography cut |
| JT-target counties + jobTypeID=6 (Adult Ed Teacher) | needs query (estimate ~10-30 currently open) | ~0-2 new/day | Tight lane |
| `keywords=CTE` system-wide | 197 currently open | ~1-3 new/day | Keyword scan |
| `keywords=Adult+Education` system-wide | 169 currently open | ~1-3 new/day | Keyword scan |

**Estimated JT-relevant contribution after dedup + hard filters:** 1-3 new postings per day on a typical run, 5-10 on a busy hiring-cycle day (back-to-school in late summer).

EdJoin's strength is the CTE / Adult Ed teaching pipeline that's underrepresented in CalCareers (no CalCareers track for K-12) and USAJobs (federal only). Even at low daily volume, one well-matched CTE Adult Ed posting per week is meaningful for JT.

---

## 9. Risks

### Low-impact, manageable
- **JSON API field naming case-sensitivity.** Azure Cognitive Search backend; `sort` field name must match the indexed property case. `postingDate` works; `PostingDate` doesn't. Document the working values in code and avoid the temptation to "fix" the casing.
- **Microsoft JSON date format.** `/Date(epoch_ms)/` — parse manually, no library helper. Sentinel value for "unset" is `/Date(-62135568000000)/` (`0001-01-01`).
- **NullReferenceException on bad params.** If you forget a query-string param or send an unparseable value, the API returns a 500 with a full stack trace (information disclosure on EdJoin's side — useful for debugging, not our problem). Defensive coding: always include every param from `buildSearchQueryString()` with sensible defaults.
- **Salary in free-text prose.** No structured field consistently filled; need a regex pack like CSU. Patterns to seed: explicit monthly range, annual range, hourly range, "Placement on X Salary Schedule" (defer to salary-schedule URL parsing), "Pay dependent on experience" (None). Expand based on first-week observation.

### Medium-impact, monitor
- **Detail-page HTML structure drift.** EdJoin uses ASP.NET MVC Razor templates with relatively stable `<h5>Label</h5><div>value</div>` patterns, but template updates could move things. Mitigation: validate parsed-field non-emptiness per posting and log structured warnings on miss.
- **Volume spikes around hiring cycles.** Mid-July through August routinely sees 5-10x normal posting volume on EdJoin (back-to-school). Our `rows=50` page-1-only design will start to miss postings during these periods. Watch and paginate when needed.
- **No `robots.txt`, no published `Terms`.** Implicit allow per convention, but JT may want to read `/Home/Privacy` before commit. Personal job-search posture is well within typical use.

### Higher-impact, less likely
- **Backend migration.** EdJoin is on ASP.NET MVC 5.3 + Azure Cognitive Search. If Sacramento County Office of Education (which runs EdJoin) modernizes to a different platform, the API contract dies. Probability low — EdJoin has been stable for 15+ years. Watch for: `X-AspNetMvc-Version` header change, sudden JSON shape changes.
- **API endpoint rename or auth requirement.** EdJoin could rename `/Home/LoadJobs` or add a CSRF token requirement (similar to CalCareers ViewState). Currently it accepts plain `XMLHttpRequest`-flagged GETs from any origin. Probability low.

### Critical risks — NONE detected
- No CDN-fronting (no CloudFront, Cloudflare, Akamai, AWS WAF). Plain IIS origin servers.
- No bot mitigation under sustained-pace probing (25/25 at 2s intervals — the CSU lesson applied directly).
- No login wall for browsing/searching.
- No reCAPTCHA on the data endpoints.

If EdJoin ever turns on CDN-level bot mitigation later, the move to Playwright would be the same as for CSU. Until then, `requests` + `BeautifulSoup` is the right stack.

---

## 10. Open Items for JT

1. **Privacy Policy review.** EdJoin has no published TOS — only `/Home/Privacy`. Quick read recommended before commit to confirm personal job-search posture is welcome. Recommendation: **add a 60-second TOS/Privacy review to the commit checklist.**

2. **Charter school inclusion.** EdJoin lists charter schools that opt out of CalSTRS/CalPERS entirely (no pension continuity). Should `filter.py` reject charter postings up front, or let them through and rely on the scorer to deprioritize? Recommendation: **let them through for v1**; revisit if noise becomes a problem. Many charters are CalSTRS-covered; blanket rejection is too coarse.

3. **Community college vs K-12 distinction.** EdJoin doesn't expose a "community college only" filter. The signal lives in `districtName` substring (`"Community College"` / `"Community College District"`). Should we maintain a separate community-college-allow-list under `data/employers.yaml`, or treat them uniformly with K-12? Recommendation: **uniform v1**; specialize when the corpus grows.

4. **Daily volume planning.** Current `rows=50` page-1-only design will miss postings during mid-July through August hiring spikes. Pagination should be wired before the back-to-school cycle starts. Recommendation: **build pagination into v1** rather than deferring like CSU/CalCareers — the cost is one extra loop and EdJoin's API is well-behaved enough to support it.

5. **Detail-fetch necessity per posting.** The listing-API JSON has enough for scoring at coarse precision (title, location, jobType, districtName). The detail-page body adds keyword-density signal and salary precision. For the daily run, fetching every candidate's detail page is ~3-5 minutes of wall clock. Acceptable but not free. Recommendation: **fetch details for all candidates in v1**; revisit if total run time matters. Could later add a Tier-1-style screening pass on listing-API JSON only, then detail-fetch only postings passing a `score >= floor`.

6. **Remote-lane integration.** The "explicit remote queries" side task you mentioned: EdJoin's contribution is `keywords=remote` (29 records, mostly Sped hybrid) and `keywords=virtual+teacher` (130 records). Should those queries run as part of the main EdJoin source on every run, or be flagged behind a separate "remote-track" config block in `data/sources.yaml`? Recommendation: **same source, additional queries**, dedup'd by `postingID`. Same pattern as CSU's three queries.

7. **CTE credential expiration handling.** JT's CTE credential expires February 2027. After expiration, the `target_titles_yes` entries for Adult Education Instructor, etc., should arguably drop out automatically. Currently inventory has no expiry awareness. Out of scope for THIS recon, but flag for ops-track planning.

8. **Multi-site postings.** Some EdJoin postings list multiple school sites within a district. Currently the recon proposes `all_locations = [location]` (single-element). For v1 this is fine. If a multi-site posting matters for JT's geo filter (e.g., posting spans Sacramento + Yolo), the source should iterate `<h5>Location</h5>` blocks. Defer to v1.1.

---

## 11. Decisions (locked 2026-06-03)

These mirror the format of csu-recon and calcareers-recon. Locked after JT reviewed §10 open items and the proposed scope-query / field-mapping / pagination spec. Privacy policy reviewed pre-commit (no scraping/automation/commercial-use prohibitions; defensive language only protects user PII against unauthorized third parties).

### 1. Platform / strategy

- **Site:** `https://www.edjoin.org/` (apex `edjoin.org` works identically; either is fine).
- **Tech:** Microsoft IIS 10.0 + ASP.NET MVC 5.3, backend Azure Cognitive Search.
- **Stack:** `requests` + `BeautifulSoup`. JSON API for listings; light HTML parse for detail-page body and salary text. **No Playwright.**
- **Bot mitigation:** None detected after 25 sustained fetches at 2s intervals (production cadence). The CSU lesson applied: this conclusion is based on sustained probing, not one-off curl.
- **Rate limit:** 2-second delay between requests; ~80-100 detail fetches per run × 2s = ~3-5 minutes wall-clock budget. Acceptable at daily cadence.

### 2. Scope queries

**Five queries per run, dedup across by `postingID` at `fetch_listings` level.** Lanes 4-5 (remote keyword sweeps) are part of the EdJoin source from day 1, not deferred to a separate remote-track refactor. Same dedup pattern as CSU's three queries.

```
# Lane 1 — Adult Ed Teacher (primary CTE-credential match)
keywords=&jobTypes=6&regions=9,27,29,31,34,37,44,58

# Lane 2 — CTE keyword sweep
keywords=CTE&jobTypes=0&regions=9,27,29,31,34,37,44,58

# Lane 3 — Career Technical Education (full phrase)
keywords=Career%20Technical%20Education&jobTypes=0&regions=9,27,29,31,34,37,44,58

# Lane 4 — Remote keyword sweep
keywords=remote&regions=0

# Lane 5 — Virtual teacher sweep
keywords=virtual%20teacher&regions=0
```

**Geographic param:** Lanes 1-3 use `regions=9,27,29,31,34,37,44,58` (JT's 8 CA target counties). Lanes 4-5 use `regions=0` (system-wide). Remote roles aren't geographically constrained — JT's overseas-eligible goal means a remote posting anywhere in CA is in-scope.

**Pagination:** see §11.7. Wire into the source class from day 1.

### 3. Geographic scope

`regions=9,27,29,31,34,37,44,58` covers: El Dorado, Monterey, Nevada, Placer, Sacramento, San Diego, Santa Cruz, Yolo. No additional location to add (San Marcos campus context = San Diego county = countyID 37, already covered). Hawaii is NOT on EdJoin.

### 4. jobType controlled vocabulary

Only one jobTypeID matches JT cleanly: **`6` (Teacher - Adult Education)**. Secondary IDs (`66`, `67`, `68` for Adult School management; `17` for generic Coordinator) are too broad or too senior. Build with `jobTypes=6` on Lane 1; use keyword sweeps for other lanes.

### 5. Salary patterns (to seed; iterate week 1)

EdJoin salary is free-text prose. Seed regex pack:

| Pattern | Action |
|---|---|
| `\$([\d,]+(?:\.\d{1,2})?)\s*[-–]\s*\$([\d,]+(?:\.\d{1,2})?)\s*per\s*month` | Both monthly |
| `\$([\d,]+(?:\.\d{1,2})?)\s*[-–]\s*\$([\d,]+(?:\.\d{1,2})?)\s*(?:per\s*year\|annually\|annual)` | Both annual ÷ 12 |
| `\$([\d,]+(?:\.\d{1,2})?)\s*[-–]\s*\$([\d,]+(?:\.\d{1,2})?)\s*per\s*hour` | None, None (let filter handle) |
| `Placement on .* Salary Schedule` | None, None + log district name for later schedule-URL follow-up |
| `Pay dependent on experience` | None, None |

Add patterns as observed misses surface.

### 6. Field mapping (precedence chain per field)

Listing-API JSON is already structured and is the right first source for fields it covers. JSON-LD on the detail page is the second source; raw DOM is the last resort. Per-field precedence:

| `Posting` field | Precedence chain |
|---|---|
| `source_job_id` | listing-API `postingID` |
| `title` | listing-API `positionTitle` → JSON-LD `title` fallback → DOM `<h2>` last resort |
| `employer` | listing-API `districtName` |
| `url` | constructed: `https://www.edjoin.org/Home/JobPosting/{postingID}` |
| `posted_date` | listing-API `postingDate` (parsed via §11.8 regex) → JSON-LD `datePosted` fallback |
| `salary_min` / `salary_max` | listing-API `PayRangeFrom` / `PayRangeTo` → JSON-LD `baseSalary` → regex on detail-page body using §11.5 patterns |
| `location` | listing-API `workSite` + `districtName` → `all_locations` as a single-element list for v1 |
| `all_locations` | `[location]` (single-element list; multi-site iteration deferred to v1.1 per §10 item 8) |
| `telework_flag` | keyword scan of `raw_text` for `"remote"` / `"virtual"` / `"hybrid"` / `"telework"`; default `False` |
| `raw_text` | detail-page DOM body (concatenate labeled sections: `Job Summary` / `Requirements / Qualifications` / `Comments and Other Information`). JSON-LD `description` is HTML-encoded and less clean for keyword scoring; do NOT use it as the primary `raw_text` source. |
| `classification` | `None` — EdJoin has no class-code system; teaching credentials are a separate axis we don't filter on |

### 7. Pagination

GET with `&page=N` and `&rows=50`. **Iterate until LoadJobs returns fewer than `rows=50` records** — one source of truth, resilient to `totalPages` being wrong. Hard safety cap at 20 pages (1,000 records per query — well beyond JT's expected volume) to prevent runaway loops.

### 8. Posted-date

Listing-API `postingDate` is in Microsoft JSON date format (`/Date(epoch_ms)/`). Parse via:

```python
import re
from datetime import datetime, timezone
m = re.match(r"^/Date\((-?\d+)\)/$", value)
ts_ms = int(m.group(1))
return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
```

Sentinel `/Date(-62135568000000)/` → `0001-01-01` → treat as None.

### 9. Telework

No structured field. Keyword-scan `positionTitle` + listing-API `JobSummary` + detail-page body for any of: `"remote"`, `"virtual"`, `"hybrid"`, `"telework"`. Default `False` if no match.

### 10. Sample fixture

The verified detail-page sample (postingID 2232159, Palo Alto Unified Elementary Teacher - 2nd Grade) is saved to `/tmp/edjoin_recon_sample.html`. Promote to `tests/fixtures/edjoin_sample.html` in Phase 2 (scraper build); not committed during recon.

---

## Sources cited

- Homepage: https://www.edjoin.org/
- Privacy: https://www.edjoin.org/Home/Privacy
- Search shell: https://www.edjoin.org/Home/Jobs?keywords=teacher&searchType=all
- JSON API: https://www.edjoin.org/Home/LoadJobs?... (parameter set per §2)
- Sample detail (probed live, 2026-06-03): https://www.edjoin.org/Home/JobPosting/2232159
- jobType lookup: https://www.edjoin.org/Home/GetJobTypesCriteria?jobtypes={ID}
- County list (CA): https://www.edjoin.org/Home/LoadRegions?states=24
- Frontend JS: https://www.edjoin.org/Scripts/pages/jobs.js
- Azure backend signal: `client-request-id`, `x-ms-client-request-id` headers leaked via 400 error body on bad sort field
