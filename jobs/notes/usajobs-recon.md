# USAJobs Reconnaissance

**Date:** 2026-05-27
**Goal:** Map the USAJobs Search API before writing `crawler/sources/usajobs.py`. This is the v1 first-real-source per the post-CalCareers-recon pivot.
**Status:** Investigation only — no code written. JT does not yet have an API key; this doc tells him where to get one and what to expect.

---

## TL;DR

USAJobs has a real REST API. JSON in, JSON out, no DOM parsing. Auth is a free API key + your email as `User-Agent`. The Code List endpoints are fully public (proven — I pulled the 822-entry occupational-series catalog with no key). The Search endpoint requires the key and returns 401 without it (proven — invalid-key probe got `401 Unauthorized` with `application/problem+json`). Pagination is page-number based. Federal salaries are annualized — we'll divide by 12 before applying `SALARY_FLOOR` (which is monthly per `.env`).

DODEA caveat that breaks one of our spec assumptions: **DODEA classroom teachers do not post to USAJobs** — they use DODEA's own Employment Application System (EAS) at `dodea.edu`. Only DODEA support staff (Ed Aides, ROTC Instructors, Instructional Systems Specialists, Principals, etc.) flow through USAJobs. If JT wants the overseas-teaching channel, EAS is its own scrape (and the spec defers it to manual JT-managed elsewhere — verify).

Recommendation: build this one. Auth one-time, then pure-JSON scraping. Should be 3–5× less effort than CalCareers and likely produces a clean enough end-to-end demo to validate the pipeline.

---

## 1. Authentication

### How JT registers

- **Registration URL:** `https://developer.usajobs.gov/` → "Register" / "Request API Key" (the developer portal site was timing out during recon; JT will see the registration link on the homepage).
- **Cost:** Free.
- **Approval:** Self-service per public documentation tone — the API is intended for any developer building tools against public federal job data. Approval should be near-instant (key emailed after form submission). **JT to confirm during registration.**
- **What he'll need to provide:** Name, email, organization (he can put "Personal project — jobs.jtplatt.com"). JT to use `James.T.Platt@gmail.com` per `.env.example` already.

### Required headers (every request)

Confirmed via Google snippet of the official authentication guide:

```
Host: data.usajobs.gov
User-Agent: <your registered email address>
Authorization-Key: <your API key>
```

- `User-Agent` is literally your email — not "Mozilla/..." like a browser. USAJobs uses this as a contact mechanism if your traffic causes issues.
- `Authorization-Key` is the API key, not `Authorization: Bearer ...`. Pay attention — they're unusual.
- `Host` must be set even though `requests` / browsers normally infer it. Some third-party clients have failed for omitting it.

### Confirmed via probe (this recon)

- `GET https://data.usajobs.gov/api/search?Keyword=software&ResultsPerPage=1` **without** `Authorization-Key` → **`HTTP 401 Unauthorized`** with `application/problem+json` body.
- Same call **with** an invalid key (`Authorization-Key: ZZZZ_INVALID_KEY_FOR_PROBE`) → also **`401 Unauthorized`**. So the key is actually validated against a registry, not just checked for presence.
- Both responses set a `Set-Cookie: akavpau_DATA_USAJ=...` cookie (Akamai bot-protection cookie). The cookie is not required for subsequent requests — the API is stateless once authed.

### Rate limits — partial info

**Cannot cite exact numbers.** The official rate-limiting guide (`developer.usajobs.gov/guides/rate-limiting`) timed out across multiple fetch attempts during this recon. Web search surfaced one practical limit:

> **10,000-result cap per query** (cited by `publicapi.dev` and other third-party references — not verified against the official docs directly because the page was unreachable).

