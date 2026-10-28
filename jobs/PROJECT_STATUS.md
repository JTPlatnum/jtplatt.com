# PROJECT_STATUS.md

**Last updated:** 2026-10-28

## Current State

**Multi-lane job agent, crawl on the Mac, delivered by weekly email.** No longer state-IT only:
every posting is sorted into one of five priority lanes (1 sneakers/collectibles,
2 overseas, 3 remote knowledge work, 4 education, 5 Sacramento state IT fallback), then
hard-filtered and Tier 1 scored. See SPEC.md "Lanes".

- **Daily crawl (crawl only):** Mac launchd job `com.jtplatt.jobs` (06:00 PT) runs
  `main.py`: fetch → upsert → filter → score → render. `publish.sh` was **removed** from
  the daily run on 2026-10-28 (jobs.jtplatt.com returned 502 every day). Tracked copy of
  the plist: `launchd/com.jtplatt.jobs.plist`.
- **Sources live:** USAJobs, CalCareers, CSU, and **Nike** (Workday JSON API, added
  2026-10-28). EdJoin deactivated (see Known Regressions).
- **Delivery: weekly digest email** (`notify.py`), Sundays 07:30 via its own launchd job
  `com.jtplatt.jobs.digest`. Gmail SMTP, up to 12 jobs, at most 6 sneaker-lane, no score
  threshold. Replaces both the website and the old T1 >= 80 "perfect fit" email.
- **Website retired:** the Opalstack app at jobs.jtplatt.com is dead (502) and out of the
  pipeline. `server.py`, `deploy.sh`, `publish.sh` remain in the repo, unused.
- Test suite: 324 passing.

## What's Done

- [x] Repo skeleton, venv at `./venv/`, pinned `requirements.txt`
- [x] `data/inventory.py` — career inventory (`SKILLS` = honest record) + separate
  `LANE_PREFERENCES` (lane keywords, never a claim of experience)
- [x] `crawler/base.py` — `Source` + `Posting` (now with currency, pay_period, remote_type)
- [x] **Sources:** USAJobs (API), CalCareers + CSU (Playwright), **Nike (Workday CxS API)**
- [x] `pay.py` + `data/currency.yaml` — salary normalized to USD-monthly at ingest
- [x] `lanes.py` — lane assignment, location/region and remote-vs-hybrid classification
- [x] `filter.py` — pay floors (normal, $100k sneaker lane, SF/NYC premium), auth-role
  rule, location rule; lane 1 also accepts Beaverton/Boston/LA and unverified-pay overseas
- [x] `score.py` — lane-based Tier 1 (lane 30, skills 20, title ±20, pay 15, location 10,
  recency 5)
- [x] `store.py`, `render.py`, `main.py` (`--dry-run`)
- [x] `notify.py` — weekly digest, `python -m notify --send-digest` / `--preview`;
  test email confirmed 2026-10-28
- [x] Retired: `server.py`, `deploy.sh`, `publish.sh` (website path; kept in repo, unused)

## What's Not Built Yet

