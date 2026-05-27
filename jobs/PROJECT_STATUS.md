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

1. **Repo init.** `git init`, venv, `requirements.txt`, project skeleton from SPEC.md → "Project Structure"
2. **Verify `data/inventory.py`** with JT — confirm structure matches his actual career data, no inventions
3. **`crawler/base.py`** — abstract `Source` class with `fetch_listings()` and `parse_posting()`
4. **`crawler/sources/calcareers.py`** — highest yield, structured pages. Get this end-to-end first.
5. **`data/employers.yaml`** — allow-list and excludes
6. **`filter.py`** — hard filters per SPEC.md
7. **`data/title_patterns.yaml`** — yes/no title bias lists
8. **`score.py`** — Tier 1 only
9. **`store.py`** — SQLite schema + I/O
10. **`render.py`** + minimal `templates/results.html` — page generation
11. **`main.py`** — wire it all into a single cron entry point with `--dry-run`
12. **Local end-to-end** — run against CalCareers live, eyeball output
13. **Add governmentjobs.com** with multi-tenant config
14. **Add USAJobs** (API, easy)
15. **Add EdJoin, Peace Corps, Making Waves**
16. **`score_llm.py`** — Tier 2 via Anthropic API
17. **`notify.py`** — email send (provider TBD)
18. **`server.py`** — Flask app with HTTP basic auth, serves the rendered HTML
19. **`deploy.sh`** + Opalstack cron entry
20. **Tune thresholds** after 2 weeks of data

Do one source end-to-end (steps 3–12) before adding more scrapers. Resist parallel-track temptation.

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