What we know from convention + observation:
- Akamai protection is active on `data.usajobs.gov` (`x-azure-ref` headers suggest Azure, plus the `akavpau_DATA_USAJ` cookie suggests Akamai — likely cross-CDN).
- Other USAJobs API consumers (third-party wrappers on GitHub) don't sleep between requests, which suggests the limits are not aggressive at the per-second level for normal-volume use.
- Our 2-second default delay (from `.cursorrules` §5) is well within any plausible bound.

**Action item for JT:** When the key arrives, the welcome email or the developer portal account page should show your rate-limit quota. Note it here when known.

### Terms of use

The Code List endpoints are documented as public-domain government data with no use restrictions. The Search API is intended for "building tools and integrations" — personal job-search tooling falls cleanly within scope. Attribution is not required for non-commercial use. JT confirms during registration.

---

## 2. Search Endpoint

### Base

- **URL:** `https://data.usajobs.gov/api/search`
- **Method:** `GET`
- **Auth:** Required (see §1)

### Query parameters

Confirmed via search (Google snippets of official docs) + third-party wrapper code on GitHub (`jobapis/jobs-usajobs`). Inferred where docs were unreachable — marked with †.

| Param | Type | Purpose | Multi-value | Notes |
|---|---|---|---|---|
| `Keyword` | string | Full-text keyword across title/description | ❌ single | Most useful for broad sweeps |
| `PositionTitle` | string | Title-field-only keyword | ❌ single | Stricter than `Keyword` |
| `LocationName` | string CSV | City/state filter (e.g., "Sacramento, California") | ✅ CSV | Multiple cities separated by `;` per docs convention † |
| `Radius` | int (miles) | Combined with `LocationName` for radius search | ❌ | Default unbounded |
| `JobCategoryCode` | string CSV | OPM occupational series codes (e.g., `2210`) | ✅ CSV (semicolon) | The primary filter we'll use — see §3 |
| `Organization` | string CSV | Agency code | ✅ CSV † | Use to narrow to DODEA, etc. |
| `RemunerationMinimumAmount` | number | Minimum salary filter (annual USD) | ❌ | We will use this with caution — see §4 |
| `RemunerationMaximumAmount` | number | Maximum salary filter | ❌ | Probably unused for us |
| `PayGradeHigh` / `PayGradeLow` | string | GS grades (e.g., `GS-09`, `GS-13`) | ❌ | Useful supplemental filter |
| `WhoMayApply` | string | `Public` / `Status` (federal employees only) — pass `Public` to exclude internal-only postings | ❌ | Should likely default to `Public` |
| `ResultsPerPage` | int | Page size | ❌ | Default 25 †, max 500 † |
| `Page` | int | 1-indexed page number | ❌ | Pagination |
| `SortField` | string | Sort key (e.g., `OpenDate`, `Relevance`) | ❌ | We'll sort by `OpenDate desc` to surface newest first |
| `SortDirection` | string | `Asc` / `Desc` | ❌ | |
| `RemunerationFrequency` | string | `Per Year` / `Per Hour` / etc. | ❌ † | Useful — filter out hourly to skip Wage-Grade roles |

† = inferred / inherited from third-party wrappers and conventional API design. Will be confirmed when the scraper does its first run.

### Pagination

Page-number model: `Page=1`, `Page=2`, etc. Combined with `ResultsPerPage` and a hard cap of **10,000 results per query** (third-party citation, not verified against official docs).

For our scope (target series + Sacramento metro + overseas), we're well under the cap. If a query approaches 10k, we narrow by series or location, not by paginating further.

### Response shape (top-level)

Inferred from training-data familiarity with the USAJobs API plus third-party wrapper code. JT will see the exact shape on first run; this is the model the scraper should expect:

```json
{
  "LanguageCode": "EN",
  "SearchParameters": { /* echo of the query */ },
  "SearchResult": {
    "SearchResultCount": 25,                  // count on this page
    "SearchResultCountAll": 1234,             // total matching across all pages
    "SearchResultItems": [
      { "MatchedObjectId": "...", "MatchedObjectDescriptor": { /* job */ } }
    ],
    "UserArea": { /* user-context metadata, usually ignored */ }
  }
}
```

