# CLAUDE.md — Job Agent Operating Manual

## Read First, Every Session

- This file
- `PROJECT_STATUS.md` — current state, what's in flight, what's done
- `SPEC.md` — the contract; do not deviate without asking JT
- `../SOUL.md` (JT's universal operating rules — voice, hard rules, deployment philosophy)

## What This Project Is

Standalone Python service that crawls public-sector job sources daily, filters against JT's hard criteria, scores against his career inventory, surfaces near-perfect matches via page + email. Lives at `jobs.jtplatt.com` on Opalstack.

**Not connected to QuickStrike, Morning Call, or Sneaker Savant.** Different repo, different stack, different deploy. Don't import from or reference those projects.

## Scope Discipline

- Only change what's explicitly asked. No surprise refactors.
- When investigating a bug, show diagnostic data BEFORE writing code.
- One problem per session when possible.
- If a fix spawns three new problems, stop and rethink.
- Don't change thresholds, cap numbers, intervals, or scoring weights without asking.

## Inventory Is Sacred

`data/inventory.py` is the only source of truth for JT's experience. Never invent, extend, or "fill in" experience that isn't there. If a posting needs something JT doesn't have, the scorer flags it as a gap. The LLM tier (`score_llm.py`) is also instructed not to invent — its prompt must include the no-fabrication rule.

When JT updates his career, he updates `inventory.py`. The agent re-scores at next run.

## Sources Are Real, Not Mocked

Every scraper hits the live site. No fake data, no synthetic test postings except in unit tests under `tests/fixtures/`. If a source breaks, fail loudly — don't silently fall back to stale data.

## Empty Scrapes Never Overwrite Good Data

If a scraper returns zero results for a source that previously returned results, treat as failure. Keep the previous run's data. Log the failure with stack trace. Don't push an empty page.

## Rate Limit Awareness

Public sites get hit gently:

- Default delay between requests within a source: 2 seconds
- Respect `robots.txt` where reasonable
- USAJobs has its own documented rate limit — stay well under it
- Never run scrapers in parallel within a single source (sequential pagination only)
- If a source returns 429 or 5xx, back off and skip the rest of that source for this run

## Email Discipline

- Send only when Tier 1 >= 80 AND Tier 2 >= 8 (current thresholds; in `.env`)
- Never re-email the same job ID
- One job per email (no digests for v1)
- If email send fails, log it; do NOT silently drop — mark the job for retry next run

## Hard Rules (from SOUL.md)

- Test locally first: `python main.py --dry-run`
- `./deploy.sh` is the only deploy path
- Never SSH to patch live
- Cron uses full Python path (Opalstack pattern)
- Server `.env` independent from local; deploy never overwrites it
- Never push `.env` files or secrets to the repo
- Backup `data/jobs.db` before destructive migrations

## Tech

- Python 3.10+, Flask, requests, BeautifulSoup4, SQLite (stdlib), Jinja2, PyYAML, python-dotenv, gunicorn, anthropic
- Pin versions in `requirements.txt`
- Use a venv at `./venv/`

## Logging

Every run writes a structured log to `logs/run-YYYY-MM-DD.log` containing per-source stats:

- Source name
- Postings discovered (new vs. seen-before)
- Hard-filter rejects with reason counts
- Tier 1 score histogram
- Tier 2 calls made + tokens used
- Errors with traceback
- Total run time

## Prompt Style (when JT writes prompts for you)

JT writes prompts in code blocks. Expect:

- "Read CLAUDE.md and PROJECT_STATUS.md for context"
- Specific about what to change and what NOT to change
- One concern per prompt
- Ends with "Run tests and show me results"

Match the energy. No fluff in responses. Brevity is law.

## When You Hit a Decision JT Hasn't Spec'd

Stop. Ask. Don't pick.

The spec is dense on purpose — most decisions are already made. Anything not in the spec is either (a) implementation detail you can decide, or (b) a real design call that needs JT. If you're not sure which, it's (b).
