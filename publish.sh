#!/usr/bin/env bash
#
# publish.sh — Mac → server data sync for the jobs agent.
# Run from anywhere (paths resolve relative to this script, which lives at the
# repo root):   ./publish.sh
#
# The crawl runs LOCALLY on the Mac (main.py — all 4 sources, incl. the
# Playwright ones CentOS 7 can't run). This pushes the two artifacts the live
# server actually serves — the rendered page and the SQLite DB — to the
# Opalstack app. No server restart: server.py reads JOBS_OUTPUT_PATH fresh on
# every request.
#
# Idempotent: checksum rsync (-c) means a re-run with no crawl change transfers
# zero bytes. Prerequisite: deploy.sh has run at least once (creates the remote
# output/ and data/ dirs).

set -euo pipefail

# --- Config ----------------------------------------------------------------
SSH_USER="jtplatnum"
SSH_HOST="vps233.opalstack.com"
APP_DIR="/home/jtplatnum/apps/jt_jobs"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"
PROBE_URL="https://jobs.jtplatt.com/"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_HTML="${REPO_ROOT}/jobs/output/index.html"
LOCAL_DB="${REPO_ROOT}/jobs/data/jobs.db"
ENV_FILE="${REPO_ROOT}/jobs/.env"

fail() { echo "PUBLISH FAILED: $*" >&2; exit 1; }

# --- Preflight: both artifacts must exist locally --------------------------
[ -f "$LOCAL_HTML" ] || fail "missing $LOCAL_HTML — run main.py on the Mac first"
[ -f "$LOCAL_DB" ]   || fail "missing $LOCAL_DB — run main.py on the Mac first"

# --- Transfer (checksum mode; no --delete) ---------------------------------
# -c: compare by checksum, not mtime/size — avoids spurious re-uploads and makes
# a no-change re-run transfer 0 bytes. --partial: resume a dropped transfer.
echo "==> [1/2] rsync index.html + jobs.db -> ${SSH_TARGET}:${APP_DIR}/"
rsync -avz --partial -c "$LOCAL_HTML" "${SSH_TARGET}:${APP_DIR}/output/index.html" \
  || fail "rsync of index.html failed"
rsync -avz --partial -c "$LOCAL_DB"   "${SSH_TARGET}:${APP_DIR}/data/jobs.db" \
  || fail "rsync of jobs.db failed"

# --- Verify auth + that the published page is served -----------------------
# Probe / (auth-gated) with the basic-auth creds from the local .env. This
# exercises the full path: auth flow works, server.py serves the file, and the
# rsync actually landed. Status-code check ONLY — we never inspect the body, so
# publish.sh stays decoupled from the HTML (which changes every run).
echo "==> [2/2] Auth + serve check: ${PROBE_URL}"
[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE — need ADMIN_USER/ADMIN_PASSWORD for the probe"

# Source the env only inside a subshell so the creds never persist in this shell.
# -sS without -f: -f makes curl abort on a non-2xx status before -w can report
# the code; without it we always capture the actual code and enforce 200 here.
code="$(
  set -a; . "$ENV_FILE"; set +a
  : "${ADMIN_USER:?ADMIN_USER not set in $ENV_FILE}"
  : "${ADMIN_PASSWORD:?ADMIN_PASSWORD not set in $ENV_FILE}"
  curl -sS -o /dev/null -w "%{http_code}" --max-time 15 \
    -u "${ADMIN_USER}:${ADMIN_PASSWORD}" "$PROBE_URL"
)" || fail "probe request to ${PROBE_URL} failed (connection/curl error)"
[ "$code" = "200" ] || fail "expected HTTP 200 from ${PROBE_URL}, got ${code}"

echo
echo "PUBLISHED — ${PROBE_URL} returned 200 (auth + page serve OK)"
