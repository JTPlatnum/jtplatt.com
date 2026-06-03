"""Tests for server.py — Flask app, basic auth, route gating.

server.py validates ADMIN_USER/ADMIN_PASSWORD at module import time. To
make the test module importable we set defaults at the top before any
`import server`, then individual tests override via monkeypatch.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys
from pathlib import Path

import pytest

# server.py raises at import if creds are missing; set defaults BEFORE any
# import of server. Individual tests can override.
os.environ.setdefault("ADMIN_USER", "testuser")
os.environ.setdefault("ADMIN_PASSWORD", "testpass")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402  — must come after env setup


TEST_USER = "testuser"
TEST_PASS = "testpass"
TEST_HTML = "<html><body><h1>TEST CONTENT</h1></body></html>"


def _basic_auth(user: str, pw: str) -> dict:
    token = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def output_file(tmp_path: Path, monkeypatch) -> Path:
    """tmp output file with known HTML; JOBS_OUTPUT_PATH points at it."""
    p = tmp_path / "index.html"
    p.write_text(TEST_HTML)
    monkeypatch.setenv("JOBS_OUTPUT_PATH", str(p))
    return p


@pytest.fixture
def client(output_file):
    """Flask test client with credentials matching TEST_USER / TEST_PASS."""
    return server.app.test_client()


@pytest.fixture
def static_dir(tmp_path: Path, monkeypatch) -> Path:
    """tmp static dir with known styles.css; STATIC_DIR points at it."""
    d = tmp_path / "static"
    d.mkdir()
    (d / "styles.css").write_text("body { color: red; }\n")
    monkeypatch.setattr(server, "STATIC_DIR", d)
    return d


# --- Index --------------------------------------------------------------

def test_index_requires_auth(client):
    resp = client.get("/")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == 'Basic realm="Jobs Agent"'


def test_index_with_invalid_user_returns_401(client):
    resp = client.get("/", headers=_basic_auth("wronguser", TEST_PASS))
    assert resp.status_code == 401


def test_index_with_invalid_password_returns_401(client):
    resp = client.get("/", headers=_basic_auth(TEST_USER, "wrongpass"))
    assert resp.status_code == 401


def test_index_with_valid_auth_returns_html(client, output_file):
    resp = client.get("/", headers=_basic_auth(TEST_USER, TEST_PASS))
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "TEST CONTENT" in body
    assert body == TEST_HTML


def test_index_when_output_missing_returns_placeholder(client, tmp_path, monkeypatch):
    """If JOBS_OUTPUT_PATH points to a nonexistent file, return 200 with
    the friendly placeholder copy."""
    missing = tmp_path / "does_not_exist.html"
    monkeypatch.setenv("JOBS_OUTPUT_PATH", str(missing))
    resp = client.get("/", headers=_basic_auth(TEST_USER, TEST_PASS))
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "hasn't completed its first run yet" in body


# --- Static -------------------------------------------------------------

def test_static_css_requires_auth(client, static_dir):
    resp = client.get("/static/styles.css")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == 'Basic realm="Jobs Agent"'


def test_static_css_with_auth_returns_css(client, static_dir):
    resp = client.get("/static/styles.css", headers=_basic_auth(TEST_USER, TEST_PASS))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "body { color: red; }" in body


def test_static_unknown_file_returns_404(client, static_dir):
    resp = client.get("/static/nope.css", headers=_basic_auth(TEST_USER, TEST_PASS))
    assert resp.status_code == 404


# --- Health -------------------------------------------------------------

def test_health_no_auth_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "ok"
    assert resp.mimetype == "text/plain"


def test_health_ignores_auth_header_when_present(client):
    """Even with a bad Authorization header, /health returns 200 — no
    surprise 401 on the uptime probe path."""
    resp = client.get("/health", headers=_basic_auth("anyone", "wrong"))
    assert resp.status_code == 200


# --- Module-load validation ---------------------------------------------

def test_module_load_fails_without_credentials(monkeypatch):
    """Confirm server.py raises at import time if ADMIN_USER or
    ADMIN_PASSWORD is missing. Restores the module afterward so other
    tests still have a working `server`.
    """
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    try:
        with pytest.raises(RuntimeError) as ei:
            importlib.reload(server)
        assert "ADMIN_USER" in str(ei.value)
        assert "ADMIN_PASSWORD" in str(ei.value)
    finally:
        # Re-establish credentials and reload so subsequent tests have a
        # working module. monkeypatch will revert env at teardown.
        os.environ["ADMIN_USER"] = TEST_USER
        os.environ["ADMIN_PASSWORD"] = TEST_PASS
        importlib.reload(server)


def test_module_load_fails_with_empty_credentials(monkeypatch):
    """Empty-string env values are treated as missing (whitespace-stripped)."""
    monkeypatch.setenv("ADMIN_USER", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    try:
        with pytest.raises(RuntimeError):
            importlib.reload(server)
    finally:
        os.environ["ADMIN_USER"] = TEST_USER
        os.environ["ADMIN_PASSWORD"] = TEST_PASS
        importlib.reload(server)


def test_module_load_fails_with_only_user_set(monkeypatch):
    """ADMIN_USER set but ADMIN_PASSWORD missing → refuse to start."""
    monkeypatch.setenv("ADMIN_USER", "someone")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    try:
        with pytest.raises(RuntimeError):
            importlib.reload(server)
    finally:
        os.environ["ADMIN_USER"] = TEST_USER
        os.environ["ADMIN_PASSWORD"] = TEST_PASS
        importlib.reload(server)


# --- Output-path resolution ---------------------------------------------

def test_output_path_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("JOBS_OUTPUT_PATH", raising=False)
    assert server._resolve_output_path() == server.DEFAULT_OUTPUT_PATH


def test_output_path_absolute_env_honored(monkeypatch, tmp_path):
    custom = tmp_path / "site.html"
    monkeypatch.setenv("JOBS_OUTPUT_PATH", str(custom))
    assert server._resolve_output_path() == custom


def test_output_path_relative_resolves_to_repo_root(monkeypatch):
    monkeypatch.setenv("JOBS_OUTPUT_PATH", "custom/site.html")
    assert server._resolve_output_path() == server.REPO_ROOT / "custom" / "site.html"
