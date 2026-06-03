"""Flask app for jobs.jtplatt.com.

Serves the rendered results page (written by `main.py` to JOBS_OUTPUT_PATH)
behind HTTP Basic auth. Auth credentials come from env (ADMIN_USER /
ADMIN_PASSWORD); the module refuses to import if either is missing or
empty — no accidental no-password deployments.

Routes:
    GET /                        auth-gated; serves rendered HTML
    GET /static/<path:filename>  auth-gated; serves CSS/assets
    GET /health                  no auth; "ok" + 200 for uptime probes

WSGI entry point: `server:app`. Local dev: `python server.py` runs on
127.0.0.1 at PORT (default 8000).
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
from functools import wraps
from pathlib import Path
from typing import Tuple

from flask import Flask, Response, g, request, send_from_directory

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = REPO_ROOT / "output" / "index.html"
STATIC_DIR = REPO_ROOT / "static"

PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>jobs.jtplatt.com</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header class="page-header">
    <h1>jobs.jtplatt.com</h1>
    <p class="meta">Awaiting first run.</p>
  </header>
  <section class="bucket">
    <p class="empty">Job agent hasn't completed its first run yet. Check back after the next scheduled run.</p>
  </section>
</body>
</html>
"""


# --- Credentials (module-level — refuses import without them) -------------

def _load_credentials() -> Tuple[str, str]:
    user = os.environ.get("ADMIN_USER", "").strip()
    pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not user or not pw:
        raise RuntimeError(
            "server: ADMIN_USER and ADMIN_PASSWORD must both be set and "
            "non-empty in the environment before the app can start. "
            "Refusing to run a no-password server."
        )
    return user, pw


ADMIN_USER, ADMIN_PASSWORD = _load_credentials()


# --- Flask app -------------------------------------------------------------

# static_folder=None disables Flask's automatic (un-auth-gated) /static
# route. We register our own auth-gated /static/<path> below.
app = Flask(__name__, static_folder=None)


def _resolve_output_path() -> Path:
    raw = os.environ.get("JOBS_OUTPUT_PATH")
    if not raw:
        return DEFAULT_OUTPUT_PATH
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _check_auth(user: str, pw: str) -> bool:
    """Constant-time comparison on both fields. Encodes to bytes because
    compare_digest is strict about same-type inputs."""
    user_ok = secrets.compare_digest(
        user.encode("utf-8"), ADMIN_USER.encode("utf-8")
    )
    pw_ok = secrets.compare_digest(
        pw.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
    )
    return user_ok and pw_ok


def _unauthorized() -> Response:
    return Response(
        "unauthorized\n",
        status=401,
        mimetype="text/plain",
        headers={"WWW-Authenticate": 'Basic realm="Jobs Agent"'},
    )


def requires_auth(view):
    """Decorator — gate a view on HTTP Basic auth. Sets `g.auth_status` so
    the after_request hook can log pass/fail uniformly."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if (
            auth is None
            or not _check_auth(auth.username or "", auth.password or "")
        ):
            g.auth_status = "fail"
            return _unauthorized()
        g.auth_status = "ok"
        return view(*args, **kwargs)
    return wrapper


# --- Routes ---------------------------------------------------------------

@app.route("/")
@requires_auth
def index() -> Response:
    """Serve the most recent rendered HTML. Returns the placeholder if the
    cron hasn't written an output file yet."""
    output = _resolve_output_path()
    if not output.exists():
        return Response(PLACEHOLDER_HTML, mimetype="text/html", status=200)
    # Read fresh on each request — no caching. The file is small and
    # access is human-paced.
    return Response(
        output.read_text(encoding="utf-8"), mimetype="text/html", status=200
    )


@app.route("/static/<path:filename>")
@requires_auth
def static_files(filename: str) -> Response:
    """Serve static assets (CSS) from STATIC_DIR. Path-traversal protection
    is handled by Flask's send_from_directory."""
    return send_from_directory(STATIC_DIR, filename)


@app.route("/health")
def health() -> Response:
    """Uptime / Opalstack health probe. Plaintext, no auth."""
    g.auth_status = "skip"
    return Response("ok", mimetype="text/plain", status=200)


# --- Request logging (uniform across routes) ------------------------------

@app.after_request
def _log_request(response: Response) -> Response:
    auth_status = getattr(g, "auth_status", "n/a")
    size = response.content_length
    size_str = str(size) if size is not None else "?"
    app.logger.info(
        "%s %s %d auth=%s size=%s",
        request.method, request.path, response.status_code,
        auth_status, size_str,
    )
    return response


# --- Logger init ----------------------------------------------------------

def _configure_logging() -> None:
    """Ensure INFO-level logging is on for app.logger when imported by any
    WSGI server. Idempotent — safe to call repeatedly."""
    if app.logger.handlers:
        # Whatever's already attached (Gunicorn etc.) wins.
        app.logger.setLevel(logging.INFO)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


_configure_logging()


# --- Local dev entry point ------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    # debug=False so reloader doesn't double-init the module.
    app.run(host="127.0.0.1", port=port, debug=False)
