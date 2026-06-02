"""Tests for store.py — SQLite roundtrip, dedup, email ledger, run lifecycle.

Each test gets a fresh sqlite under tmp_path via the `db` fixture, which calls
init_db(); module-level _conn is swapped per test, so no cross-test bleed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import store
from crawler.base import Posting


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_jobs.db"
    store.init_db(db_path)
    return db_path


def _posting(**overrides) -> Posting:
    defaults = dict(
        source="usajobs",
        source_job_id="P-12345",
        title="Information Technology Specialist",
        employer="Department of Defense",
        url="https://example.test/job/P-12345",
        raw_text="duties and qualifications",
        classification=None,
        salary_min=8000.0,
        salary_max=10000.0,
        location="Sacramento, California",
        all_locations=["Sacramento, California"],
        telework_flag=True,
        posted_date=date(2026, 5, 20),
    )
    defaults.update(overrides)
    return Posting(**defaults)


# --- init_db ---------------------------------------------------------------

def test_init_db_creates_tables(db):
    conn = store._get_conn()
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "emails", "runs"}.issubset(names)


def test_init_db_idempotent(db, tmp_path):
    store.init_db(tmp_path / "test_jobs.db")  # re-call same path
    store.init_db(tmp_path / "test_jobs.db")  # and again
    conn = store._get_conn()
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "emails", "runs"}.issubset(names)


def test_init_db_uninitialized_raises():
    """Calling a store function before init_db raises RuntimeError."""
    store._conn = None  # simulate clean state
    with pytest.raises(RuntimeError, match="init_db"):
        store.start_run()


# --- upsert_job ------------------------------------------------------------

def test_upsert_inserts_new_posting(db):
    job_id, is_new = store.upsert_job(_posting())
    assert isinstance(job_id, int)
    assert is_new is True


def test_upsert_roundtrip_preserves_fields(db):
    p = _posting(
        classification="Information Technology Specialist I",
        all_locations=["Sacramento, California", "Honolulu, Hawaii"],
        telework_flag=False,
    )
    job_id, _ = store.upsert_job(p)
    rows = store.get_active_jobs(seen_within_days=30)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == job_id
    assert r["source"] == "usajobs"
    assert r["source_job_id"] == "P-12345"
    assert r["title"] == "Information Technology Specialist"
    assert r["classification"] == "Information Technology Specialist I"
    assert r["salary_min"] == 8000.0
    assert r["salary_max"] == 10000.0
    assert r["all_locations"] == ["Sacramento, California", "Honolulu, Hawaii"]
    assert r["telework_flag"] is False
    assert r["posted_date"] == date(2026, 5, 20)
    assert r["first_seen_at"] is not None
    assert r["last_seen_at"] is not None


def test_upsert_telework_none_roundtrips_as_none(db):
    """Trinary state — None must survive the int round-trip."""
    job_id, _ = store.upsert_job(_posting(telework_flag=None))
    rows = store.get_active_jobs()
    assert rows[0]["telework_flag"] is None


def test_upsert_same_posting_returns_is_new_false(db):
    p = _posting()
    job_id_1, is_new_1 = store.upsert_job(p)
    job_id_2, is_new_2 = store.upsert_job(p)
    assert job_id_1 == job_id_2
    assert is_new_1 is True
    assert is_new_2 is False


def test_upsert_preserves_first_seen_at_updates_last_seen_at(db):
    job_id, _ = store.upsert_job(_posting())
    conn = store._get_conn()
    # Age the row so we can verify first_seen_at survives.
    old_ts = "2020-01-01T00:00:00"
    conn.execute(
        "UPDATE jobs SET first_seen_at = ?, last_seen_at = ? WHERE id = ?",
        (old_ts, old_ts, job_id),
    )
    conn.commit()
    store.upsert_job(_posting())
    row = conn.execute(
        "SELECT first_seen_at, last_seen_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["first_seen_at"] == old_ts          # preserved
    assert row["last_seen_at"] > old_ts            # refreshed


def test_upsert_updates_mutable_content(db):
    """A source can edit a posting — raw_text / salary update on re-upsert."""
    job_id, _ = store.upsert_job(_posting(raw_text="version 1", salary_min=8000.0))
    job_id_2, is_new = store.upsert_job(
        _posting(raw_text="version 2 — updated", salary_min=8500.0)
    )
    assert job_id == job_id_2
    assert is_new is False
    row = store._get_conn().execute(
        "SELECT raw_text, salary_min FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["raw_text"] == "version 2 — updated"
    assert row["salary_min"] == 8500.0


def test_dedup_distinct_source_jobs_coexist(db):
    """Same source_job_id under different sources is two distinct rows."""
    id1, new1 = store.upsert_job(_posting(source="usajobs", source_job_id="X-1"))
    id2, new2 = store.upsert_job(_posting(source="calcareers", source_job_id="X-1"))
    assert id1 != id2
    assert new1 is True and new2 is True


# --- posted_date fallback --------------------------------------------------

def test_posted_date_fallback_uses_posting_date_when_set(db):
    job_id, _ = store.upsert_job(_posting(posted_date=date(2026, 5, 20)))
    assert store.get_posted_date_fallback(job_id) == date(2026, 5, 20)


def test_posted_date_fallback_uses_first_seen_when_missing(db):
    """CalCareers postings have posted_date=None; first_seen_at proxies."""
    job_id, _ = store.upsert_job(_posting(posted_date=None))
    expected = _utc_naive_now().date()
    assert store.get_posted_date_fallback(job_id) == expected


def test_posted_date_fallback_raises_for_missing_id(db):
    with pytest.raises(KeyError):
        store.get_posted_date_fallback(99999)


# --- Email ledger ----------------------------------------------------------

def test_has_been_emailed_false_initially(db):
    job_id, _ = store.upsert_job(_posting())
    assert store.has_been_emailed(job_id) is False


def test_record_email_then_has_been_emailed_true(db):
    job_id, _ = store.upsert_job(_posting())
    store.record_email(job_id, tier1=85, tier2=8.5, address="JTP3000@hotmail.com")
    assert store.has_been_emailed(job_id) is True


def test_record_email_allows_null_tier2(db):
    """A posting can be emailed on Tier 1 alone (Tier 2 not always computed)."""
    job_id, _ = store.upsert_job(_posting())
    store.record_email(job_id, tier1=85, tier2=None, address="JTP3000@hotmail.com")
    row = store._get_conn().execute(
        "SELECT tier1_score, tier2_score FROM emails WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert row["tier1_score"] == 85
    assert row["tier2_score"] is None


# --- Run lifecycle ---------------------------------------------------------

def test_start_run_returns_id(db):
    run_id = store.start_run()
    assert isinstance(run_id, int)
    assert run_id > 0


def test_end_run_updates_row(db):
    run_id = store.start_run()
    store.end_run(
        run_id,
        stats={"fetched": 47, "filtered": 4, "scored": 43},
        status="success",
    )
    row = store._get_conn().execute(
        "SELECT * FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "success"
    assert row["fetched"] == 47
    assert row["filtered"] == 4
    assert row["scored"] == 43
    assert row["ended_at"] is not None


def test_start_run_yields_distinct_ids(db):
    id1 = store.start_run()
    id2 = store.start_run()
    assert id1 != id2


# --- get_active_jobs -------------------------------------------------------

def test_get_active_jobs_returns_within_window(db):
    store.upsert_job(_posting())
    assert len(store.get_active_jobs(seen_within_days=14)) == 1


def test_get_active_jobs_excludes_stale(db):
    """A posting whose last_seen_at is outside the window must be excluded."""
    job_id, _ = store.upsert_job(_posting())
    conn = store._get_conn()
    conn.execute(
        "UPDATE jobs SET last_seen_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00", job_id),
    )
    conn.commit()
    assert store.get_active_jobs(seen_within_days=14) == []


def test_get_active_jobs_orders_by_last_seen_desc(db):
    id1, _ = store.upsert_job(_posting(source="usajobs", source_job_id="P-1"))
    id2, _ = store.upsert_job(_posting(source="usajobs", source_job_id="P-2"))
    # Force p1 to look 1 minute older — still inside the window, but older than p2.
    older = (_utc_naive_now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn = store._get_conn()
    conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (older, id1))
    conn.commit()
    rows = store.get_active_jobs(seen_within_days=14)
    assert [r["id"] for r in rows] == [id2, id1]


def test_get_active_jobs_includes_bookkeeping_columns(db):
    store.upsert_job(_posting())
    r = store.get_active_jobs()[0]
    assert "id" in r
    assert "first_seen_at" in r
    assert "last_seen_at" in r
