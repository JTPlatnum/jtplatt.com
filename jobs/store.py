"""SQLite I/O. See SPEC.md.

Module-level connection state. Open once per process via init_db(path);
subsequent calls swap to a new database (closing the prior conn). Tests
exploit this by re-init'ing under tmp_path.

Schema v1 — three tables:
    jobs    one row per (source, source_job_id). UPSERT on conflict; first_seen_at
            preserved, last_seen_at refreshed.
    emails  ledger of sent emails — has_been_emailed gates re-sends.
    runs    diagnostic per-pipeline-execution log.

Timestamps are ISO 8601 UTC-naive (datetime.utcnow().isoformat(timespec='seconds')).
all_locations stored as JSON TEXT. telework_flag stored as INTEGER 0/1/NULL to
preserve the trinary state from Posting.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from crawler.base import Posting

log = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    source_job_id   TEXT NOT NULL,
    title           TEXT NOT NULL,
    employer        TEXT NOT NULL,
    url             TEXT NOT NULL,
    raw_text        TEXT NOT NULL,
    classification  TEXT,
    salary_min      REAL,
    salary_max      REAL,
    location        TEXT,
    all_locations   TEXT,
    telework_flag   INTEGER,
    posted_date     TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    UNIQUE(source, source_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);

CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    sent_at         TEXT NOT NULL,
    tier1_score     INTEGER NOT NULL,
    tier2_score     REAL,
    address         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_emails_job_id ON emails(job_id);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    status          TEXT,
    fetched         INTEGER,
    filtered        INTEGER,
    scored          INTEGER
);
"""


# --- Connection management -------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError(
            "store: init_db(path) must be called before any other store function"
        )
    return _conn


def _utc_now() -> datetime:
    # Drop tzinfo so isoformat() emits "2026-06-02T15:30:00" — the naive
    # ISO 8601 shape the schema, tests, and active-window comparisons all
    # assume. utcnow() is deprecated in 3.12.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now() -> str:
    return _utc_now().isoformat(timespec="seconds")


def init_db(path: Path) -> sqlite3.Connection:
    """Open (or reopen) the SQLite database at path and create tables if missing.
    Idempotent — safe to call repeatedly. Closes any prior global connection."""
    global _conn
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    _conn = conn
    return conn


# --- Runs ------------------------------------------------------------------

def start_run() -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)",
        (_now(),),
    )
    conn.commit()
    return cur.lastrowid


def end_run(run_id: int, stats: Dict[str, Any], status: str) -> None:
    conn = _get_conn()
    conn.execute(
        """
        UPDATE runs
        SET ended_at = ?, status = ?, fetched = ?, filtered = ?, scored = ?
        WHERE id = ?
        """,
        (
            _now(),
            status,
            stats.get("fetched"),
            stats.get("filtered"),
            stats.get("scored"),
            run_id,
        ),
    )
    conn.commit()


# --- Jobs ------------------------------------------------------------------

def _bool_to_int(b: Optional[bool]) -> Optional[int]:
    if b is None:
        return None
    return 1 if b else 0


def _int_to_bool(v: Optional[int]) -> Optional[bool]:
    if v is None:
        return None
    return bool(v)


def _encode_locations(locs: Optional[List[str]]) -> Optional[str]:
    return json.dumps(locs) if locs is not None else None


def _encode_posted_date(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def upsert_job(posting: Posting) -> Tuple[int, bool]:
    """Insert or update by (source, source_job_id). Returns (job_id, is_new).

    On update, first_seen_at is preserved; last_seen_at is refreshed; all
    mutable content fields are overwritten so source edits propagate.
    """
    conn = _get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT id FROM jobs WHERE source = ? AND source_job_id = ?",
        (posting.source, posting.source_job_id),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO jobs (
                source, source_job_id, title, employer, url, raw_text,
                classification, salary_min, salary_max, location,
                all_locations, telework_flag, posted_date,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                posting.source, posting.source_job_id, posting.title,
                posting.employer, posting.url, posting.raw_text,
                posting.classification, posting.salary_min, posting.salary_max,
                posting.location, _encode_locations(posting.all_locations),
                _bool_to_int(posting.telework_flag),
                _encode_posted_date(posting.posted_date),
                now, now,
            ),
        )
        conn.commit()
        return cur.lastrowid, True

    job_id = existing["id"]
    conn.execute(
        """
        UPDATE jobs SET
            title = ?, employer = ?, url = ?, raw_text = ?,
            classification = ?, salary_min = ?, salary_max = ?,
            location = ?, all_locations = ?, telework_flag = ?,
            posted_date = ?, last_seen_at = ?
        WHERE id = ?
        """,
        (
            posting.title, posting.employer, posting.url, posting.raw_text,
            posting.classification, posting.salary_min, posting.salary_max,
            posting.location, _encode_locations(posting.all_locations),
            _bool_to_int(posting.telework_flag),
            _encode_posted_date(posting.posted_date),
            now,
            job_id,
        ),
    )
    conn.commit()
    return job_id, False


def get_posted_date_fallback(job_id: int) -> date:
    """Return posted_date if set, else first_seen_at as a date. CalCareers
    doesn't ship posted_date — first_seen_at proxies it after run 1."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT posted_date, first_seen_at FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no job with id={job_id}")
    if row["posted_date"]:
        return date.fromisoformat(row["posted_date"])
    return date.fromisoformat(row["first_seen_at"][:10])


# --- Emails ----------------------------------------------------------------

def has_been_emailed(job_id: int) -> bool:
    conn = _get_conn()
    return conn.execute(
        "SELECT 1 FROM emails WHERE job_id = ? LIMIT 1",
        (job_id,),
    ).fetchone() is not None


def record_email(
    job_id: int,
    tier1: int,
    tier2: Optional[float],
    address: str,
) -> None:
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO emails (job_id, sent_at, tier1_score, tier2_score, address)
        VALUES (?, ?, ?, ?, ?)
        """,
        (job_id, _now(), tier1, tier2, address),
    )
    conn.commit()


# --- Active jobs -----------------------------------------------------------

def get_active_jobs(seen_within_days: int = 14) -> List[Dict[str, Any]]:
    """Return jobs whose last_seen_at falls within the trailing window.

    Returns list of dicts with Posting fields plus bookkeeping columns
    (id, first_seen_at, last_seen_at). all_locations decoded from JSON;
    telework_flag back to bool/None; posted_date parsed to date.
    """
    conn = _get_conn()
    cutoff = (
        _utc_now() - timedelta(days=seen_within_days)
    ).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT id, source, source_job_id, title, employer, url, raw_text,
               classification, salary_min, salary_max, location, all_locations,
               telework_flag, posted_date, first_seen_at, last_seen_at
        FROM jobs
        WHERE last_seen_at >= ?
        ORDER BY last_seen_at DESC
        """,
        (cutoff,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        if d["all_locations"]:
            d["all_locations"] = json.loads(d["all_locations"])
        d["telework_flag"] = _int_to_bool(d["telework_flag"])
        if d["posted_date"]:
            d["posted_date"] = date.fromisoformat(d["posted_date"])
        out.append(d)
    return out