- [ ] More sneaker/collectibles sources (see What's Next)
- [ ] `score_llm.py` — Tier 2 LLM scoring (deferred; the digest doesn't need it)
- [ ] `crawler/sources/governmentjobs.py` — optional, low priority now
- [ ] EdJoin rebuild (Known Regressions)

## What's Next

1. **More sources:** StockX and GOAT (Greenhouse job boards), Adidas and New Balance
   (Workday, same pattern as `nike.py`).
2. **Threshold tuning, once those sources are in:** Tier 1 weights, the page buckets
   (80 / 40), and lane-1 breadth (any Nike title with "product"/"operations" lands in
   lane 1; "merchant" is missing from lane-1 keywords). Most Nike survivors score < 40.

## Spec Deviations (intentional, noted)

- SPEC.md lists requests + BeautifulSoup4 for scraping. CalCareers and CSU were built
  on **Playwright** instead (JS-heavy sites). Real choice, works — flagged here so the
  spec and reality don't silently diverge.
- SPEC implied the crawl runs server-side (Opalstack cron). Reality: the crawl runs on
  the **Mac** (CentOS 7 / glibc 2.17 can't run Chromium). It no longer publishes anywhere;
  results go out by weekly email.
- `deploy.sh` sources **devtoolset-11** before pip: greenlet (a Playwright dep) compiles
  a C++ extension, and CentOS 7's system g++ 4.8 predates C++11. devtoolset statically
  links the newer libstdc++, so the built `.so` runs under the base system afterward.
- `deploy.sh` is **two-phase**: a default run applies the config edits and prints them
  for review, then STOPS; `DEPLOY_CONFIRM_RESTART=1 ./deploy.sh` actually cycles uWSGI
  and runs the health check. A failed health check does NOT auto-roll back.

## Open Items for JT

- **Reload the daily launchd job** so the publish removal takes effect (launchd caches the
  old command until reloaded), and **install the digest job** (`launchd/com.jtplatt.jobs.digest.plist`
  → `~/Library/LaunchAgents/`, then `launchctl bootstrap`).
- **SF/NYC premium floor** — set `SALARY_FLOOR_PREMIUM_METRO=12500` ($150k/yr) in local
  `.env`; unset = SF/NYC rejected.
- **Salary floor** — confirm `SALARY_FLOOR` in local `.env` is the real ITS I monthly rate.
- ~~**Mail provider**~~ RESOLVED 2026-10-28: Gmail SMTP for the weekly digest.
- **City of Sacramento pension** — CalPERS or legacy SCERS for new hires?

## Known Risks

- Scrapers break when source sites redesign. Playwright sources (CalCareers, CSU) are the
  most fragile; Workday/Greenhouse JSON APIs are steadier but undocumented.
- Remote vs hybrid is inferred from posting text and can be fooled (e.g. "fully remote
  program"). Sources should set `remote_type` explicitly where they can.
- Nike lists no structured salary; most Nike jobs are "pay unverified". Pay parsed from
  description text treats "$" as USD (peso risk on Mexico postings).
- The Mac must be awake for the 06:00 crawl and Sunday 07:30 digest; launchd fires missed
  jobs on next wake, possibly together.
- LinkedIn / Indeed deliberately excluded. Some good roles missed.

### Known Regressions

- **EdJoin deactivated 2026-07-08 — `/Home/LoadJobs` decommissioned in favor of
  `/Home/RegionSearch` (JS-driven). Source rebuild required to reactivate. Held pending
  decision.** Was 234 records on 2026-06-03; 0 across all 5 locked queries since 2026-06-26.
  Triage ruled out filter-param drift: the unfiltered baseline probe (`jobTypes=0&regions=0`,
  no keywords) AND a bare `keywords=teacher` sweep both return `totalRecords=0` — the endpoint
  itself no longer serves the catalog (still 200 OK with well-formed empty JSON — a silent
  deprecation, not a 404). The live `/Home/Jobs` page has zero `LoadJobs` references and 5 to
  `/Home/RegionSearch`. Deactivated in the pipeline (commented out of `sources.yaml`, removed
  from `filter.ALLOWED_SOURCES`, disabled in `main._build_sources`); parser code, tests,
  fixtures, and `notes/edjoin-recon.md` retained intact. Reactivation = capture the
  RegionSearch contract via Playwright, re-derive jobType/region IDs, rewrite `edjoin.py`
  (likely moves EdJoin onto the Mac+Playwright path CalCareers/CSU already use). Deferred
  until after the SJSU application deadline (2026-07-21). Not blocking other sources.

## Decisions Log

- **2026-05-27** — Hosting: `jobs.jtplatt.com` subdomain on Opalstack (separate app).
- **2026-05-27** — Stack: Python (not Node).
- **2026-05-27** — Email-on-perfect-only. Page is the always-on artifact.
- **2026-05-27** — Cron daily at 06:00 PT.
- **2026-05-27** — Auth via Flask HTTP basic auth (no nginx config needed).
- **2026-05-27** — Two-tier scoring: rule-based for all, LLM for daily top 10.
- **2026-06** — CalCareers + CSU built on Playwright (JS-heavy); deviates from spec's requests+BS4.
- **2026-06** — EdJoin: 5-query design with pagination, listing-API-first field mapping.
- **2026-06** — Tier 2 DEFERRED until real T1 >= 80 candidates emerge. Top T1 score with 4 sources is 60.
- **2026-06** — Email DEFERRED with Tier 2; trigger is T1 >= 80 AND T2 >= 8 per spec.
- **2026-06** — governmentjobs DE-PRIORITIZED as optional v1.x; not blocking v1 deploy.
- **2026-06** — Sustained-pace probe (N>1 fetches at production cadence) locked as recon standard after CSU/AWS WAF incident.
- **2026-06** — Upsert-then-filter architecture locked (main.py = production entry point; scripts/render_demo.py deleted).
- **2026-06** — Diffs-before-greenlight as commit process rule.
- **2026-06-26** — Mac-publish architecture locked: server hosts/serves only; Mac crawls. CentOS 7 glibc 2.17 cannot run Playwright Chromium. GitHub Actions evaluated and deferred as v2 (always-on alternative if Mac-dependency becomes friction).
- **2026-06-26** — devtoolset-11 sourced before pip in deploy.sh for greenlet C++ compile.
- **2026-06-26** — Two-phase deploy gate (review-only by default; DEPLOY_CONFIRM_RESTART=1 to actually cycle uWSGI).
- **2026-06-30** — `publish.sh` built and live: Mac → server rsync of output/index.html + data/jobs.db with an auth-gated /health probe.
- **2026-06-30** — Daily automation live: Mac launchd job (`~/Library/LaunchAgents/com.jtplatt.jobs.plist`, outside repo) runs the full crawl + publish at 06:00 PT; end-to-end pipeline confirmed working. v1 ops loop complete.
- **2026-10-28** — Multi-lane rewrite: five priority lanes; lane keywords kept separate from SKILLS; output ranks by lane, then score.
- **2026-10-28** — Pay normalized to USD-monthly at ingest; unknown currency / no salary = "pay unverified"; international and SF/NYC need verified pay, except lane 1.
- **2026-10-28** — Nike source via Workday CxS JSON API (site `nke`, Retail Stores excluded, detail fetch for new jobs only). robots.txt disallows only the human `/nke/` pages.
- **2026-10-28** — Weekly digest email replaces the website and the T1 >= 80 per-job alert. Website (jobs.jtplatt.com) retired; `publish.sh` removed from the daily run.
