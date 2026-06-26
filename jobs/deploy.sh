#!/usr/bin/env bash
#
# deploy.sh — the ONLY path to production for the jobs agent.
# Run from inside jobs/:   ./deploy.sh
#
# Per SOUL.md: one step, zero passwords (SSH keys), zero manual fixes after.
# Never SSH to patch live — every fix goes through this script.
#
# What it does:
#   1. rsync the jobs/ dir to the Opalstack app dir (no --delete; server-only
#      files like env/, uwsgi.ini, start/stop, .env are preserved)
#   2. pip install -r requirements.txt into the existing 3.12 venv
#   3. point uwsgi.ini at server:app (default is myapp/wsgi.py)
#   4. ensure the app's start script loads the server .env (see NOTE below)
#   5. restart uWSGI via ./stop && ./start            [gated — see below]
#   6. verify https://jobs.jtplatt.com/health returns "ok"   [gated]
#
# This server ONLY serves the rendered page — it never runs main.py. There is
# deliberately no `playwright install chromium`: CentOS 7 (glibc 2.17) can't run
# Playwright/Chromium. The crawl (incl. the CalCareers/CSU browser sources) runs
# on the Mac, which then publishes output/index.html + data/jobs.db here via a
# separate publish step.
#
# TWO-PHASE (review gate):
#   Default run does steps 1-5, then prints the final uwsgi.ini and start
#   (with a diff vs their .bak) and STOPS before cycling uWSGI, so the config
#   can be eyeballed first. To actually cycle uWSGI + health-check, re-run:
#       DEPLOY_CONFIRM_RESTART=1 ./deploy.sh
#   The re-run is idempotent (config edits don't pile up; pip/playwright no-op).
#
# On a FAILED health check it does NOT roll back — it stops and surfaces the
# error so the broken state stays intact for diagnosis. Rollback is a manual
# call (command printed on failure).
#
# Exits nonzero on ANY failure so it shows up in the shell.
#
# NOTE — server .env injection (CONFIRM before first run):
#   server.py refuses to import without ADMIN_USER/ADMIN_PASSWORD in the
#   environment. uWSGI doesn't read .env files. So step 5 prepends a line to
#   the app's `start` script that sources $APP_DIR/.env before uwsgi execs.
#   Secrets stay in the server-only .env — never in the repo, never here.

set -euo pipefail

# --- Config (CONFIRM the two marked items match the live app) --------------
SSH_USER="jtplatnum"
SSH_HOST="vps233.opalstack.com"
APP_DIR="/home/jtplatnum/apps/jt_jobs"        # confirmed
VENV="${APP_DIR}/env"                          # confirmed (Python 3.12.13)
HEALTH_URL="https://jobs.jtplatt.com/health"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"

# Restart/health phase runs only when this is set to 1. Default (unset/0) stops
# after the config edits and prints the files for review.
CONFIRM="${DEPLOY_CONFIRM_RESTART:-0}"

# Resolve to the directory this script lives in (jobs/), so it works
# regardless of the caller's CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

fail() { echo "DEPLOY FAILED: $*" >&2; exit 1; }

echo "==> [1/6] Syncing jobs/ -> ${SSH_TARGET}:${APP_DIR}/"
# No --delete: must not wipe server-only files (env/, uwsgi.ini, start/stop/
# kill, myapp/, tmp/, .env, data/jobs.db, logs/, output/).
rsync -az \
  --exclude 'venv/' \
  --exclude '.env' \
  --exclude 'data/jobs.db' \
  --exclude 'logs/' \
  --exclude 'output/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'tests/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  ./ "${SSH_TARGET}:${APP_DIR}/" \
  || fail "rsync failed"

echo "==> [2-4] Remote: install deps, wire uWSGI config (no restart yet)"
ssh "$SSH_TARGET" bash -s <<'REMOTE' || fail "remote provisioning/config failed"
set -euo pipefail

APP_DIR="/home/jtplatnum/apps/jt_jobs"
VENV="${APP_DIR}/env"
cd "$APP_DIR"

# Runtime dirs that are excluded from the sync but must exist.
mkdir -p logs output data

echo "  -> [2/6] pip install -r requirements.txt (devtoolset-11 for C++ ext builds)"
# CentOS 7's system g++ (4.8) predates C++11, so any C-extension lacking a
# cp312/glibc-2.17 wheel (notably greenlet, a Playwright dep) fails to compile.
# Enable devtoolset-11 (g++ 11). It links the newer libstdc++ statically, so the
# built .so runs under the base system later (no SCL at uWSGI/cron runtime).
# Source the enable file directly, not `scl_source` — the wrapper returns
# nonzero inside a non-interactive `bash -s` shell and trips set -e. Relax
# -e/-u around the source (it touches unset vars and may return nonzero).
set +e +u
source /opt/rh/devtoolset-11/enable
set -e -u
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt

