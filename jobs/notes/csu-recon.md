# CSU Careers Reconnaissance

**Date:** 2026-06-02
**Goal:** Map the CSU (California State University) jobs ecosystem before writing `crawler/sources/csu.py`. Cover all 23 campuses via a unified entry point.
**Status:** Investigation only — no code written. Verified against the live site via `curl`-only probes from a real Chrome UA. One live posting fetched and parsed by hand (jcid 557129, Information Security Analyst II at San Diego State).

---

## TL;DR

CSU runs a single shared **PageUp People** ATS instance (tenant 873) for all 23 campuses plus the Chancellor's Office. The portal lives at `https://csucareers.calstate.edu/` (note: `jobs.calstate.edu` 301-redirects to a SharePoint marketing page on `www.calstate.edu` that is Cloudflare-bot-challenged and useless to us — **don't use that URL**).

Search is **GET with query-string params** (like USAJobs, much simpler than CalCareers' POST+ViewState dance) over server-rendered HTML. Filter facets are a clean controlled vocabulary: `category`, `work-type`, `location`, `search-keyword`, with `page` and `page-items` for pagination. Detail pages render server-side, no JS required for parsing. No auth, no API key, robots.txt only walls off `/admin`, `/uat`, `/staging` etc. — public listing/job paths are fully open.

There is no public JSON endpoint (probed `/en-us/listing.json`, `/api/jobs`, `/api/listing`, query-param `?format=json` — all return 302 or fall through to the HTML pages). There are also no `ETag`/`Last-Modified` headers, so conditional GETs won't help us; each run does a fresh fetch.

**Recommendation:** Build with **`requests` + `BeautifulSoup`**. No Playwright needed for v1. Effort sits between USAJobs (easiest, JSON API) and CalCareers (hardest, ViewState POST). Estimated 2–3 hours of scraper work plus 1 hour of classification-mapping research.

Two specific gotchas worth flagging up front: (1) the IT Consultant Foundation/Career/Expert classification family does NOT always appear in the title — campuses typically advertise the working title (e.g., "Systems Administrator I") with the CSU class series sometimes parenthesized, sometimes not. JT's title scorer will undercount unless we widen the keyword scan to body text. (2) Salary has no structured field — it's prose inside the body, with multiple sentence patterns observed. Will need a regex pack.

---

## 1. Site Structure

### Base URLs

| URL | What it serves | Use? |
|---|---|---|
| `https://jobs.calstate.edu/` | 301-redirects to `www.calstate.edu/csu-system/careers/Pages/default.aspx` (SharePoint marketing page, Cloudflare bot challenge — returns HTTP 403 `cf-mitigated: challenge` to any non-browser client) | ❌ Don't use |
| `https://www.calstate.edu/csu-system/careers/Pages/default.aspx` | SharePoint marketing landing page; links out to the actual portal | ❌ Don't scrape; it's the redirect target only |
| **`https://csucareers.calstate.edu/en-us/listing/`** | The real listing endpoint — unified across all 23 campuses + Chancellor's Office. Server-side HTML, PageUp-backed | ✅ Primary search entry |
| **`https://csucareers.calstate.edu/en-us/search/?<filters>`** | The filtered search results | ✅ Use with params (see §2) |
| **`https://csucareers.calstate.edu/en-us/job/{NUMERIC_ID}/{slug}`** | Posting detail | ✅ Per-posting fetch |
| `https://csucareers.calstate.edu/cw/en-us/listing/` | Same data, different path. Probably "current workforce" (internal applicants); served via the same template. | Ignore — `/en-us/` is canonical for external. |
| `https://secure.dc4.pageuppeople.com/apply/873/...` | Apply gateway — application form lives here. `873` is the CSU tenant ID; `dc4` is PageUp's data center 4. | Don't scrape; this is the apply destination JT will click. |

Platform identifiers visible in the listing page:

- `<html data-ng-app="csu-app">` — AngularJS SPA shell wrapping PageUp content
- `<meta name="GENERATOR" content="Microsoft SharePoint" />` — masterpage chrome is hosted on CSU's SharePoint
- `<script src="https://careers-static.pageuppeople.com/Jobs-<hash>.js">` — PageUp client library
- `<script src=".../csujobs.js">` — CSU-specific Angular controller
- `sitemap.xml` returns: `<loc>http://careers.pageuppeople.com/</loc>` — explicit confirmation of PageUp underneath
- `data-sitekey="6LeC1A0UAAAAAPZK8E1CxJkweNzEiOuWfqJ63a0v"` — reCAPTCHA v2 site key, used on the apply form (not on search; we don't hit it)

### Auth

**None.** No registration, no API key, no OAuth. Direct GET works from any UA.

### Robots.txt

`https://csucareers.calstate.edu/robots.txt` — explicit and short:

```
User-agent: *
Disallow: /admin
Disallow: /awake
Disallow: /uat
Disallow: /cwuat
Disallow: /ciuat
Disallow: /ci
Disallow: /uatinternal
Disallow: /testint
Disallow: /staging
(plus uppercase variants and /*/<path>/ patterns)
```

All disallowed paths are admin/staging/UAT environments. **Public listing and posting paths are explicitly permitted.** Our scraper stays in `/en-us/listing/`, `/en-us/search/`, and `/en-us/job/`.

### Rate limits / bot protection

- **CloudFront** sits in front (`x-cache: Miss from cloudfront`, `via: 1.1 ...cloudfront.net`). No `cf-mitigated: challenge` triggers observed on listing or detail pages from a realistic UA.
- No `Retry-After` or rate-limit headers documented.
- The reCAPTCHA on apply pages doesn't gate search or detail.
- **Decision:** stick to our default 2-second delay between requests per `.cursorrules` §5. Should be well under any plausible bound.

### Search endpoint

| Verb | URL | Notes |
|---|---|---|
| GET | `/en-us/search/?<filters>` | Multi-param GET. Filters combine with AND across param names; same-named params (e.g., multiple `location` values) combine with OR. |

Confirmed filter parameters (from inspecting form inputs in `/en-us/listing/` and successful test queries):

| Param | Values | Multi-value | Notes |
|---|---|---|---|
| `search-keyword` | free text | ❌ single | Substring search across title + body; URL-encoded |
| `category` | controlled vocabulary (~60 values; see §3) | ✅ repeat the param | `category=Information+Systems+%26+Technology` |
| `work-type` | controlled vocabulary (13 values) | ✅ repeat | `work-type=Staff` is what we want; excludes Faculty/Student/Auxiliary |
| `location` | 23 CSU campuses + Chancellor's Office variants | ✅ repeat | E.g., `location=Sacramento`, `location=Monterey+Bay` |
| `page` | int ≥ 1 | ❌ single | 1-indexed |
| `page-items` | int (default 20, observed 40 on listing landing) | ❌ single | Page size. Default 20 in search results; pushing larger should work but unverified. |

Sample working URL (probed successfully — 20 results page 1, 13 page 2):

```
GET https://csucareers.calstate.edu/en-us/search/?category=Information+Systems+%26+Technology&work-type=Staff
```

### Pagination

Page-number model. `?page=2&page-items=20`. Pagination control in the rendered HTML shows only "Next" — no last-page hint, no total-count indicator in the markup we can grep for. **Strategy:** iterate `page=1,2,3,...` until a page returns zero job links, with a safety cap of (say) 20 pages. At our scope, queries should fit in 1–3 pages.

### Posting URL pattern

```
https://csucareers.calstate.edu/en-us/job/{NUMERIC_ID}/{slug}
```

- `NUMERIC_ID` — 6-digit (sometimes 5-digit) integer, monotonically increasing per posting (PageUp's internal job ID; e.g., `557129`, `558440`). Stable. Use as `source_job_id`.
- `slug` — title-derived, lowercase, hyphenated. Cosmetic; only `NUMERIC_ID` matters for stability.

Detail pages return HTTP 200 directly with no auth, redirects, or JS challenge.

### Caching

No `ETag` or `Last-Modified` on listing or detail pages. Only `cache-control: private`. **Conditional GETs won't reduce our work** — every run re-fetches each posting in scope.

---

## 2. Sample Posting

**Verified live (2026-06-02):** [Information Security Analyst (Information Security Analyst II) — San Diego State, jcid 557129](https://csucareers.calstate.edu/en-us/job/557129/information-security-analyst-information-security-analyst-ii)

### Detail-page meta block (verified)

```html
<h2>Information Security Analyst (Information Security Analyst II)</h2>
<p>
  <span style="float:right"><a class="apply-link button" href="https://secure.dc4.pageuppeople.com/apply/873/gateway/default.aspx?c=apply&lJobID=557129&lJobSourceTypeID=805&sLanguage=en-us">Apply now</a></span>
  <b>Job no:</b> <span class="job-externalJobNo">557129</span><br>
  <b>Work type:</b> <span class="work-type staff">Staff</span><br>
  <b>Location:</b> <span class="location">San Diego</span><br>
  <b>Categories:</b> <span class="categories">Unit 9 - CSUEU - Technical Support Services, Probationary, Full Time, Information Systems &amp; Technology, Telecommute eligible (work onsite as scheduled and/or as requested and telecommute as scheduled)</span><br>
</p>
<p>
  <b>Advertised:</b> <span class="open-date"><time datetime="2026-05-15T16:00:00Z">May 15 2026</time></span> Pacific Daylight Time<br>
  <b>Applications close:</b>  
</p>
<div id="job-details"> ... full prose body ... </div>
```

Notable: `Applications close:` was empty for this posting — "open until filled" is common in CSU. Some postings have a date; many don't.

### Field mapping (verified against jcid 557129)

| Our `Posting` field | DOM source | Notes |
|---|---|---|
| `source_job_id` | `span.job-externalJobNo` text | E.g., `"557129"`. Also embedded in the URL path. Use the span — it's the canonical PageUp posting ID. |
| `title` | `<h2>` immediately preceding the meta block | E.g., `"Information Security Analyst (Information Security Analyst II)"`. The parenthetical is often the CSU classification — keep it in `title`; the scorer's title-pattern matcher reads both `title` and `classification`. |
| `classification` | parsed from `title` parenthetical when present | Heuristic: regex `\(([^)]+)\)\s*$` on title. NOT a structured field on the page. May be `None` when the working title doesn't include a parenthetical. See §3 — IT Consultant Foundation/Career/Expert often shows up here, but not always. |
| `employer` | derived from `span.location` via campus → university name map | E.g., `"San Diego"` → `"San Diego State University"`. The location IS the campus, and the campus IS the employer in CSU's structure. Build a lookup table once; 23 campuses + Chancellor's Office variants. |
| `url` | constructed: `https://csucareers.calstate.edu/en-us/job/{source_job_id}/{slug}` | Slug-stripped form `.../en-us/job/{ID}` may also work — needs probe verification. |
| `salary_min` / `salary_max` | regex over body text — **no structured field** | E.g., `"$6,492/month"` extracted from "Initial step placement is not expected to exceed Step 1 ($6,492/month)". Other phrasings observed in other postings: "Anticipated Salary: $X,XXX – $Y,YYY per month", "Salary Range: $X,XXX–$Y,YYY", "$XXX,XXX – $YYY,YYY annually". Pack of 3-5 regex patterns. If none match → `(None, None)` + log. Same Range-A-conservative posture as CalCareers. |
| `location` | `span.location` text | Campus name (e.g., `"San Diego"`, `"Monterey Bay"`). Not a city name — see §3 location mapping for the filter implications. |
| `all_locations` | `[location]` (single-element list) | Multi-campus CSU postings are rare; defer multi-element handling. |
| `telework_flag` | substring match in `span.categories` text | True if categories contains any of: `"Telecommute eligible"`, `"Remote in-state eligible"`, `"Remote out-of-state eligible"`. False if contains `"On-site"`. Default keyword scan of body as fallback for postings without any of those (rare). |
| `raw_text` | concat with section headers: `<h2>` title + meta paragraph (Job no/Work type/Location/Categories/Advertised) + `<div id="job-details">` inner text | The bulk of scoring signal lives in `#job-details`. Categories string also matters — it carries union/bargaining-unit info that maps to classification series. |
| `posted_date` | `time[datetime]` inside `span.open-date` | E.g., `<time datetime="2026-05-15T16:00:00Z">`. Parse the `datetime=` attribute as ISO 8601 → date. **This is a structured field, unlike CalCareers** — no first_seen_at fallback needed. |

### Salary parsing complexity

Salary is the messiest part. Observed patterns across the IT category sweep (informal scan, not exhaustive):

| Pattern | Example | Notes |
|---|---|---|
| Single step ceiling | `"Initial step placement is not expected to exceed Step 1 ($6,492/month)"` | The number is the **max** of the initial placement, not a range. Treat as both min and max if no other range present. |
| Explicit monthly range | `"Anticipated Salary Range: $5,025 – $9,425 per month"` | Standard. Parse both. |
| Annual range | `"$92,000 – $116,000 annually"` | Some MPP roles. Convert to monthly: `/ 12`. |
| Hourly | `"$25.00 – $35.00 per hour"` | Student/temp roles; should be filtered out at scoring boundary OR rejected via the salary floor. |
| "Salary commensurate with experience" | Free text, no number | `(None, None)` + log. Pass salary floor by default (None semantics in `filter.py`). |

The scraper should try patterns in order, take the first match, and log when no pattern matches so we can add more.

---

## 3. Scoping — Which Subset to Pull

### Why scope at all

CSU has ~600–1,000 open postings systemwide at any time, but the vast majority are Faculty, Student Assistant, Coach, or Auxiliary — none in JT's lane. Hard-filtering at query time rather than after fetch saves cycles and rate-limit budget.

### Primary filter — IT lane

```
GET /en-us/search/?category=Information+Systems+%26+Technology&work-type=Staff&page=N
```

Returns 20 results per page. Total IT-staff postings observed on 2026-06-02: ~33 across pages 1+2 (page 1: 20, page 2: 13).

### Secondary filter — Instructional Designer / Training Coordinator lane

These don't sit cleanly under `Information Systems & Technology`. Categories most likely to contain them:

- `Education Support Professionals` — contains Instructional Designer postings
- `Information Systems & Technology` — also contains some Instructional Designer roles (overlap)
- `Administrative` — has Training Coordinator–style roles occasionally

**Strategy:** run a second query with `search-keyword=Instructional+Designer` (no category filter — let the keyword match across category boundaries) and a third with `search-keyword=Training+Coordinator`. Dedup against the IT result set by `source_job_id`.

### Sample category values (controlled vocabulary, ~60 total)

Full list pulled from filter UI. JT-relevant:

- ✅ **Information Systems & Technology** — primary IT bucket
- ✅ **Education Support Professionals** — Instructional Designer territory
- ✅ **Administrative** — Training Coordinator territory (sometimes)
- 🟡 **Telecommute eligible / Remote in-state / Remote out-of-state** — these ARE categories too (slightly odd, but useful as the source-of-truth for `telework_flag`)
- ❌ Faculty - * (15 sub-categories) — JT's preferences don't include classroom teaching beyond CTE
- ❌ Coach / Athletics / Custodial / Trades — out of scope
- ❌ Health Professionals / Counselor / Research / Library — out of scope

### Work-type filter

13 values. We want `Staff`. Possibly also `Management (MPP)` if JT is open to administrative-grade roles. Excluded by default:
- `Auxiliary` (typically nonprofit affiliated entities — no CalPERS continuity)
- `Student Assistant`, `Graduate Assistant`, `Instructional Student Assistant`
- `Instructional Faculty - Temporary/Lecturer`, `Tenured/Tenure-Track`, `Visiting Faculty`
- `Extended Education Instructor`, `Teaching Associate`, `Research Fellows`
- `Non-Instructional Faculty (Coach/Counselor/Librarian)`

### Location filter — 23 campuses + Chancellor's Office

JT-relevant per his `target_locations` and per the spec's named-campus list:

| CSU Location filter value | JT target_location match | Notes |
|---|---|---|
| `Sacramento` | ✅ "Sacramento" | Sacramento State |
| `Chancellor's Office - Sacramento` | ✅ "Sacramento" | CO has a Sacramento posting site |
| `Monterey Bay` | ✅ "Monterey" (substring) | CSUMB |
| `San Marcos` | ⚠️ NOT in current target_locations | Cal State San Marcos (north San Diego county). Spec names this campus. Recommend adding "San Marcos" to inventory. |
| `Channel Islands` | ⚠️ NOT in current target_locations | CSUCI (Ventura county). Spec names this campus. Recommend adding. |
| `San Diego` | partial — Oceanside in target_locations | SDSU itself is San Diego proper, not Oceanside. Recommend adding "San Diego" if JT is open to it. |
| `San Diego - Imperial Valley` | partial — same | Border campus. Recommend adding if JT is open. |
| All others (Bakersfield, Chico, Dominguez Hills, East Bay, Fresno, Fullerton, Humboldt, Long Beach, Los Angeles, Maritime, Northridge, Pomona, San Bernardino *, San Francisco, San José *, Sonoma, Stanislaus *, Cal Poly SLO/Solano, Mustang Business Park) | ❌ outside target | Out of geography. |

### CSU IT Consultant classification family — IMPORTANT FINDING

**JT specified "IT Consultant Foundation / Career / Expert" as the primary classification family.** This IS a real CSU classification (CSU class spec series `0420`). However:

- On the public job board, posting **titles** typically use the **working title**, not the official classification name.
- For example, page 1 of the IT-staff filter returned: `"Systems Administrator I"`, `"Software Developer II"`, `"Information Security Analyst II"`, `"Technology Support Specialist II"` — none used `"Information Technology Consultant"` in the title.
- The parenthetical sometimes contains the working-title level (`(Information Security Analyst II)`) but not the underlying CSU class series.
- **The CSU class series does not appear in the structured DOM** (verified empirically against jcid 557129: zero occurrences of `"Information Technology Consultant"` or `"IT Consultant"`; no `Classification:` labeled field; no `class-spec` / `class-series` / `class-code` block; only `"CSU Classification Salary Range: $X-$Y per month"` in body prose, naming the range but not the class). **Inferable only from working title or via narrow-widened `target_titles_yes`** — see locked decisions.

**Implication for `score.py` title-pattern matcher:** if `target_titles_yes` keeps "IT Consultant (CSU)" as a YES entry, the regex won't match these titles. Two paths:

1. **Widen target_titles_yes** to include the CSU working-title surface forms: `"Systems Administrator"`, `"Software Developer"`, `"Technology Support Specialist"`, `"Business Systems Analyst"`, `"Information Security Analyst"`. Increases recall but risks scoring federal cybersec roles (out of scope) higher.
2. **Match `"Information Technology Consultant"` against `raw_text`** (not just title/classification fields), and accept the precision drop on USAJobs side as acceptable.

**Recommend path #1** — widen `target_titles_yes`. Most CSU IT-staff working titles map to roles JT could plausibly take. The `target_titles_no` list already drops the senior/expert-cert noise.

Same logic applies to **Instructional Designer / Training Coordinator** — both already appear in `target_titles_yes` and the titles in CSU listings match cleanly (`"Instructional Designer"`, `"Instructional Designer, Center for Teaching & Learning"`, etc.).

### Sample target search (full)

```
# IT lane
GET https://csucareers.calstate.edu/en-us/search/
    ?category=Information+Systems+%26+Technology
    &work-type=Staff
    &page=1
    &page-items=50

# ID/Training lane (overlaps IT, dedup by source_job_id)
GET https://csucareers.calstate.edu/en-us/search/
    ?search-keyword=Instructional+Designer
    &work-type=Staff
    &page=1

GET https://csucareers.calstate.edu/en-us/search/
    ?search-keyword=Training+Coordinator
    &work-type=Staff
    &page=1
```

Headers: just a realistic UA. `Accept: text/html`. No auth.

---

## 4. Volume Estimate

| Slice | Page-1 observed (2026-06-02) | Estimated daily-new | Notes |
|---|---|---|---|
| All IT-staff systemwide | ~33 currently open | 1–3 new/day | Page 1 + page 2 union of unique `source_job_id`s |
| Filtered to JT's target campuses (Sacramento, Monterey Bay, San Marcos, Channel Islands) | ~5–8 currently open | <1 new/day | Geography is the biggest cut |
| ID/Training systemwide | ~10–15 currently open | <1 new/day | Across `Instructional Designer` + `Training Coordinator` keyword sweeps |

**Order of magnitude:** CSU contributes maybe 1–3 new postings per day to JT's surface after filtering. Same order as USAJobs Sacramento-metro slice. Both significantly fewer than CalCareers IT/SSA (10-12/day).

CSU's strength isn't volume — it's that it's the cleanest CalPERS-continuing employer pool in JT's geography (Sacramento State faculty/staff stay CalPERS through the Chancellor's Office reciprocity). One good CSU hit per week is more valuable than five USAJobs hits to bring on the page.

---

## 5. Risks

### Low-impact, expected
- **No JSON, must parse HTML.** Server-rendered, no JS, no Ajax dependency. BeautifulSoup is sufficient. Effort cost ~1 hour to write the parser.
- **No conditional GETs.** No `ETag` or `Last-Modified`. Each run re-fetches every posting in scope. At ~30 IT + ~15 ID postings × 1.5 pages of search results, we're at ~50 HTTP requests per run. At 2-second delay, ~100 seconds of wall clock. Acceptable.
- **Total count not in the page markup.** We iterate `page=N` until a page returns zero job links. Safety cap at 20 pages.

### Medium-impact, monitor
- **PageUp version bump.** PageUp ships JS/CSS with versioned cache-busting hashes. CSU's customization sits in `csujobs.js` on the CSU server. If PageUp restructures the meta-block HTML (specifically `span.job-externalJobNo`, `span.work-type`, `span.location`, `span.categories`, `span.open-date > time`), our parser breaks silently — empty fields, no exception. **Mitigation:** validate the parsed fields aren't empty after each fetch; log a structured warning if a span we expect is missing.
- **Salary parsing precision.** No structured field. We rely on a regex pack against body prose with ~5 patterns. Postings without a parseable salary get `(None, None)` and pass the salary floor by default (per `filter.py` rule 1 semantics). When we add real CSU posting volume to the audit view, expect to add 1–3 more regex patterns in the first month based on observed misses.
- **Working-title vs classification mismatch.** As detailed in §3, the CSU Information Technology Consultant classification often isn't in the title text. Scorer needs widening (recommended path #1 in §3) before this source contributes well.
- **CloudFront cache pollution.** CloudFront in front of csucareers might serve stale results for ~30 seconds after a CSU edit. Negligible at our daily cadence.

### Higher-impact, less likely
- **Tenant migration.** PageUp tenant `873` is what we're seeing today. If CSU moves to a different ATS (Workday, Oracle, etc.), the whole scraper dies. The Chancellor's Office's CHRS Recruiting program standardized CSU on PageUp; a near-term migration is unlikely. **Watch:** if the `csujobs.js` URL or the tenant ID in apply-link URLs changes, dig in.

### Critical — AWS WAF JS-challenge (discovered 2026-06-02 Phase-2 live-demo probe)
- **csucareers.calstate.edu is fronted by CloudFront + AWS WAF with the `AwsWafIntegration` JS-challenge.** Under sustained `requests`-style automated load, the WAF returns a **2464-byte canned challenge page** (HTML with `<script src="…token.awswaf.com/…/challenge.js">` and an `AwsWafIntegration.getToken()` reload loop) instead of the posting body. Tripped during the Phase-2 live demo: of 33 IT-staff jcids, only the first detail fetch returned real HTML; the next 28 all returned the WAF challenge stub. Search-results queries 2 and 3 also tripped, returning 0 jcids each. Threshold appears to be ~2 fetches per ~2 seconds on the detail-page path; one-off `curl` probes (the recon's only data shape until live demo) never hit it.
- **Original recon assessment was wrong.** The "Cloudflare bot challenge — not currently active" note above was based on one-off probes. The real production behavior under crawler load is the opposite: WAF challenge is *active and reliable* against automated `requests` traffic. Diagnostic evidence: all 28 failing detail responses had identical 2464-byte length and identical AWS WAF challenge body.
- **Mitigation: Playwright (locked Decision #1, revised 2026-06-02).** Headless Chromium executes the WAF JS, obtains a valid `aws-waf-token`, and subsequent navigations within the same browser context pass. This matches CalCareers's pre-existing Playwright pattern (spin-up-per-run, one browser for the whole fetch). Cost: ~2–4s per navigation vs `requests`'s ~0.3s, so wall-clock cost for ~50 navigations rises to ~3–5 minutes from ~100s. Acceptable at daily cadence.
- **Watch list.** If WAF behavior changes (token TTL shortens, or the `awswaf.com` challenge.js URL changes), Playwright may need updating. Re-probe whenever the scraper produces empty runs.

### Out of scope for v1
- Per-campus apply experience (PageUp routes apply through `secure.dc4.pageuppeople.com/apply/873/...`; we link to the apply URL but JT submits manually per spec).
- The internal `/cw/en-us/listing/` (current workforce) path — duplicate data, no value.
- Auxiliary corporations (the Cal Poly Foundation, ASI, etc.). These get coded as `work-type=Auxiliary` and we exclude them anyway.

---

## 6. Open Items for JT

1. **Add "San Marcos", "Channel Islands", "San Diego" to `inventory.PREFERENCES.target_locations`?**
   - Recommendation: **Yes** for San Marcos and Channel Islands (the spec named them explicitly for CSU). **Optional** for San Diego itself — JT to confirm whether SDSU (city of San Diego proper) is acceptable in addition to Oceanside.
   - Confirms: JT signs off in the next inventory edit.

2. **Widen `target_titles_yes` to include CSU working titles?**
   - The CSU IT Consultant Foundation/Career/Expert classification rarely appears in titles. Most IT-staff CSU postings use working titles like "Systems Administrator", "Software Developer", "Technology Support Specialist", "Business Systems Analyst", "Information Security Analyst".
   - Recommendation: add a `target_titles_yes` extension with the surface working titles. Keep `"IT Consultant (CSU)"` as a stale entry for documentation; the new entries do the work.
   - Risk: also up-weights USAJobs federal cybersec roles. Manageable — the `target_titles_no` already has CISSP-gated roles, and the salary floor cuts the rest.
   - Confirms: JT picks the title list.

3. **Include `work-type=Management (MPP)` alongside Staff?**
   - MPP = Management Personnel Plan, CSU's administrative-grade ladder above Staff. Some MPP roles (Director of IT, IT Operations Manager) align with JT's trajectory; others are senior leadership beyond his level.
   - Recommendation: start with `Staff` only. Add MPP if Staff yields too thin a result set after a week of runs.
   - Confirms: JT signs off; defer revisit.

4. **Salary parsing fallback policy.**
   - When the regex pack misses, salary is `(None, None)` and the posting passes the salary floor by default. This matches USAJobs behavior for missing-salary postings.
   - Recommendation: keep current behavior. Log every regex miss so we can grow the pack in week 1.
   - Confirms: JT signs off.

5. **Per-page result count (`page-items` param).**
   - Default is 20 on `/en-us/search/`. Listing landing renders 40. Pushing larger (e.g., `page-items=100`) is technically possible but unverified — PageUp may cap it.
   - Recommendation: use `page-items=50` and iterate `page=1,2,...` until zero results. Verify the cap during first probe.
   - Confirms: defer to scraper-build phase.

---

## 7. Decisions (proposed — JT to confirm before scraper build)

1. **Library:** `requests` + `BeautifulSoup`. No Playwright in v1. (Confirmed by recon: pure SSR HTML, GET search, no JS rendering required.)
2. **Source name:** `csu` (matches existing `usajobs`, `calcareers` naming).
3. **Tenant identifier:** PageUp tenant `873` is implicit in the apply URL; we don't need to encode it ourselves since we go through `csucareers.calstate.edu`. Note it in code comments for context.
4. **Query strategy:** three queries per run, dedup by `source_job_id`: (a) `category=Information+Systems+%26+Technology&work-type=Staff`, (b) `search-keyword=Instructional+Designer&work-type=Staff`, (c) `search-keyword=Training+Coordinator&work-type=Staff`. Add to `data/sources.yaml` under a `csu:` block, mirroring the existing `usajobs:` and `calcareers:` shape.
5. **Detail fetch:** one GET per posting, server-side HTML. Parse with BS4 selectors documented in §2.
6. **Posted-date proxy:** structured `time[datetime]` attribute is present — no `first_seen_at` fallback needed (unlike CalCareers).
7. **Rate limit:** 2-second default between requests, sequential pagination, no parallelism within source.
8. **Empty-scrape protection:** if the IT-staff query returns zero job links AND there are no JT-target-location postings on page 1, treat as scraper failure per CLAUDE.md "Empty Scrapes Never Overwrite Good Data". Keep prior run's data; log + raise.

---

## Sources cited

- Listing landing page: https://csucareers.calstate.edu/en-us/listing/
- robots.txt: https://csucareers.calstate.edu/robots.txt
- sitemap.xml: https://csucareers.calstate.edu/sitemap.xml (single URL: http://careers.pageuppeople.com/ — confirms PageUp)
- Sample posting (probed live): https://csucareers.calstate.edu/en-us/job/557129/information-security-analyst-information-security-analyst-ii
- PageUp careers static asset: https://careers-static.pageuppeople.com/Jobs-<hash>.js (URL observed in listing HTML)
- Apply gateway pattern: https://secure.dc4.pageuppeople.com/apply/873/gateway/default.aspx?c=apply&lJobID={ID}&lJobSourceTypeID=805 (URL observed in apply button)

---

## Decisions (locked 2026-06-02)

These supersede the proposed decisions in §7. Locked after JT reviewed the §2 classification-location grep evidence and the salary-pattern list.

### Probe corrections (2026-06-02, post-Phase-2 live demo)

The Phase-2 live demo (CSU added to `scripts/render_demo.py`, run end-to-end against production) surfaced one critical recon error. See §5 "Critical — AWS WAF JS-challenge" for full diagnostic evidence.

- **§5 risk reassessment.** "Cloudflare bot challenge — not currently active" was wrong. csucareers.calstate.edu runs AWS WAF (CloudFront-fronted) with the `AwsWafIntegration` JS-challenge active on detail and search paths under sustained automated load.
- **Decision #1 revised.** Stack switched from `requests` + `BeautifulSoup` to **Playwright (headless Chromium)** for both search and detail navigations. Pure parser functions are unchanged.

### 1. Platform / strategy

- **Site:** `https://csucareers.calstate.edu/` (NOT `jobs.calstate.edu` — that 301-redirects to a Cloudflare-challenged marketing page that returns HTTP 403 `cf-mitigated: challenge` to non-browser clients).
- **Tech:** PageUp People, tenant `873`, shared across all 23 campuses + Chancellor's Office.
- **Stack:** **Playwright (headless Chromium) — required for both search and detail fetches due to AWS WAF JS-challenge integration. Decision revised 2026-06-02 per WAF probe; see §5.** Pure parser functions (`parse_posting`, `parse_search_results`, `_parse_salary`, etc.) are stack-agnostic and unchanged from the requests-era design.
- **Rate limit:** 2-second delay between navigations; ~50 navigations per run (3 search pages + ~45 detail pages) at ~3–5s wall clock each puts a run at ~3–5 minutes. Acceptable at daily cadence.

### 2. Scope queries

Run in this order, dedup across all by `source_job_id`:

```
GET /en-us/search/?category=Information+Systems+%26+Technology&work-type=Staff&page-items=50
GET /en-us/search/?search-keyword=Instructional+Designer&work-type=Staff&page-items=50
GET /en-us/search/?search-keyword=Training+Coordinator&work-type=Staff&page-items=50
```

**Page 1 only for v1.** Paginate when results consistently exceed `page-items=50`.

### 3. Geographic scope

Use the existing `inventory.PREFERENCES.target_locations` PLUS one addition: **"San Marcos"** (CSU San Marcos — about 15 mi inland from Oceanside, same region as JT's existing Oceanside entry). **Channel Islands skipped** — not on JT's wishlist despite spec mention.

### 4. Classification handling — path #1 (narrow widening)

The CSU IT Consultant classification (`Information Technology Consultant - Foundation / Career / Expert`) **does not appear in the structured DOM** (empirically verified against jcid 557129). Going **path #1**: narrow-widen `target_titles_yes` GLOBALLY with the surface working titles that advertise this work. These apply across sources, not only CSU.

**Added:**

- Systems Administrator
- Software Developer
- Programmer
- Programmer Analyst
- Systems Analyst
- Web Developer
- Application Analyst
- Information Technology Consultant
- Instructional Technologist

**Deliberately NOT added:**

- **Information Security Analyst** — credential gap (CSU postings often imply CISSP-track; lands closer to `target_titles_no` than `_yes`).
- **Technology Support Specialist** — helpdesk-leaning, generally below JT's grade trajectory.

JT will re-evaluate after the first week of live runs if the recall/precision tradeoff needs adjustment.

### 5. Salary pattern priority

Parse body prose in this order, take **first** match:

| # | Pattern | Action |
|---|---|---|
| 1 | `"Anticipated Salary Range: $X – $Y per month"` | both min/max |
| 2 | `"$X – $Y annually"` | both min/max ÷ 12 |
| 3 | `"CSU Classification Salary Range: $X-$Y per month"` | both min/max |
| 4 | `"Initial step placement is not expected to exceed Step 1 ($X/month)"` | min only |
| 5 | `"$X – $Y per hour"` | salary `(None, None)` (let filter handle; usually student/temp anyway) |
| 6 | `"Salary commensurate with experience"` | `(None, None)` |

Log every posting where no pattern matches; iterate the pack as patterns surface in production.

### 6. Field mapping (grounded in jcid 557129 — see §2)

- `source_job_id` ← `span.job-externalJobNo` text
- `title` ← `<h1>` in posting detail (working title)
- `posted_date` ← `<time datetime="...">` attribute (structured ISO 8601)
- `telework_flag` ← substring match in `span.categories` against `"Telecommute eligible"`, `"Remote in-state eligible"`, `"Remote out-of-state eligible"`
- `classification` ← parsed from title parenthetical when present (heuristic regex `\(([^)]+)\)\s*$`); `None` otherwise
- `raw_text` ← concatenation of `#job-details` prose + `categories` text

**Correction to §2 field-mapping table:** that table reads `title` ← `<h2>` based on the meta-block markup snippet. Decision is `<h1>` — the actual posting-page H1 carries the title; the meta `<h2>` is a layout-level repeat. Verify selector during scraper-build phase; fall back to whichever element carries the working title text.

### 7. Pagination

`GET` with `&page=N`. **v1 ships page 1 only.** Add a comment in `crawler/sources/csu.py` noting pagination is deferred until results exceed `page-items=50`.

### 8. §3 hedge tightened

The §3 phrasing "may appear in the body text (Categories string or Position Information block), but is not consistently structured" is replaced (above) with the empirically-grounded reading: **the class series does not appear in the structured DOM; inferable only from working title or via narrow-widened `target_titles_yes`.**
