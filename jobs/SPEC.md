# SPEC.md — Job Agent

The contract. Do not deviate without asking JT.

## What This Is

A standalone Python service that crawls a fixed set of job sources daily, sorts each posting into one of five priority lanes, filters against JT's hard criteria, scores against his lane preferences and career inventory (`data/inventory.py`), renders a results page at `jobs.jtplatt.com`, and emails him only when something scores as a near-perfect fit.

Lives at `jtplatt.com/jobs/` in the same repo as the portfolio site, but runs as its own Opalstack app at the `jobs.jtplatt.com` subdomain. Independent of QuickStrike, Morning Call, and Sneaker Savant — different stack (Python, not Node), different deploy, never imports from those projects.

---

## Lanes (priority order)

Every posting that clears the hard filters is assigned to its best-fit lane (`lanes.assign_lane`). The first lane whose rule fires wins. Output ranks by lane, then Tier 1 score. Lane keywords live in `inventory.LANE_PREFERENCES`, kept separate from `SKILLS` (lane keywords say where JT wants to go, never what he has done).

1. **Sneakers / collectibles.** Sneaker/collectibles domain hit (title, employer, or text) AND a function keyword in the title (brand, product, merchandising, strategy, partnerships, consulting, category, sourcing, supply chain, production, manufacturing, operations, factory, plant, quality, floor supervisor, production manager). Hard pay floor $100k/yr.
2. **Overseas.** Any non-US location. Southeast Asia and Europe are priority regions (scored higher). Overseas is also reported on every lane as `overseas_region`, so a sneaker job in Germany stays lane 1 and is flagged overseas.
3. **Remote knowledge work.** Fully remote (not hybrid; worldwide or US-eligible, since lane 2 already took overseas) AND a fit with JT's background. Checked in two steps:
   - **Core title** (before lanes 4-5, unchanged): IT / analyst / consulting title (`title_keywords`). Only these feed lane-fit scoring.
   - **Wider fit gate** (after lanes 4-5, so education / Sacramento title evidence wins): a `fit_title_keywords` hit in the title (tech/IT: engineer, developer, systems, technical, data, analytics, QA, product, architect, devops; finance: finance, accounting, accountant, FP&A, revenue, controller, audit; ops/PM: operations, project/program manager, process; writing/research: writer, editor, content, copywriter, research, documentation; business: partnerships, business development, account manager/executive, customer success, marketing, sales), else any core or fit keyword in the description ("IT" excluded there: it would match "it"). Titles in `excluded_title_keywords` (nurse, clinical, physician, therapist, attorney, counsel, paralegal, trades, technician, driver...) never enter through this gate. Fully remote roles with no fit stay unlaned.

