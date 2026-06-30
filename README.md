# jtplatt.com

JT Platt's personal repo. Two things live here as siblings:

| Path | What | Deploys to |
|---|---|---|
| `/` (root) | Portfolio site — static HTML/CSS, Bootstrap, PDF resumes | `jtplatt.com` (Opalstack static app) |
| `/jobs/` | Job-search agent — Python/Flask service | `jobs.jtplatt.com` (Opalstack Python app) |

One repo, one git history, but two independent Opalstack apps. Changes to the portfolio don't deploy the agent and vice versa — each subdirectory has its own deploy story.

`SOUL.md` at the repo root is the universal operating manual (voice, hard rules, deploy philosophy). Both subprojects reference it.

## Subprojects

- See `jobs/README.md` for the job agent.
- The portfolio at the root is currently static. A refresh is planned after the agent ships.