echo "  -> [3/6] point uwsgi.ini at server:app"
[ -f uwsgi.ini ] || { echo "uwsgi.ini missing in $APP_DIR" >&2; exit 1; }
# Create-once backup: preserve the PRISTINE original across re-runs so the diff
# below always shows original -> current, and rollback restores the real default.
[ -f uwsgi.ini.bak ] || cp uwsgi.ini uwsgi.ini.bak
# Strip the default app-callable + reload + chdir directives, then set ours.
# The stock Opalstack ini uses `wsgi-file`/`touch-reload` pointed at
# myapp/wsgi.py. Deleting chdir/module/touch-reload too keeps this idempotent
# across re-runs (no duplicate lines piling up). All other directives
# (http-socket, virtualenv, daemonize, pidfile, workers, threads) are kept.
sed -i -E '/^[[:space:]]*(module|wsgi|wsgi-file|mount|touch-reload|chdir)[[:space:]]*=/d' uwsgi.ini
{
  printf 'module = server:app\n'
  printf 'chdir = %s\n' "$APP_DIR"
  printf 'touch-reload = %s/server.py\n' "$APP_DIR"
} >> uwsgi.ini

echo "  -> [4/6] ensure start sources the server .env"
[ -f start ] || { echo "start script missing in $APP_DIR" >&2; exit 1; }
if ! grep -q 'jt_jobs/.env' start; then
  [ -f start.bak ] || cp start start.bak
  # Insert right after the shebang so uwsgi inherits the exported vars.
  sed -i '1a set -a; [ -f '"$APP_DIR"'/.env ] && . '"$APP_DIR"'/.env; set +a' start
fi

# --- Show final config for review (before any restart) ---------------------
echo
echo "========== uwsgi.ini  (diff vs .bak) =========="
diff -u uwsgi.ini.bak uwsgi.ini || true
echo "========== uwsgi.ini  (final, full) =========="
cat uwsgi.ini
echo
echo "========== start  (diff vs .bak) =========="
if [ -f start.bak ]; then
  diff -u start.bak start || true
else
  echo "(no start.bak — .env line already present from a prior run; unchanged)"
fi
echo "========== start  (final, full) =========="
cat start
REMOTE

if [ "$CONFIRM" != "1" ]; then
  echo
  echo "==> Config applied and shown above. uWSGI NOT cycled yet."
  echo "    Eyeball the two files. When satisfied, cycle uWSGI + health-check with:"
  echo "        DEPLOY_CONFIRM_RESTART=1 ./deploy.sh"
  exit 0
fi

echo "==> [5/6] Remote: restart uWSGI (stop, wait for release, start)"
ssh "$SSH_TARGET" bash -s <<'REMOTE' || fail "uWSGI restart failed (config left intact; not rolled back)"
set -euo pipefail
cd /home/jtplatnum/apps/jt_jobs

# Note the currently-running master (if any) before stopping.
OLDPID=""
[ -f tmp/jt_jobs.pid ] && OLDPID="$(cat tmp/jt_jobs.pid 2>/dev/null || true)"

./stop || true

# uWSGI --stop is graceful and releases 127.0.0.1:18675 asynchronously. Starting
# before the old master frees the socket causes 'Address already in use', which
# kills the new master too (this exact race took the app down on first cycle).
# Wait for the old pid to die, then for the port to actually be free.
if [ -n "$OLDPID" ]; then
  for _ in $(seq 1 20); do kill -0 "$OLDPID" 2>/dev/null || break; sleep 1; done
fi
for _ in $(seq 1 20); do
  ss -ltn 2>/dev/null | grep -q '127.0.0.1:18675' || break
  sleep 1
done

./start
REMOTE

echo "==> [6/6] Health check: ${HEALTH_URL}"
ok=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  body="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"
  if [ "$body" = "ok" ]; then ok="yes"; break; fi
  echo "  ...waiting for app to come up (attempt $i)"
  sleep 3
done
if [ "$ok" != "yes" ]; then
  echo "DEPLOY FAILED: health check did not return 'ok' from ${HEALTH_URL}" >&2
  echo "uWSGI was cycled. State left AS-IS for diagnosis — NO auto-rollback." >&2
  echo "Check the worker log:  ~/logs/apps/jt_jobs/uwsgi.log" >&2
  echo "Manual rollback (only if you decide to):" >&2
  echo "  ssh ${SSH_TARGET} 'cd ${APP_DIR} && cp uwsgi.ini.bak uwsgi.ini && cp start.bak start && ./stop && ./start'" >&2
  exit 1
fi

echo
echo "ALL SYSTEMS GO — ${HEALTH_URL} returned 'ok'."