**Remote job boards** (Remotive, Remote OK, We Work Remotely, Himalayas; `lanes.REMOTE_BOARD_SOURCES`): every posting is fully remote (by source, since `remote_type` isn't stored), and the location says where the *candidate* may live, not where an office is. `lanes.remote_board_scope` replaces the lane-2 rule for them: worldwide / no restriction ("anywhere") or US-eligible ("USA", "United States", "North America", "Americas", even alongside other countries) -> on to lane 3 (still needs a knowledge-work title); locked to recognized non-US countries or regions (a country name, "City, Country", or Europe / LATAM / APAC etc.) -> lane 2; free text that can't be placed (Remote OK's bare "Redwood City", "Seoul") is not treated as overseas and goes on to lane 3. Lane 1 still wins first. Within lane 2, a remote role locked to Japan, Vietnam, or Thailand gets a +10 score bonus so it ranks above other country-locked roles.
4. **Education.** Counseling, instructional design, edtech, training, curriculum titles.
5. **Sacramento state IT (fallback).** Step-up title only (ITS II, ITS III, ITM I/II, IT Supervisor II) AND a Sacramento-ring location (Sacramento, Yolo, Placer, El Dorado). ITS I is lateral and never qualifies.

Postings no lane rule fires on are "unlaned" and rank last.

**Authentication / authenticator roles** (sneaker domain only) qualify only if (fully remote OR supervisory/lead/head/manager title) AND verified pay >= $100k/yr. Otherwise rejected. IT identity roles ("Authentication Engineer" at a bank) are out of scope for this rule.

---

## Hard Filters (must pass ALL)

A posting that fails any one of these is dropped. Not shown, not scored, not emailed. Applied in this order (`filter.py`):

1. **Pay floor.** Applies only to verified pay.
   - Normal floor: monthly USD >= `SALARY_FLOOR` (JT's current state salary). Applies to every lane, including education (no pay cut).
   - Sneaker lane: also >= $100k/yr.
   - Pay is normalized to USD-monthly when a `Posting` is constructed (`pay.py`, rates in `data/currency.yaml`: USD, EUR, GBP, VND, IDR, THB, CNY, JPY, KRW). Sources set `currency` and `pay_period`; defaults are USD / monthly.
   - Unknown currency or pay period: salary cleared, posting let through, marked **pay unverified**.
   - No salary: let through, marked **pay unverified**.
   - **Sneaker-lane salary estimate** (`estimate.py`, bands in `data/salary_estimates.yaml`): a lane-1 posting with no verified pay gets an estimated annual USD base range from its title level (Associate, Professional, Senior Professional, Lead / Specialist II, Manager, Senior Manager, Director, Senior Director / VP; unreadable level = conservative fallback). The sneaker floor is applied to `estimate_min`: below $100k/yr = dropped. Verified pay always wins over the estimate.
     - Overseas with no local-hire marker in the title: full US band (assumes expat / US pay).
     - Overseas with a local-hire marker in the title (`local` token, e.g. "Local Creation", "GC Local"; or "ITC" = Nike India tech center): band x `local_hire_multiplier` (0.4). No per-country discount.
     - Never applied to remote-board sources (Remotive, Remote OK, WWR, Himalayas): real posted pay only.
     - Estimates are never verified pay: digest display and this floor only. Not used in scoring, never in application or resume output.
   - Outside the sneaker lane, $100k/yr is a strong scoring bonus, not a cutoff.
2. **Current employer.** FI$CAL / FISCal rejected.
3. **Source allow-list.** `filter.ALLOWED_SOURCES`.
4. **Disqualifying requirements.** Reject if the posting requires any credential or qualification in `data/reject_requirements.yaml` (PE, CPA, JD, MD, RN, LCSW, high clearances, CCIE, CISSP, top-tier cloud certs, degree requirements JT lacks).
5. **Authentication rule.** See Lanes.
6. **Location.** Pass if ANY posting location is acceptable:
   - Home region: `inventory.PREFERENCES["target_locations"]` (Sacramento, Yolo, Placer, El Dorado, Santa Cruz, Monterey, Hawaii, Truckee/Tahoe, Oceanside, San Marcos). On-site, hybrid, or remote.
   - Fully remote (`lanes.remote_type == "full"`). Hybrid is NOT remote: a hybrid office must be in the home region.
   - International, only with verified pay (it already cleared the floor after USD conversion; this is what keeps "100k dong" out).
   - San Francisco / New York City, only with verified pay >= `SALARY_FLOOR_PREMIUM_METRO` (monthly USD). Unset env = SF/NYC rejected.
   - Anything else rejected.
   - **Lane 1 (sneakers) exception:** Beaverton (Nike HQ), Boston (Converse HQ), and Los Angeles (NikeSKIMS / media) are also accepted, and international / SF / NYC postings pass with unverified pay (marked "pay unverified"). A verified lane-1 salary below the premium floor still fails in SF/NYC. These three cities are NOT accepted for any other lane, and every other lane still needs verified pay for international / SF / NYC. Config: `LANE_PREFERENCES` lane 1 `extra_locations`, `unverified_pay_ok_everywhere`.

**Remote vs hybrid** (`lanes.remote_type`): explicit `Posting.remote_type` wins; a location string that is itself remote ("Anywhere in the U.S. (remote job)") = full; the word "hybrid" = hybrid; a full-remote phrase ("fully remote", "100% remote", "work from anywhere") = full; `telework_flag` or a bare remote/telework mention = hybrid.

If a posting requires something the inventory can't back, it gets rejected. We don't fudge.

---

## Soft Scoring

Two tiers. Tier 1 runs on every posting that clears hard filters. Tier 2 runs only on the daily top 10 by Tier 1.

### Tier 1 — Rule-based (0–100)

Computed in `score.py`. Ranks within a lane. Components:

| Component | Weight | Source |
|---|---|---|
| Lane fit | 30 | 20 for having a lane + 2 per lane keyword hit (cap 10). `inventory.LANE_PREFERENCES` |
| Background match | 20 | `inventory.SKILLS` + role `skills_used`, 2 per hit (cap 20). Honest record only |
| Title pattern match | ±20 | `inventory.PREFERENCES` target_titles_yes / target_titles_no (NO wins) |
| Pay | 15 | 15 verified >= $100k/yr; 5 verified below that; 0 unverified |
| Location | 10 | 10 fully remote or SE Asia/Europe; 7 other overseas; 5 hybrid |
| Recency | 5 | Posting <= 7 days old |
| Preferred country lock | +10 | Lane 2 only: remote-board role locked to Japan / Vietnam / Thailand (`lanes.preferred_country_lock`) |
| AI resilience | +4 / -3 | Re-rank only, never a filter (`score.ai_resilience`). Decided from the title: +4 resilient (authentication, verification, inspection, appraisal, grading, fraud / trust & safety, compliance, audit; partnerships, business development, account management, sales, negotiation; operations, supervision, plant/floor, people management (manager, director, head of), training; QA titles only when the body describes physical goods). -3 exposed (software engineer/developer/programmer, front/back end, full stack, data entry, data/reporting analyst, tier-1 / help desk / customer support, content writer / copywriter, bookkeeping), cancelled by a senior / lead / principal / staff / architect / manager / director / head / chief / VP / supervisor signal in the title. Else 0. Body text is not scanned for the keywords (boilerplate like "EEO compliance" would hit every posting). |

Score dict: `{"score", "lane", "components"}`. `lane` carries lane id/name, why it fired, `overseas_region`, `remote_type`.

Title patterns: YES/NO lists live in `inventory.PREFERENCES`. ITS I and bare "IT Specialist" are NOT on the YES list (lateral moves).

**Thresholds need retuning.** The email (T1 >= 80) and page bucket (80 / 40) thresholds were set against the old weights and have not been changed. Retune after the lane scoring has run on real data.

### Tier 2 — LLM scoring (0–10)

`score_llm.py`. For the daily top 10 by Tier 1 score only.

Sends posting text + a structured summary of JT's inventory + the project's no-fabrication rules to the Anthropic API. Asks for:

- A 0–10 fit score
- One-sentence rationale
- Up to three explicit gaps where JT's inventory doesn't match the posting

Costs are negligible — pennies/day. Use `claude-sonnet-4-20250514` or current equivalent.

---

## Email Digest (weekly)

Replaces the old per-posting "perfect fit" alert (Tier 1 >= 80 AND Tier 2 >= 8) and the need to browse the results page.

- **When:** Sundays 07:30 local via its own launchd agent, `com.jtplatt.jobs.digest` (source: `jobs/launchd/com.jtplatt.jobs.digest.plist`). Independent of the daily 06:00 crawl agent; reads whatever is in `data/jobs.db`.
- The daily crawl agent (`com.jtplatt.jobs`, tracked copy at `jobs/launchd/com.jtplatt.jobs.plist`) runs `main.py` only. The `publish.sh` step (Mac -> Opalstack) was removed 2026-10-28; the digest replaces the website.
- **What:** up to 12 jobs (`notify.DIGEST_SIZE`), from the same path as the results page: active jobs (last_seen within 14 days) -> hard filters -> Tier 1. **No score threshold.**
- **Per-lane cap:** at most 6 lane-1 jobs (`notify.LANE1_CAP`), highest score first; then up to 4 slots reserved for lane 3 (`notify.LANE3_RESERVE`), by score within lane 3; remaining slots from lanes 2-5 by score (lane 3 can take more on score); leftover slots from unlaned jobs. Fewer than 4 lane-3 jobs -> the unused reserved slots go back to the general pool. The lane-1 cap is hard: if other lanes run dry the digest is shorter. Displayed by lane (1 first, unlaned last), score desc, one heading per lane.
- **Per job:** title (linked to the apply URL), employer, location, pay (annual USD range, "pay unverified", or a lane-1 estimate: "est. $X-$Y *actual salary range not posted" (US), "est. $X-$Y *actual salary not posted; assumes expat/US pay" (overseas), "est. $X-$Y *local-hire role" (overseas local-hire)), score, and "via <source>" (attribution required by the Remotive / Remote OK / WWR / Himalayas terms; shown for every source). Lane is the group heading.
- **Format:** multipart plain text + simple single-column HTML with inline styles. No images, no remote resources, no tracking.
- **Transport:** Gmail SMTP, STARTTLS on 587. Env: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` (Google App Password), `EMAIL_TO`. Missing env = exit 1, nothing sent.
- **On demand:** `python -m notify --send-digest` (send now) or `python -m notify --preview out.html` (write HTML, send nothing), run from `jobs/`.
- No per-job dedup across weeks: a job still active next Sunday can appear again.

---

## Results Page (`jobs.jtplatt.com`)

Flask app behind HTTP basic auth. Password in `.env`. No nginx config required — auth is enforced in the Flask app.

### Sections (top to bottom)

1. **Perfect fits** — emailed today (Tier 1 >= 80, Tier 2 >= 8)
2. **Strong matches** — Tier 1 60–79
3. **Decent matches** — Tier 1 40–59 (collapsible)
4. **Below threshold** — Tier 1 < 40 (hidden by default)

### Per-row

Title | Employer | Salary band | Location | Lane | Fully remote / hybrid flag | Overseas flag | Pay unverified flag | Source | Posted date | Tier 1 score | Tier 2 score (if any) | Apply link | "Why" expand panel

Within each section, rows sort by lane (1 first, unlaned last), then Tier 1 score.

Page rebuilds at the end of each cron run.

---

## Sources (v1)

| Source | Method | Notes |
|---|---|---|
| CalCareers | Scrape search URL pagination | CA state departments + Covered California |
| governmentjobs.com | Scrape with per-tenant config | Sacramento County, City of Sacramento, City of West Sac, Folsom, others — list in `data/sources.yaml` |
| EdJoin (edjoin.org) | Scrape | CA K-12 + community colleges |
| CSU Careers | Scrape — system TBD during implementation | All 23 CSU campuses statewide — CalPERS-covered staff. Covers Sac State, Monterey Bay, San Marcos, etc. |
| USAJobs | Official API (developer.usajobs.gov) | Federal incl. DODEA, State Dept |
| Nike | Workday CxS JSON API (`nike.wd1.myworkdayjobs.com/wday/cxs/nike/nke/jobs`) | Site `nke` only (`nke2` = US retail, skipped). "Retail Stores" job family excluded via `appliedFacets` (facets are include-only: every other family id is applied). Pages of 20, total from page 1. Detail GET only for job ids not already stored; known jobs re-yield stored detail fields. No structured salary: parsed from description text when a "$X to $Y" salary range appears, else pay unverified. Known risk: Mexican postings also write pesos as "$", so a peso range in that format would be read as USD; revisit when a Mexico-heavy source is added. robots.txt disallows the human `/nke/` pages, not the API path; 1 run/day at 2s spacing. |
| Greenhouse | Public job-board JSON API, one GET per board: `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` (no paging; every job returned with its description; `meta.total` checked) | Boards: `stockx` (StockX), `fanaticsinc` (Fanatics), `fanaticscollectibles` (Fanatics Collectibles), `goatgroup` (GOAT Group); `fanaticsfbg` (betting) skipped. Employer = brand per board, so every posting carries a sneaker-lane domain keyword; lane 1 still needs a title keyword. `content` is entity-escaped HTML: unescaped, then tags stripped. Real posted pay parsed from the description where present: first range after a salary/pay keyword ("Salary Range $X — $Y USD", "$X to $Y", tiered ranges take Tier 1), hourly figures ("$20 — $20", "$17/hr") normalized as hourly so the floors drop them; hourly below $7.25 (e.g. Fanatics "$1 — $1" placeholder) and non-USD codes stay pay unverified. No posted pay: lane-1 estimate layer applies. 2s between boards; 429/5xx skips the rest of the source; one failed board does not block the others. |
| Remotive | Public JSON API, one GET: `remotive.com/api/remote-jobs` (whole free feed, `.jobs[]`; Remotive delays jobs 24h) | Terms: link back to the Remotive URL and name Remotive (digest shows "via Remotive"); max 4 calls/day (we make 1). `candidate_required_location` split on commas = candidate locations. Salary is free text: "$Xk - $Yk" / "$170,000 - $200,000" ranges (annual) and "$X/hour" (hourly) parsed; "OTE", "per task", single annual figures, and strings without "$" ("70K-80K") stay pay unverified. `remote_type="full"`. No estimate. |
| Remote OK | Public JSON API, one GET: `remoteok.com/api` (array; element 0 is terms metadata, skipped) | Browser User-Agent required (else 403). Terms: link back and name Remote OK. `position` / `company` / free-text `location` (often empty = no restriction). `salary_min`/`salary_max`: 0 = not posted, else USD annual. `remote_type="full"`. No estimate. |
| We Work Remotely | Category RSS, one GET per feed: `weworkremotely.com/categories/{feed}.rss` | Feeds: `remote-programming-jobs`, `remote-product-jobs`, `remote-management-and-finance-jobs`, `remote-sales-and-marketing-jobs` (`remote-business-jobs` does not exist). 2s between feeds; a job in two feeds is kept once (by guid slug). Title "Company: Role" split on the first ": ". `region` ("Anywhere in the World", "North America Only") = candidate location. No salary field: description run through the Greenhouse text pay parser, else pay unverified. `remote_type="full"`. No estimate. |
| Himalayas | Public JSON API, cursor-paged: `himalayas.app/jobs/api?limit=20&cursor=...` (server caps limit at 20; feed is 100k+ jobs, newest first) | Newest 500 per run (25 pages, 2s apart), following `nextCursor` (offset is deprecated). `locationRestrictions` = candidate countries: empty = anywhere, includes US = US-eligible, non-US only = overseas (no explicit "hires internationally" field; derived). Salary structured (`minSalary`/`maxSalary`/`currency`/`salaryPeriod`) and fed straight to pay.py: currencies not in `currency.yaml` (e.g. CAD) or missing stay pay unverified. `remote_type="full"`. No estimate. |

### Deferred / research items (not v1)

- **International School Services (iss.edu)** — member-only platform. Scraping likely violates TOS. JT manages manually via his ISS account.
- **US State Dept overseas schools (state.gov/overseas-schools)** — directory of schools, not a centralized job board.
- **Foreign Service Specialist (Diplomatic Technology Officer)** — exam-pipeline via Pearson, not crawlable. JT already has an account; manages manually.
- **FBI** — separate portal. JT manages manually.
- **LinkedIn / Indeed** — actively block scraping. Paid APIs not worth it for v1.
- **Peace Corps** — pays stipends, would always fail the salary floor hard filter.
- **Making Waves** — mostly nonprofit ops without CalPERS/CalSTRS coverage.

---

## Inventory Is Source of Truth

`data/inventory.py` is JT's career inventory translated to structured Python. The scorer reads from it. Never invent, extend, or "fill in" experience. If a posting requires something not in the inventory, the scorer marks it as a gap. The LLM tier is instructed not to invent either.

When JT updates his career, he updates `inventory.py`. The agent re-scores at next run.

---

## State

SQLite at `data/jobs.db`.

Tables:

- `jobs` — id, source, source_job_id, title, employer, url, salary_min, salary_max (USD-monthly, already normalized; NULL = pay unverified), location, telework_flag, raw_text, estimate_min, estimate_max (annual USD, NULL unless lane-1 estimate), estimate_source ("level_table" or NULL), first_seen, last_seen, status (active/closed). `currency`, `pay_period`, `remote_type`, `pay_note` are Posting fields but NOT stored yet.
- `scores` — job_id, tier1_score, tier1_components_json, tier2_score, tier2_rationale, tier2_gaps_json, scored_at
- `emails` — job_id, sent_at, subject
- `runs` — id, started_at, finished_at, source, postings_found, errors_json

---

## Tech Stack

- Python 3.10+ (verify Opalstack version)
- Flask
- requests + BeautifulSoup4
- SQLite (`sqlite3` stdlib)
- Jinja2 (bundled with Flask)
- python-dotenv
- anthropic (Tier 2)
- PyYAML
- gunicorn (WSGI on Opalstack)

Pin versions in `requirements.txt`.

---

## Cron

Daily at 06:00 America/Los_Angeles. Full Python path required (Opalstack pattern). Path below is illustrative — JT confirms the actual install path during deploy:

```
0 6 * * * /home/jtplatt/apps/jobagent/venv/bin/python /home/jtplatt/apps/jobagent/main.py >> /home/jtplatt/apps/jobagent/logs/cron.log 2>&1
```

Note: the Opalstack app directory (`/home/jtplatt/apps/jobagent/`) is what gets deployed — it receives a synced copy of this repo's `jobs/` subdirectory. The repo dir (`/home/jtplatt/dev/jtplatt.com/jobs/`) is for development only.

---

## Project Structure (within `jtplatt.com/jobs/`)

```
jobs/
├── crawler/
│   ├── __init__.py
│   ├── base.py                  # Abstract Source class
│   └── sources/
│       ├── __init__.py
│       ├── calcareers.py
│       ├── governmentjobs.py
│       ├── edjoin.py
│       ├── usajobs.py
│       ├── nike.py
│       ├── peacecorps.py
│       └── makingwaves.py
├── data/
│   ├── inventory.py             # JT's career inventory (source of truth)
│   ├── employers.yaml           # Allow-list and excludes
│   ├── sources.yaml             # governmentjobs.com tenants etc.
│   ├── title_patterns.yaml
│   ├── currency.yaml            # USD-per-unit conversion table (pay.py)
│   ├── reject_requirements.yaml
│   └── jobs.db                  # SQLite (gitignored)
├── pay.py                       # Salary -> USD-monthly normalization
├── lanes.py                     # Lane assignment + location/remote classification
├── filter.py                    # Hard filters
├── score.py                     # Tier 1
├── score_llm.py                 # Tier 2
├── store.py                     # SQLite I/O
├── notify.py                    # Weekly digest email
├── launchd/
│   ├── com.jtplatt.jobs.plist         # Daily crawl schedule (installed copy in ~/Library/LaunchAgents)
│   └── com.jtplatt.jobs.digest.plist  # Weekly digest schedule (install copy in ~/Library/LaunchAgents)
├── render.py                    # Build HTML
├── templates/
│   └── results.html
├── static/
│   └── styles.css
├── main.py                      # Cron entry point
├── server.py                    # Flask app (auth + serve)
├── tests/
├── logs/                        # Gitignored
├── requirements.txt
├── .env.example
├── deploy.sh
├── SPEC.md                      # This file
├── CLAUDE.md
├── PROJECT_STATUS.md
└── README.md
```

Repo root (`jtplatt.com/`) contains the portfolio site files (`index.html`, `styles.css`, `img/`, `downloads/`) and `SOUL.md`. The portfolio and the agent are siblings under one repo.

---

## Deployment

Per SOUL.md hard rules:

- `./deploy.sh` (inside `jobs/`) is the only path to production
- Never SSH to patch
- Deploy syncs the `jobs/` subdirectory to the `jobs.jtplatt.com` Opalstack app — NOT the entire repo
- The portfolio at `jtplatt.com` has its own deploy path; the agent never touches it
- Local `.env` and server `.env` are independent; deploy never touches server `.env`
- `python main.py --dry-run` runs the full pipeline against fixtures or live sites without writing to DB or sending email — verify locally before push

---

## Out of Scope for v1

- LinkedIn / Indeed scraping
- Auto-applying to anything (JT submits manually, always)
- Per-posting resume tailoring (that's Claude.ai work, not the agent)
- Slack / SMS notifications
- OAuth or social login
- Multi-user (this is JT's tool)

---

## Build Order

1. [completed] Repo init / skeleton
2. [completed] inventory.py
3. [completed] crawler/base.py
4. USAJobs source (API) — first real source
5. filter.py
6. score.py + title_patterns.yaml
7. store.py
8. render.py + minimal template
9. End-to-end local run with USAJobs as sole source
10. CalCareers — deferred until pipeline proven
11. governmentjobs.com (similar ASP.NET pattern; benefits from CalCareers lessons)
12. EdJoin
13. CSU Careers
14. score_llm.py
15. notify.py
16. server.py
17. deploy.sh
18. Tune thresholds

Do one source end-to-end before adding the next. Resist the urge to write all six scrapers up front.

---

## Open Items JT Needs to Resolve

1. Exact monthly salary floor (current ITS I rate) → goes in `.env`
1a. SF/NYC premium floor → `SALARY_FLOOR_PREMIUM_METRO` in `.env` (monthly USD)
2. City of Sacramento pension system — CalPERS or legacy SCERS for new hires? → drives `employers.yaml`
3. Opalstack Python version available and gunicorn setup pattern → confirm during deploy
4. Anthropic API key → goes in `.env` server-side only
5. Mail-sending service — SMTP via Opalstack, Mailgun free tier, SendGrid free tier? → pick one before Tier 2 build
6. Tier 1 / Tier 2 thresholds — start at 80 / 8 per this spec, tune after data accumulates. Needs retuning after the 2026-10-28 lane rewrite changed Tier 1 weights.
