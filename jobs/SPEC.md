# SPEC.md — Job Agent

The contract. Do not deviate without asking JT.

## What This Is

A standalone Python service that crawls a fixed set of public-sector job sources daily, filters against JT's hard criteria, scores against his career inventory (`data/inventory.py`), renders a results page at `jobs.jtplatt.com`, and emails him only when something scores as a near-perfect fit.

Lives at `jtplatt.com/jobs/` in the same repo as the portfolio site, but runs as its own Opalstack app at the `jobs.jtplatt.com` subdomain. Independent of QuickStrike, Morning Call, and Sneaker Savant — different stack (Python, not Node), different deploy, never imports from those projects.

---

## Hard Filters (must pass ALL)

A posting that fails any one of these is dropped. Not shown, not scored, not emailed.

1. **Salary floor.** Monthly minimum >= `SALARY_FLOOR` in `.env`. JT sets this to his current ITS I monthly rate.
2. **Employer allow-list.** Posting employer matches an entry in `data/employers.yaml`. The list covers:
   - CalPERS-continuing (CA state departments, CSU campuses, certain special districts)
   - CalSTRS-continuing (CA K-12 districts, community college districts)
   - SCERS / '37 Act counties with CalPERS reciprocity (Sacramento County in particular)
   - Federal (FERS — separate system but acceptable per JT)
   - Overseas teaching exceptions (DODEA, Peace Corps Response, accredited international schools)
3. **Flexibility OR location OR overseas.** Posting must show one of:
   - Telework/remote/hybrid keyword in the listing, OR
   - Location within Sacramento metro (Sacramento, Yolo, Placer, El Dorado counties), OR
   - Overseas teaching role
4. **Explicit excludes.** Reject if employer matches anything in `data/employers.yaml` → `explicit_exclude`. FI$CAL is the only entry today.
5. **Disqualifying requirements.** Reject if the posting requires any credential or qualification in `data/reject_requirements.yaml`. Initial list:
   - PE license, CPA, JD, MD, RN, LCSW
   - Active TS/SCI or other high clearances
   - CCIE, CISSP, AWS Solutions Architect Professional, GCP Professional, Azure Expert
   - "5+ years recent" requirements for languages/frameworks not in JT's inventory (e.g. recent React, recent Go, recent Kubernetes)

If a posting requires something the inventory can't back, it gets rejected. We don't fudge.

---

## Soft Scoring

Two tiers. Tier 1 runs on every posting that clears hard filters. Tier 2 runs only on the daily top 10 by Tier 1.

### Tier 1 — Rule-based (0–100)

Computed in `score.py`. Components:

| Component | Weight | Source |
|---|---|---|
| Inventory keyword match | 40 | Keywords extracted from `data/inventory.py` |
| Title pattern match | 25 | `data/title_patterns.yaml` (yes/no lists) |
| Flexibility bonus | 10 | Posting mentions telework/remote/flexible/hybrid |
| Person-facing bonus | 10 | Training/instruction/liaison/requirements/consulting keywords |
| International bonus | 10 | Overseas/international/Foreign Service/DODEA |
| Recency bonus | 5 | Posting < 7 days old |

Title patterns (initial — tune in repo):

- **YES bias:** Information Technology Specialist I/II, Business Systems Analyst, Applications Analyst, Training Officer, Education Programs Consultant, Adult Education Instructor, IT Consultant (CSU), Instructional Designer, Staff Services Analyst (training focus)
- **NO penalty:** Senior Software Engineer (recent-stack-heavy), Database Administrator, Network Architect, Cybersecurity Analyst requiring CISSP/CCIE, anything with "Senior Full-Stack" or "Lead DevOps"

### Tier 2 — LLM scoring (0–10)

`score_llm.py`. For the daily top 10 by Tier 1 score only.

Sends posting text + a structured summary of JT's inventory + the project's no-fabrication rules to the Anthropic API. Asks for:

- A 0–10 fit score
- One-sentence rationale
- Up to three explicit gaps where JT's inventory doesn't match the posting

Costs are negligible — pennies/day. Use `claude-sonnet-4-20250514` or current equivalent.

---

## Email Trigger

JT gets an email only if a posting hits **Tier 1 >= 80 AND Tier 2 >= 8**.

Email contains: title, employer, salary, location, apply link, Tier 2 rationale, gaps. One email per posting (never re-email after dedup).

Everything else lives on the results page. He browses there.

---

## Results Page (`jobs.jtplatt.com`)

Flask app behind HTTP basic auth. Password in `.env`. No nginx config required — auth is enforced in the Flask app.

### Sections (top to bottom)

1. **Perfect fits** — emailed today (Tier 1 >= 80, Tier 2 >= 8)
2. **Strong matches** — Tier 1 60–79
3. **Decent matches** — Tier 1 40–59 (collapsible)
4. **Below threshold** — Tier 1 < 40 (hidden by default)

### Per-row

Title | Employer | Salary band | Location | Telework flag | Source | Posted date | Tier 1 score | Tier 2 score (if any) | Apply link | "Why" expand panel

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

- `jobs` — id, source, source_job_id, title, employer, url, salary_min, salary_max, location, telework_flag, raw_text, first_seen, last_seen, status (active/closed)
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
│       ├── peacecorps.py
│       └── makingwaves.py
├── data/
│   ├── inventory.py             # JT's career inventory (source of truth)
│   ├── employers.yaml           # Allow-list and excludes
│   ├── sources.yaml             # governmentjobs.com tenants etc.
│   ├── title_patterns.yaml
│   ├── reject_requirements.yaml
│   └── jobs.db                  # SQLite (gitignored)
├── filter.py                    # Hard filters
├── score.py                     # Tier 1
├── score_llm.py                 # Tier 2
├── store.py                     # SQLite I/O
├── notify.py                    # Email
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
2. City of Sacramento pension system — CalPERS or legacy SCERS for new hires? → drives `employers.yaml`
3. Opalstack Python version available and gunicorn setup pattern → confirm during deploy
4. Anthropic API key → goes in `.env` server-side only
5. Mail-sending service — SMTP via Opalstack, Mailgun free tier, SendGrid free tier? → pick one before Tier 2 build
6. Tier 1 / Tier 2 thresholds — start at 80 / 8 per this spec, tune after data accumulates