Pagination loop pseudocode:

```
page = 1
loop:
  fetch ?Page=page&ResultsPerPage=500
  yield each SearchResultItems[i].MatchedObjectDescriptor
  if len(SearchResultItems) < 500: break
  page += 1
  sleep 2  # respect .cursorrules §5
```

---

## 3. Occupational Series Mapping

OPM organizes federal jobs by 4-digit series codes. USAJobs Search filters by these via `JobCategoryCode`. I pulled the full active series catalog from the public Code List endpoint (`/api/codelist/occupationalseries`, no auth needed). 822 active series; the ones relevant to JT's `target_titles_yes`:

### Confirmed (from the live catalog this recon pulled)

| Code | Series Name | Why it matches |
|---|---|---|
| **2210** | Information Technology Management | Maps to "Information Technology Specialist I/II" and "Applications Analyst." THE primary code for JT. |
| **0343** | Management And Program Analysis | The federal home for "Business Systems Analyst" — most BSA-style work lives here. |
| **0301** | Miscellaneous Administration And Program | Catch-all for "Staff Services Analyst" style roles — federal cousins of CA's SSA. Lower precision, higher recall. |
| **1701** | General Education And Training | Maps to "Training Officer," "Education Programs Consultant" — broad ed-and-training admin. |
| **1712** | Training Instruction | Maps to "Adult Education Instructor" — instructional roles. |
| **1750** | Instructional Systems | Maps directly to "Instructional Designer." Also covers the Instructional Systems Specialist (ISS) role DODEA hires for. |
| **1740** | Education Services | Less common; education-program-administration. Include for completeness. |
| **1710** | Education And Vocational Training | Adjacent to 1701/1712 — pick up vocational ed roles JT could plausibly bridge from CTE credential. |
| **1101** | General Business And Industry | Wide net; many BSA roles live here too when 0343 doesn't fit. |

### Series we should explicitly NOT include (would generate noise)

- **2210 sub-specialties at GS-14/15 only** — JT is GS-09–GS-12 equivalent. Filter by `PayGradeHigh=GS-12` to avoid senior-cybersecurity-with-CISSP noise (which is `target_titles_no` per `inventory.py`).
- **1102 Contracting** — JT does not have FAC-C credentials.
- **0501 Financial Administration / 0510 Accountant** — outside JT's preferred path even though his finance background fits; per `inventory.py.PREFERENCES.primary_criteria` the path is teaching/IT/training, not finance.

### Sources cited
- OPM GS-2210 series: https://www.opm.gov/policy-data-oversight/classification-qualifications/general-schedule-qualification-standards/0300/gs-2210-information-technology-management-series/
- Full catalog (pulled this recon): `https://data.usajobs.gov/api/codelist/occupationalseries` — 822 active series

### DODEA

**Important finding.** DODEA splits its hiring across two systems:

1. **EAS (Employment Application System)** at `dodea.edu/offices/human-resources` — for classroom **teachers, counselors, nurses, psychologists, occupational/physical therapists**. These positions are *not* posted on USAJobs. Candidates maintain an active EAS application; DODEA fills from that pool.

2. **USAJobs** — for DODEA **non-teacher support staff:** Educational Aides (1702), Office Automation (0326), Principals/Assistant Principals (0340 family), ROTC Instructors, **Instructional Systems Specialists (1750)**. These are crawlable.

**Implication for our scoping:** if JT wants overseas teaching as an escape hatch, USAJobs alone won't surface DODEA classroom teacher roles. The 1750 (Instructional Systems) angle is the closest USAJobs-discoverable DODEA path, and it lines up cleanly with the "Instructional Designer" entry in `target_titles_yes`. **DODEA teacher pipeline is a separate manual channel JT manages himself** — keep that scope discipline; don't try to integrate EAS into v1.

