# PROJECT_STATUS.md

**Last updated:** 2026-05-27 (initial spec, pre-implementation)

## Current State

Pre-implementation. Spec just landed. Repo not initialized yet.

## What's Done

- [x] SPEC.md drafted
- [x] CLAUDE.md drafted
- [x] `data/inventory.py` drafted from JT's career inventory (no fabrications)
- [x] Repo structure decided
- [x] Hosting decided: `jobs.jtplatt.com`, separate Opalstack app
- [x] Stack decided: Python 3.10+, Flask, SQLite
- [x] Source list decided for v1
- [x] Two-tier scoring model decided (rule-based + LLM for top 10)

## What's Next (Build Order)

1. ✓ **Repo init** — skeleton, venv, requirements
2. ✓ **`data/inventory.py`** — verified
3. ✓ **`crawler/base.py`** — abstract `Source` class
4. **USAJobs source (API)** — first real source (developer.usajobs.gov)
5. **`filter.py`** — hard filters per SPEC.md
6. **`score.py`** + **`data/title_patterns.yaml`** — Tier 1
7. **`store.py`** — SQLite schema + I/O
8. **`render.py`** + minimal **`templates/results.html`**
9. **End-to-end local run** with USAJobs as sole source
10. **CalCareers** — deferred until pipeline proven
11. **`governmentjobs.com`** — similar ASP.NET pattern; benefits from CalCareers lessons
12. **EdJoin**
13. **CSU Careers**
14. **`score_llm.py`** — Tier 2 via Anthropic API
15. **`notify.py`** — email send
16. **`server.py`** — Flask app with HTTP basic auth
17. **`deploy.sh`** + Opalstack cron entry
18. **Tune thresholds** after 2 weeks of data

Do one source end-to-end (steps 4–9) before adding more scrapers. Resist parallel-track temptation.

## Open Items for JT

- Exact monthly salary floor — current ITS I rate → `.env: SALARY_FLOOR`
- City of Sacramento pension system for new hires — CalPERS or legacy SCERS? → drives `employers.yaml`
- Confirm Opalstack Python version available and the gunicorn pattern there
- Pick a mail-sending service — SMTP via Opalstack, Mailgun free tier, SendGrid free tier
- Anthropic API key → server `.env` only, never committed

## Known Risks

- Scrapers break when source sites redesign. Budget maintenance time. Add per-source "last-success" timestamps to the run log.
- LinkedIn / Indeed deliberately excluded — paid APIs not worth it. Some good roles may be missed.
- Thresholds (Tier 1 >= 80, Tier 2 >= 8) are educated guesses. Will need 2–4 weeks of data to tune.
- "Perfect fit" is fuzzy by definition. Aim is to cut noise, not eliminate it.
- Opalstack's CentOS 7 may force older Python via SCL (like the Node 20 SCL pattern). Confirm before assuming Python 3.10+.

## Decisions Log

- **2026-05-27** — Hosting: `jobs.jtplatt.com` subdomain on Opalstack (matches existing pattern; clean separation from QuickStrike/Morning Call).
- **2026-05-27** — Stack: Python (not Node). Better scraping ergonomics; standalone makes consistency-with-other-projects irrelevant.
- **2026-05-27** — Email-on-perfect-only. Page is the always-on artifact.
- **2026-05-27** — Cron daily at 06:00 PT. State postings move fast but not hourly.
- **2026-05-27** — Auth via Flask HTTP basic auth (no nginx config needed on Opalstack shared).
- **2026-05-27** — Two-tier scoring: rule-based for all, LLM for daily top 10.
