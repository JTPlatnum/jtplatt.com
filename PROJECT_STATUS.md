# PROJECT_STATUS.md — Portfolio (jtplatt.com root)

**Last updated:** 2026-06-30

This is the status doc for the **portfolio site at the repo root** (`jtplatt.com`).
The job agent has its own separate doc at [`jobs/PROJECT_STATUS.md`](jobs/PROJECT_STATUS.md).
One repo, two independent Opalstack apps — see [`README.md`](README.md).

## Current State

**Live as of 2026-06-30** at https://jtplatt.com. Static, hand-built, no framework, no
build step, no tracker. Refreshed from the stale 2023 Bootstrap site into an editorial
"five worlds" layout. The point of the site is narrow and deliberate: a **passive
credibility artifact** — someone hears about JT, googles him, lands here, and gets the
right impression. Not a destination, not active marketing.

## Architecture & Deploy

- **Files:** static `index.html` + `styles.css` + `img/` at the repo root. That's the
  whole site.
- **Host:** Opalstack **static app `jtplatt`**, on host **`vps233`**, under the
  **`zorrotmm`** shell account — NOT `jtplatnum`. Document root: `~zorrotmm/apps/jtplatt/`.
- **Deploy = rsync, not git.** The Opalstack static app does not pull from GitHub; files
  reach it by rsync over SSH. The git push only updates GitHub.
- **Cross-shell-user constraint (matters):** the working copy lives under the `jtplatnum`
  Mac/account, but the portfolio app lives under a **different** Opalstack account,
  `zorrotmm`. Deploy is therefore a cross-user rsync to
  `zorrotmm@vps233.opalstack.com:~/apps/jtplatt/`. `zorrotmm` is **password-based** (no
  SSH key installed), so the rsync runs through an `expect` wrapper feeding the password
  (`~/.ssh/config` alias `opal-zorrotmm`, with `PubkeyAuthentication=no` to skip the
  passphrase-protected default key). This is why the jobs-agent deploy scripts don't apply
  here — different account, different auth.
- **Deploy command shape:**
  ```
  rsync -avz --exclude='*-jt-*.png' --exclude='.DS_Store' \
    -e "ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password" \
    index.html styles.css img opal-zorrotmm:~/apps/jtplatt/
  ```
  The `*-jt-*.png` exclude keeps the raw source photos off the server (see Git below).

## Design & Content (locked decisions)

- **Layout (top→bottom):** compact hero → **five-worlds panel strip** → Selected Work
  gallery → "the full record" timeline → contact. Editorial, warm off-white `#FAFAF7`.
- **Type:** Source Serif (body, captions, the voice) + Archivo Narrow (panel titles,
  section labels, structure). Chosen over Oswald.
- **Hero:** arc line (locked) — *"From Wall Street finance and Silicon Valley startups to
  classrooms in East Oakland, the sneaker industry, and state IT in Sacramento."* Sub-line
  (locked) — *"Five different worlds, same move: learn how the room works, then build the
  thing it's missing."* Desktop: headshot ~20% left, headline + sub-line ~80% right;
  stacks on mobile.
- **Five panels:** 01 Wall Street, 02 Silicon Valley, 03 East Oakland, 04 Sneakers,
  05 California. Each 4:5, **unified grayscale-warm-duotone** treatment (dark `#23201b`,
  light `#efe9dc`) so disparate sources read as one set. JT monogram stamp + index number
  on each.
- **Panel images:** **four personal photos** (Platt St corner NYC; SF alley + bike;
  teaching a geometry lesson with student faces blurred; running the Sneaker Savant
  convention table) **+ one iconic image** (low-angle California State Capitol dome,
  DimiTalen, CC0). Mixed personal/iconic is intentional: documentation where it exists,
  representative imagery where it doesn't. Raw source PNGs processed → `img/world-*.jpg`.
- **Sneaker Savant naming:** "The Sneaker Savant" is the brand throughout; describe the
  methodology generically ("a sneaker grading and consultancy practice") — JT does not
  hold the "Shoemetrics" trademark, so it isn't led as a noun. Metrics shipped: 17,000-
  person community, 100,000+ pairs analyzed. **No StockX claim, no 1.5M-reach claim**
  (can't be substantiated).
- **Contact line (locked):** *"Currently in California state IT. Open to conversations
  about technical roles in public sector or education."* Email shown as the full visible
  address `James.T.Platt@gmail.com` (mailto), plus LinkedIn + GitHub. Bottom placement is
  correct (passive artifact).

## Git

- **Origin was overwritten via `git push --force-with-lease`.** Pre-existing `origin/master`
  carried a destructive 2023 Opalstack-side commit (`54ad3e8`) that deleted the portfolio
  PDFs and diverged from local by 30+ commits. Local was the source of truth; origin was
  force-overwritten to match. **Do NOT `git pull`** out of old habit.
- Origin/master is now at the portfolio commit **`ab2c448`**. The destructive history is
  gone; future portfolio commits are normal fast-forward pushes.
- **Raw panel source PNGs (`img/*-jt-*.png`) are git-ignored** and rsync-excluded — they're
  ~1MB un-treated personal color photos; the repo and server hold only the treated
  `img/world-*.jpg` that the page actually serves.

## Open Items

- **Opalstack account consolidation (deferred):** the portfolio (`jtplatt`),
  `thesneakersavant`, and `djclick` apps live under the **`zorrotmm`** account while the
  job agent and most other work live under **`jtplatnum`**. Migrating them onto `jtplatnum`
  would remove the cross-user/password-auth deploy friction. Explicitly **paused** —
  "Sneaker Savant infrastructure is too valuable to migrate hastily."
- **Selected Work screenshot privacy:** the `jobs.jtplatt.com` card image is the agent's
  daily report with posting details redacted (employer / location / salary / title / date
  blurred). Re-check on any future screenshot refresh that no employer names leak — this
  has regressed before (locations were unblurred in an earlier pass).
- **Resume PDF:** the old four 2023 PDFs are unlinked; no current resume is linked (contact
  routes to LinkedIn). Drop a fresh PDF in `downloads/` and link it when ready.
