# PROJECT_STATUS.md

**Last updated:** 2026-06-26

## Current State

Pipeline runs end-to-end. `main.py` does upsert → hard-filter → Tier 1 score → render,
with `--dry-run`. `server.py` is **deployed and live** at https://jobs.jtplatt.com,
behind Flask HTTP basic auth (`/health` returns `ok`, `/` returns 401 unauthenticated).
It serves the "Awaiting first run" placeholder until the Mac publishes real output.
Four of five sources are live. Tier 2 (LLM), email, and the governmentjobs source are
not built yet. Full test suite passes (221 tests).

**Architecture (locked):** the box is CentOS 7 (glibc 2.17) and cannot run
Playwright/Chromium, which the CalCareers + CSU sources need. So the crawl runs on the
**Mac** (where Playwright works) and publishes `output/index.html` + `data/jobs.db` to
the server; the server only serves the page. `deploy.sh` ships code to the server;
a separate `publish.sh` (next) does the Mac → server data sync.

## What's Done

- [x] Repo skeleton, venv at `./venv/`, pinned `requirements.txt`
- [x] `data/inventory.py` — career inventory, sole title/keyword source
- [x] `crawler/base.py` — abstract `Source` class + `Posting` dataclass
- [x] **USAJobs source** — official API, query config, field mapping verified from probe
- [x] **CalCareers source** — live, via Playwright
- [x] **CSU source** — live, via Playwright (all campuses)
- [x] **EdJoin source** — live, 5-lane queries with pagination, listing-API-first mapping
- [x] `filter.py` — hard pass/fail rules before scoring
- [x] `score.py` — Tier 1 rule-based scoring (title + classification)
- [x] `store.py` — SQLite persistence layer
- [x] `render.py` + `templates/results.html` + `static/styles.css` — results page
- [x] `main.py` — production entry point, upsert-then-filter pipeline, `--dry-run`
- [x] `server.py` — Flask app, HTTP basic auth, serves HTML at `/`, `/health` probe
- [x] `deploy.sh` — server-only Opalstack deploy, **verified live** (see notes below)
- [x] Test suite — 221 passing

## What's Not Built Yet

Remaining v1 ops items (the Mac → server side of the architecture):

- [ ] **`publish.sh`** (repo root) — Mac → server rsync of `output/index.html` +
  `data/jobs.db`, then a `/health` auth probe. In progress.
- [ ] **Mac launchd job** — schedules the daily local crawl (`main.py`) + `publish.sh`.

These files exist as one-line stubs:

- [ ] **`crawler/sources/governmentjobs.py`** — multi-tenant scraper (Sacramento County/City, etc.)
- [ ] **`score_llm.py`** — Tier 2 LLM scoring (daily top 10, Anthropic API)
- [ ] **`notify.py`** — email on perfect-fit (Tier 1 >= 80 AND Tier 2 >= 8)
- [ ] Threshold tuning (after data accumulates)

Future polish (not blocking): split server-only vs crawler-only requirements so the
server stops compiling/installing Playwright (and greenlet) it never uses.

## What's Next (suggested order)

1. **`publish.sh`** — Mac → server data sync (output/index.html + data/jobs.db). In progress.
2. **Mac launchd job** — daily local crawl + publish, ~06:00 PT.
3. **governmentjobs source** — optional, de-prioritized in original scoping. Build only
   if the v1 surface feels thin after 2–4 weeks of real data.
4. **Tier 2 LLM scoring (`score_llm.py`)** — deferred until real T1 >= 80 candidates
   surface (current top T1 is 60, so the LLM has nothing to upgrade). Cost-conscious
   decision.
5. **Email notifications (`notify.py`)** — deferred with Tier 2, since the trigger is
   specced as T1 >= 80 AND T2 >= 8. Can be unblocked earlier by changing the trigger to
   T1-only if desired.
6. **Threshold tuning** after 2–4 weeks of live data.

## Spec Deviations (intentional, noted)

- SPEC.md lists requests + BeautifulSoup4 for scraping. CalCareers and CSU were built
  on **Playwright** instead (JS-heavy sites). Real choice, works — flagged here so the
  spec and reality don't silently diverge.
- SPEC implied the crawl runs server-side (Opalstack cron). Reality: the crawl runs on
  the **Mac** and publishes to the server — CentOS 7 (glibc 2.17) can't run Chromium.
- `deploy.sh` sources **devtoolset-11** before pip: greenlet (a Playwright dep) compiles
  a C++ extension, and CentOS 7's system g++ 4.8 predates C++11. devtoolset statically
  links the newer libstdc++, so the built `.so` runs under the base system afterward.
- `deploy.sh` is **two-phase**: a default run applies the config edits and prints them
  for review, then STOPS; `DEPLOY_CONFIRM_RESTART=1 ./deploy.sh` actually cycles uWSGI
  and runs the health check. A failed health check does NOT auto-roll back.

## Open Items for JT

- **Mail provider** — `.env.example` still offers Mailgun / SendGrid / SMTP. Pick one
  before `notify.py`.
- **Salary floor** — `.env.example` ships a placeholder `SALARY_FLOOR=8000`. Set the real
  ITS I monthly rate in local `.env`.
- **Anthropic API key** — server `.env` only; needed for Tier 2.
- ~~**Opalstack** — Python version / install path / WSGI pattern.~~ RESOLVED: Python
  3.12.13, uWSGI 2.0.31, app at `/home/jtplatnum/apps/jt_jobs/`, port 18675. Deployed.
- **City of Sacramento pension** — CalPERS or legacy SCERS for new hires? Drives `employers.yaml`.

## Known Risks

- Scrapers break when source sites redesign. Playwright sources (CalCareers, CSU) are the
  most fragile. Budget maintenance time; track per-source last-success.
- LinkedIn / Indeed deliberately excluded — paid APIs not worth it. Some good roles missed.
- Thresholds (Tier 1 >= 80, Tier 2 >= 8) are educated guesses. Tune after 2–4 weeks of data.
- "Perfect fit" is fuzzy by definition. Aim is to cut noise, not eliminate it.
- CentOS 7 (glibc 2.17) can't run Playwright/Chromium → the crawl runs on the Mac. Risk:
  if the Mac is off at the scheduled run time, there's no fresh crawl that day. GitHub
  Actions is the v2 fallback if this dependency becomes friction.

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
