# jobs/ — Job Agent

JT's personal job-search agent. Standalone Python service.

Lives inside the `jtplatt.com` repo as a subdirectory, but runs as its own Opalstack app at `jobs.jtplatt.com`. Independent of the portfolio at the repo root, and independent of QuickStrike, Morning Call, and Sneaker Savant.

## Read These First

1. `SPEC.md` — what we're building, in detail
2. `CLAUDE.md` — operating manual for Claude Code in Cursor
3. `PROJECT_STATUS.md` — current state, build order, open items
4. `data/inventory.py` — source of truth for JT's career experience
5. `../SOUL.md` — universal operating rules (voice, deploy philosophy)

## Cursor workflow

Open `jobs/` as the Cursor workspace root when working on the agent. That way `.cursorrules` and `CLAUDE.md` apply cleanly without leaking into work on the portfolio.

## Quick Setup (once code lands)

```bash
cd jobs
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with secrets, SALARY_FLOOR, etc.
python main.py --dry-run
```

## Deploy

```bash
./deploy.sh
```

Per SOUL.md: never SSH to patch live. Everything goes through code → test → push → deploy. Deploy syncs only the `jobs/` subdirectory to the `jobs.jtplatt.com` Opalstack app; the portfolio at the repo root is unaffected.
