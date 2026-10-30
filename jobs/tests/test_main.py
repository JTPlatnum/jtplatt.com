"""Tests for main.py — the production pipeline entry point.

Mocks each source's fetch_listings to control inputs deterministically.
Uses tmp_path for the SQLite DB and output HTML so tests don't pollute
production state.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Iterator, List
from unittest.mock import MagicMock

import pytest

# Make `import main` resolve against jobs/ root, same as production cron.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import main as main_mod
import store
from crawler.base import Posting
from crawler.sources.calcareers import CalCareersSource
from crawler.sources.csu import CSUSource
from crawler.sources.edjoin import EdJoinSource
from crawler.sources.usajobs import USAJobsSource


def _synthetic_posting(source: str, jid: str, title: str = "Adult Education Teacher") -> Posting:
    return Posting(
        source=source,
        source_job_id=jid,
        title=title,
        employer="Test District",
        url=f"https://example.test/{source}/{jid}",
        raw_text="Adult Education curriculum design and instruction. Telework eligible.",
        classification=None,
        salary_min=9000.0,
        salary_max=11000.0,
        location="Sacramento, California",
        all_locations=["Sacramento, California"],
        telework_flag=True,
        posted_date=date(2026, 6, 1),
    )


@pytest.fixture
def isolated_pipeline(tmp_path: Path, monkeypatch):
    """Isolate main.py from production state.

    - DB → tmp_path/test.db
    - Output → tmp_path/output/index.html
    - Logs → tmp_path/logs
    - SALARY_FLOOR → 0 (let everything pass salary rule)
    - load_dotenv → no-op (don't pick up local .env)
    """
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "output" / "index.html"
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(main_mod, "DB_PATH", db_path)
    monkeypatch.setattr(main_mod, "DEFAULT_OUTPUT_PATH", out_path)
    monkeypatch.setattr(main_mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(main_mod, "LOG_FILE", log_dir / "agent.log")
    monkeypatch.delenv("JOBS_OUTPUT_PATH", raising=False)

    # Suppress any local .env so test behavior doesn't depend on JT's box.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)

    # USAJobsSource validates these at instantiation. Set fake values so the
    # source can be constructed; fetch_listings will be mocked anyway.
    monkeypatch.setenv("USAJOBS_API_KEY", "test-key-not-used")
    monkeypatch.setenv("USAJOBS_USER_AGENT", "test@example.com")

    # Filter.SALARY_FLOOR is set at import time from os.environ; force it to 0
    # so synthetic postings with salary_min=9000 always pass rule 1.
    import filter as filter_mod
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)

    return {"db_path": db_path, "out_path": out_path, "log_dir": log_dir}


def _patch_source_fetch(monkeypatch, postings_per_source: dict, failing: set = frozenset()):
    """Replace each Source class's fetch_listings with a fake that yields
    the supplied postings (or raises if in `failing` set).

    `postings_per_source` keys: 'usajobs', 'calcareers', 'csu', 'edjoin'.
    """
    class_map = {
        "usajobs": USAJobsSource,
        "calcareers": CalCareersSource,
        "csu": CSUSource,
        "edjoin": EdJoinSource,
    }

    for name, cls in class_map.items():
        if name in failing:
            def _fail(*args, _name=name, **kw):
                raise RuntimeError(f"synthetic-{_name}-failure")
            monkeypatch.setattr(cls, "fetch_listings", _fail)
        else:
            postings = postings_per_source.get(name, [])
            def _fake(*args, _postings=postings, **kw) -> Iterator[Posting]:
                yield from _postings
            monkeypatch.setattr(cls, "fetch_listings", _fake)

    # Skip the YAML load inside the EdJoin path of _build_sources — even when
    # we don't fail edjoin, we don't want to depend on sources.yaml.
    real_build = main_mod._build_sources

    def _build_no_yaml():
        return [
            ("usajobs", USAJobsSource(), None),
            ("calcareers", CalCareersSource(), None),
            ("csu", CSUSource(), None),
            ("edjoin", EdJoinSource(), []),  # queries list is irrelevant when fetch is mocked
        ]
    monkeypatch.setattr(main_mod, "_build_sources", _build_no_yaml)


def _count_runs_rows() -> int:
    conn = store._get_conn()
    return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


# --- Test 1: all 4 sources OK -> exit 0, HTML written, runs row recorded ---

def test_pipeline_runs_with_all_sources_mocked(isolated_pipeline, monkeypatch, capsys):
    _patch_source_fetch(monkeypatch, {
        "usajobs": [_synthetic_posting("usajobs", "u1")],
        "calcareers": [_synthetic_posting("calcareers", "c1")],
        "csu": [_synthetic_posting("csu", "s1")],
        "edjoin": [_synthetic_posting("edjoin", "e1")],
    })

    rc = main_mod.main([])

    assert rc == main_mod.EXIT_OK
    assert isolated_pipeline["out_path"].exists(), "HTML output file must be written"
    # Runs row recorded (start_run + end_run both fired)
    assert _count_runs_rows() == 1
    captured = capsys.readouterr()
    assert "Pipeline complete." in captured.out
    assert "4/4 sources OK" in captured.out


# --- Test 2: one source raises mid-fetch -> others run, HTML written -------

def test_pipeline_continues_when_one_source_fails(isolated_pipeline, monkeypatch, capsys):
    _patch_source_fetch(
        monkeypatch,
        postings_per_source={
            "usajobs": [_synthetic_posting("usajobs", "u1")],
            # calcareers fails
            "csu": [_synthetic_posting("csu", "s1")],
            "edjoin": [_synthetic_posting("edjoin", "e1")],
        },
        failing={"calcareers"},
    )

    rc = main_mod.main([])

    assert rc == main_mod.EXIT_OK
    assert isolated_pipeline["out_path"].exists()
    # Status is 'partial' since 1 of 4 failed; both 'success' and 'partial'
    # statuses still record the row.
    assert _count_runs_rows() == 1
    conn = store._get_conn()
    row = conn.execute("SELECT status FROM runs").fetchone()
    assert row["status"] == "partial"
    captured = capsys.readouterr()
    assert "3/4 sources OK" in captured.out
    # Failing source name must appear in log output (stderr/stdout aggregated)
    combined = captured.out + captured.err
    assert "calcareers" in combined
    assert "synthetic-calcareers-failure" in combined


# --- Test 3: --dry-run suppresses HTML + runs row --------------------------

def test_dry_run_writes_no_html_no_runs_row(isolated_pipeline, monkeypatch, capsys):
    _patch_source_fetch(monkeypatch, {
        "usajobs": [_synthetic_posting("usajobs", "u1")],
        "calcareers": [_synthetic_posting("calcareers", "c1")],
        "csu": [_synthetic_posting("csu", "s1")],
        "edjoin": [_synthetic_posting("edjoin", "e1")],
    })

    rc = main_mod.main(["--dry-run"])

    assert rc == main_mod.EXIT_OK
    assert not isolated_pipeline["out_path"].exists(), \
        "--dry-run must not write HTML"
    # init_db ran (the test infrastructure depends on _get_conn working)
    # but no runs row was recorded
    assert _count_runs_rows() == 0, "--dry-run must not insert a runs row"
    captured = capsys.readouterr()
    assert "dry-run" in captured.out
    assert "4/4 sources OK" in captured.out


# --- Test 4: all sources fail -> exit 1, no HTML --------------------------

def test_total_failure_exit_code(isolated_pipeline, monkeypatch, capsys):
    _patch_source_fetch(
        monkeypatch,
        postings_per_source={},
        failing={"usajobs", "calcareers", "csu", "edjoin"},
    )

    rc = main_mod.main([])

    assert rc == main_mod.EXIT_TOTAL_FAILURE
    assert not isolated_pipeline["out_path"].exists(), \
        "Total failure must not write HTML"
    # We DO still insert a runs row at start (start_run was called before
    # fetch loop) and update it to 'error' status — this is intentional
    # diagnostic state, not a render side effect. Verify the status.
    assert _count_runs_rows() == 1
    conn = store._get_conn()
    row = conn.execute("SELECT status FROM runs").fetchone()
    assert row["status"] == "error"
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Pipeline FAILED" in combined
    assert "0/4 sources OK" in combined


# --- Sanity: output path env override --------------------------------------

def test_output_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "site.html"
    monkeypatch.setenv("JOBS_OUTPUT_PATH", str(custom))
    monkeypatch.setattr(main_mod, "REPO_ROOT", tmp_path)  # not used for absolute paths
    assert main_mod._output_path() == custom


def test_output_path_relative_resolves_to_repo_root(monkeypatch):
    monkeypatch.setenv("JOBS_OUTPUT_PATH", "custom/site.html")
    expected = main_mod.REPO_ROOT / "custom" / "site.html"
    assert main_mod._output_path() == expected


def test_output_path_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("JOBS_OUTPUT_PATH", raising=False)
    assert main_mod._output_path() == main_mod.DEFAULT_OUTPUT_PATH


# --- Cross-source dedupe -----------------------------------------------------

def _dup(source, jid, title="Category Manager", employer="StockX", pay=None):
    return (Posting(source=source, source_job_id=jid, title=title, employer=employer,
                    url=f"https://example.test/{source}/{jid}", raw_text="",
                    salary_min=pay, salary_max=pay), {"score": 1})


def test_dedupe_collapses_same_job_across_sources():
    kept = [_dup("greenhouse", "1"), _dup("weworkremotely", "2", title="Category  Manager!")]
    out, n = main_mod.dedupe_cross_source(kept)
    assert n == 1 and [p.source for p, _ in out] == ["greenhouse"]


def test_dedupe_real_pay_wins_over_unverified():
    kept = [_dup("weworkremotely", "2"), _dup("greenhouse", "1", pay=10000.0)]
    out, n = main_mod.dedupe_cross_source(kept)
    assert n == 1 and [p.source for p, _ in out] == ["greenhouse"]


def test_dedupe_tie_keeps_first_seen():
    kept = [_dup("weworkremotely", "2", pay=9000.0), _dup("greenhouse", "1", pay=10000.0)]
    out, _ = main_mod.dedupe_cross_source(kept)
    assert [p.source for p, _ in out] == ["weworkremotely"]


def test_dedupe_keeps_same_source_reqs():
    kept = [_dup("greenhouse", "1"), _dup("greenhouse", "2")]
    out, n = main_mod.dedupe_cross_source(kept)
    assert n == 0 and len(out) == 2


def test_dedupe_ignores_corporate_suffix_and_case():
    kept = [_dup("greenhouse", "1", employer="StockX, Inc."), _dup("remoteok", "2", employer="stockx")]
    assert main_mod.dedupe_cross_source(kept)[1] == 1


def test_dedupe_different_title_or_employer_not_collapsed():
    kept = [_dup("greenhouse", "1"), _dup("remoteok", "2", title="Category Manager - Apparel"),
            _dup("himalayas", "3", employer="GOAT Group")]
    assert main_mod.dedupe_cross_source(kept)[1] == 0