Filter for DODEA non-teacher roles via:
- `Organization=Department of Defense Education Activity` (string code TBD — confirm by inspecting an actual DODEA posting's `OrganizationName` field on first run)
- Or `Keyword=DODEA` as a coarse fallback

---

## 4. Field Mapping (USAJobs response → our `Posting` dataclass)

The Search response embeds the full posting payload in `MatchedObjectDescriptor`. No follow-up GET required for our purposes — but per `MatchedObjectId` there is a separate detail endpoint if we ever need the longer formatted description. Field mapping below was originally **inferred from training-data familiarity and third-party wrapper code**, then corrected against a real probe response.

### Probe corrections (2026-05-28)

A live authenticated probe (5 results, JobCategoryCode=2210, Sacramento+50mi) surfaced five divergences from the originally-inferred mapping. Each is now corrected below:

1. **`raw_text` source.** `PositionFormattedDescription` is NOT the description — every probed posting had a single entry with `Label: "Dynamic Teaser"` and `LabelDescription: "Hit highlighting for keyword searches."` That's a search-results teaser placeholder, not the job body. The real description content is on `UserArea.Details.*` as individual string fields.
2. **Salary types.** `PositionRemuneration[*].MinimumRange` and `MaximumRange` are **strings** ("90925"), not numbers. Convert to `float` before dividing by 12.
3. **Location ordering.** `PositionLocation` is alphabetized by `LocationName` — so `[0]` for a multi-location posting is typically `Birmingham, Alabama`, not whatever the user searched for. `[0]` is unreliable as a display location.
4. **`PublicationStartDate` format.** Returns `"2026-05-26T07:11:05.6100"` — four-digit fractional seconds, no `Z`/timezone suffix. Python 3.10 `datetime.fromisoformat` rejects this format (3.11+ tolerates it).
5. **`PositionURI` cosmetics.** Includes an explicit `:443` port (`https://www.usajobs.gov:443/job/...`). Strip before display/storage.

The conclusion that **no second request per posting is needed** still holds — the Search response carries the full description — but the content lives in `UserArea.Details`, not `PositionFormattedDescription`.

### Corrected mapping

| Our `Posting` field | JSON path | Notes |
|---|---|---|
| `source_job_id` | `MatchedObjectDescriptor.PositionID` | E.g., `"260004MP-12951788-CS"`. Stable per posting. |
| `title` | `MatchedObjectDescriptor.PositionTitle` | Free-form job title. |
| `employer` | `MatchedObjectDescriptor.DepartmentName` + ` / ` + `OrganizationName` | E.g., "Department of Justice / Federal Bureau of Investigation". |
| `url` | `MatchedObjectDescriptor.PositionURI`, with `:443` stripped | Probe returns `https://www.usajobs.gov:443/job/870502400`; strip the port for display. |
| `salary_min` | `float(PositionRemuneration[*].MinimumRange)` ÷ 12 | Values arrive as **strings**, not numbers. Per locked decision #2: use the **lowest** `MinimumRange` across all entries (most multi-grade postings will have one). Federal pay is annualized; convert annual → monthly before comparing to `SALARY_FLOOR`. Filter for `RateIntervalCode == "PA"` (Per Annum); reject `"PH"` (Per Hour) and `"WC"` (Without Compensation). |
| `salary_max` | `float(PositionRemuneration[*].MaximumRange)` ÷ 12 | Same string-to-float conversion. Use the **highest** `MaximumRange` across all entries. |
| `location` | See note | `PositionLocation[0]` is **not reliable** — the array is alphabetized by `LocationName`, so a 54-location nationwide posting starts at "Birmingham, Alabama" regardless of the search radius. Strategy: if `len(PositionLocation) == 1`, use that entry's `LocationName`. If more than one, use `PositionLocationDisplay` (e.g., `"Multiple Locations"`) and store the full list in `all_locations`. |
| `all_locations` | `[loc.LocationName for loc in PositionLocation]` | New `Posting` field. Powers the hard-filter check (ANY entry matching Sacramento metro OR overseas qualifies) and smart display. Overseas detection: `CountryCode != "United States"` on any entry. |
| `telework_flag` | `UserArea.Details.TeleworkEligible` (boolean) — confirm with keyword scan of `raw_text` for `"telework"`/`"remote"`/`"hybrid"` | Field IS present on every probed posting (boolean, sometimes `False` when posting actually mentions hybrid). Treat structured field as a HINT; confirm with description scan. Same approach as CalCareers. |
| `raw_text` | Labeled concat of `QualificationSummary` + `UserArea.Details.JobSummary` + joined `UserArea.Details.MajorDuties` + `UserArea.Details.Requirements` + `UserArea.Details.Education` + `UserArea.Details.OtherInformation` | **NOT** `PositionFormattedDescription` (placeholder — see probe correction #1). Excluded sections (boilerplate, not scoring signal): `Evaluations`, `HowToApply`, `WhatToExpectNext`, `RequiredDocuments`, `Benefits`. Prefix each included section with its label (e.g., `"=== Job Summary ===\n..."`) so `score.py` and `score_llm.py` can locate context if needed. |
| `posted_date` | `MatchedObjectDescriptor.PublicationStartDate`, parsed as `date.fromisoformat(value[:10])` | Format is `"2026-05-26T07:11:05.6100"` — 4-digit fractional seconds, no timezone. `datetime.fromisoformat` fails on Python 3.10 (works on 3.11+). 3.10-safe approach: slice the leading `YYYY-MM-DD` (first 10 chars) and feed to `date.fromisoformat`. |

### Federal salary specifics

- **Frequency:** annual (`PositionRemuneration[0].RateIntervalCode == "PA"` = Per Annum). Other interval codes: `PH` (Per Hour), `WC` (Without Compensation), `FB` (Fee Basis).
- **Conversion to monthly:** `annual / 12`. There is no two-decimal pay-period quirk — federal jobs are stated as annual.
- **Locality pay** is already included in the posting's stated range (federal jobs publish locality-adjusted ranges per region). Sacramento metro and overseas postings will have different ranges for the same grade — that's fine and reflects actual take-home.
- **Multiple ranges:** some postings (rare) list more than one `PositionRemuneration` entry — e.g., a single posting open across multiple grade levels. **Decision needed** (see open items): take the first, the highest max, or all?

### Location specifics

`PositionLocation` is an array. Each entry has:

```json
{
  "LocationName": "Sacramento, California",
  "CountryCode": "United States",
  "CountrySubDivisionCode": "California",
  "CityName": "Sacramento",
  "Longitude": -121.4944,
  "Latitude": 38.5816
}
```

For overseas (DODEA support staff):

```json
{
  "LocationName": "Wiesbaden, Germany",
  "CountryCode": "Germany",
  "CountrySubDivisionCode": null,
  "CityName": "Wiesbaden"
}
```

So `CountryCode != "United States"` is a clean overseas signal — useful for the "international bonus" in Tier 1 scoring (§ Soft Scoring in SPEC.md).

A posting can have **many** `PositionLocation` entries (the same role open at multiple duty stations). For our purposes: store the first as `location`, keep all in `raw_text`. The hard filter for Sacramento-metro or overseas evaluates ANY location matching.

### Raw text — Search response vs detail GET

The Search response includes the full `PositionFormattedDescription` (labeled blocks) and `QualificationSummary`. **We do not need a second request per posting.** This is materially simpler than CalCareers. One paginated Search loop produces every field we need.

The separate detail endpoint (`/api/search?... &PositionID=...` or a dedicated endpoint) exists for edge cases but is not in scope.

---

## 5. Sample Query

We can't execute it (no API key yet), but here's the call the scraper will make on JT's first real run — for ITS-family + BSA + Instructional Systems, Sacramento OR overseas, sorted by newest:

```
GET https://data.usajobs.gov/api/search
    ?JobCategoryCode=2210;0343;1701;1712;1750;1740;1710;0301
    &LocationName=Sacramento, California
    &Radius=50
    &WhoMayApply=Public
    &ResultsPerPage=500
    &Page=1
    &SortField=OpenDate
    &SortDirection=Desc
    &RemunerationFrequency=Per Year
Headers:
    Host: data.usajobs.gov
    User-Agent: James.T.Platt@gmail.com
    Authorization-Key: <JT's key>
```

For the overseas track (DODEA non-teacher, Foreign Service IT, State Dept overseas IT), a second call with no `LocationName` and `Organization=` filtered to overseas-posting agencies — or just `Keyword=overseas`. Two calls per run is well within any rate limit.

**Expected response sketch** (will be confirmed on first real run):

```json
{
  "LanguageCode": "EN",
  "SearchParameters": {...},
  "SearchResult": {
    "SearchResultCount": 47,
    "SearchResultCountAll": 47,
    "SearchResultItems": [
      {
        "MatchedObjectId": "839472691",
        "MatchedObjectDescriptor": {
          "PositionID": "TC-12345-26-DEF",
          "PositionTitle": "Information Technology Specialist (POLICY & PLNG)",
          "PositionURI": "https://www.usajobs.gov/job/839472691",
          "PositionLocation": [{"LocationName": "Sacramento, California", ...}],
          "OrganizationName": "Forest Service",
          "DepartmentName": "Department of Agriculture",
          "JobCategory": [{"Name": "Information Technology Management", "Code": "2210"}],
          "PositionRemuneration": [{
            "MinimumRange": "98000.00", "MaximumRange": "127400.00",
            "RateIntervalCode": "PA", "Description": "Per Year"
          }],
          "PositionStartDate": "2026-05-15",
          "ApplicationCloseDate": "2026-06-12T23:59:59Z",
          "PublicationStartDate": "2026-05-15T00:00:00Z",
          "QualificationSummary": "Specialized experience...",
          "PositionFormattedDescription": [
            {"Label": "Duties", "LabelDescription": "..."},
            {"Label": "Requirements", "LabelDescription": "..."}
          ],
          "UserArea": {"Details": {"TeleworkEligible": true, "WhoMayApply": {"Name": "United States Citizens"}}}
        }
      }
    ]
  }
}
```

---

## 6. Volume Estimate

**Across our target series (8 codes above), Sacramento metro + overseas + any DODEA, daily new postings:**

| Series | Estimated new/day, nationwide | Filtered to Sac metro + overseas |
|---|---|---|
| 2210 (IT Mgmt) | ~25–60 | ~3–8 |
| 0343 (Mgmt/Program Analysis) | ~40–80 | ~3–6 |
| 0301 (Misc Admin) | ~30–60 | ~3–5 (high noise; many hard-rejects) |
| 1701, 1712, 1750, 1740, 1710 (education family) | ~10–25 combined | ~1–3 |
| **Total per day** | ~100–200 across our series | **~10–22 after location filter** |

These are educated-guess estimates based on federal hiring volume from public sources. **First week of live runs will measure for real.** If volume is materially different we revise — but the order of magnitude is what matters for scoping decisions.

### DODEA specifically

DODEA support-staff hires (1750 ISS, 1702 Ed Aide, principals, ROTC) post on USAJobs in spurts — probably **5–20 postings per month** across all DODEA support roles globally. Not a flood. Worth keeping in scope; cost of filtering is near zero.

**Classroom teachers via DODEA: 0 per day on USAJobs.** They're all via EAS. JT manages EAS manually per the spec.

---

## 7. Risks

### Low-impact, manageable
- **Rate limit reached.** Unlikely at our 2-paginated-calls-per-day cadence. If it happens: respect 429 + back off per `.cursorrules` §5.
- **API key revoked.** If our usage looks abusive or violates Terms. Mitigation: stick to declared use case, don't spawn parallel keys, don't share.

### Medium-impact, monitor
- **Field shape changes.** The API is versioned only implicitly. USAJobs may add/rename fields. Our scraper should `.get()` with defaults, log unknown fields, fail soft on a single bad posting rather than crash the run.
- **Telework flag unreliable.** As noted: the structured `TeleworkEligible` boolean lies often. We do keyword scan of description as the source of truth. Same approach we'll need for CalCareers, so the logic in `filter.py` is reusable.
- **Hourly-rate (Wage-Grade) postings.** A small minority of USAJobs postings have `RateIntervalCode: "PH"`. These would surface as ITS-family roles in non-DC blue-collar IT support pipelines and shouldn't be in JT's flow. Filter: require `RateIntervalCode == "PA"` OR `RemunerationFrequency=Per Year` at query time.

### Higher-impact, less likely
- **Postings without a parseable `PositionRemuneration`.** Federal Wage-Grade or "Without Compensation" (`WC`) volunteer roles. Skip these in filter.py with explicit logging — they'd fail the salary floor anyway.
- **Akamai bot challenges.** The cookie observed (`akavpau_DATA_USAJ`) hints at Akamai. At our low volume + valid `User-Agent: <email>`, challenges are very unlikely. Mitigation: if we ever get a Captcha challenge in the response, fail loudly, don't retry blindly.

### Out of scope
- We are **not** integrating DODEA EAS in v1. JT manages teacher applications through EAS himself (per spec). USAJobs covers DODEA support roles only.

---

## Open Items for JT

1. **Register for the API key.** Visit `https://developer.usajobs.gov/`, click Register / Request Key. Use `James.T.Platt@gmail.com` as the registered email. Save the key into the server `.env` only as `USAJOBS_API_KEY=...` (the slot already exists in `.env.example`). Note the actual rate limits the welcome email or portal shows.
2. **Multiple `PositionRemuneration` entries — which range do we store?** Recommend: first entry's MinimumRange / MaximumRange, with all ranges concatenated into `raw_text`. Same conservative posture as CalCareers Range A.
3. **`WhoMayApply` default.** Recommend `WhoMayApply=Public` so we exclude internal-federal-only postings JT can't apply to. Confirm.
4. **Organization filter for DODEA.** The string code for "Department of Defense Education Activity" is TBD until first response is inspected. May be `DD` + sub-code or a longer slug. Verify on first run; record in `data/sources.yaml` or a constants module.
5. **Sort order.** Recommend `SortField=OpenDate, SortDirection=Desc` so newest-first; combined with our deduplication-by-PositionID, we always pick up genuinely new postings in the first page of results.

## Decisions (locked 2026-05-28)

1. **API key.** JT registers at developer.usajobs.gov. Key goes in BOTH local .env (for local testing) and server .env (for production) — independent per SOUL.md; deploy never syncs .env. Slot USAJOBS_API_KEY already in .env.example.

2. **Multiple PositionRemuneration entries.** salary_min = lowest MinimumRange across all entries; salary_max = highest MaximumRange across all entries. Preserve all ranges in raw_text. Hard salary filter compares salary_min >= SALARY_FLOOR (strict; conservative on rare multi-grade postings, consistent with JT's "rather miss than mediocre" stance).

3. **WhoMayApply.** Default Public — excludes internal-federal-only (Status) postings JT can't apply to as a non-federal employee.

4. **DODEA organization code.** Resolve by looking up "Department of Defense Education Activity" in the public agencysubelements code list (/api/codelist/agencysubelements, no auth), NOT by inferring from a posting. Record the resolved code in a constants module when the scraper is built.

5. **Sort order.** SortField=OpenDate, SortDirection=Desc. Newest-first, with dedup by PositionID.
