"""Production pipeline entry point. Cron calls this daily.

Pipeline order (per SPEC.md):
    1. Load .env + sources.yaml
    2. For each source: fetch, upsert into store. Per-source failures are
       isolated — one bad source does not kill the run.
    3. Pull active-window jobs from store (14-day default).
    4. Apply hard filters.
    5. Score survivors (Tier 1).
    6. Render HTML to JOBS_OUTPUT_PATH (default ./output/index.html).
    7. Record the runs row.

Exit codes:
    0  — pipeline complete (full or partial success)
    1  — total failure (config load failed OR all sources failed)
    2  — render failure after sources succeeded

CLI:
    python main.py              full pipeline
    python main.py --dry-run    fetch + filter + score; no HTML, no runs row
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "jobs.db"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "output" / "index.html"
LOG_DIR = REPO_ROOT / "logs"
LOG_FILE = LOG_DIR / "agent.log"

EXIT_OK = 0
EXIT_TOTAL_FAILURE = 1
EXIT_RENDER_FAILURE = 2


# --- Logging ---------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    """stdout + rotating file. Format includes timestamp, level, source/operation."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: List[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
        ),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    return logging.getLogger("main")


# --- Source configuration --------------------------------------------------

def _build_sources() -> List[Tuple[str, Any, Optional[List[Dict[str, Any]]]]]:
    """Build the source roster. Returns [(name, instance, queries_or_None), ...].

    EdJoinSource.fetch_listings(queries) takes its queries as an argument
    (5-lane design per locked recon §11.2); the others read internally from
    sources.yaml.
    """
    import yaml  # noqa: PLC0415

    from crawler.sources.calcareers import CalCareersSource  # noqa: PLC0415
    from crawler.sources.csu import CSUSource  # noqa: PLC0415
    from crawler.sources.edjoin import EdJoinSource  # noqa: PLC0415
    from crawler.sources.usajobs import USAJobsSource  # noqa: PLC0415

    sources_cfg_path = REPO_ROOT / "data" / "sources.yaml"
    if not sources_cfg_path.exists():
        raise FileNotFoundError(f"sources.yaml not found at {sources_cfg_path}")
    sources_cfg = yaml.safe_load(sources_cfg_path.read_text())

    return [
        ("usajobs", USAJobsSource(), None),
        ("calcareers", CalCareersSource(), None),
        ("csu", CSUSource(), None),
        ("edjoin", EdJoinSource(), sources_cfg["edjoin"]["queries"]),
    ]


# --- Fetch ----------------------------------------------------------------

def _fetch_all_sources(
    sources: List[Tuple[str, Any, Optional[List[Dict[str, Any]]]]],
    dry_run: bool,
    log: logging.Logger,
) -> Tuple[List[str], List[str], int, int]:
    """Run each source, upsert into store. Per-source failures isolated.

    Returns (succeeded, failed, new_count, re_seen_count). In --dry-run
    mode no upsert occurs and every yielded posting counts as 'new' for
    summary purposes.
    """
    import store  # noqa: PLC0415

    succeeded: List[str] = []
    failed: List[str] = []
    new_count = 0
    re_seen_count = 0

    for name, src, queries in sources:
        try:
            log.info("fetch.%s: starting", name)
            iterator = (
                src.fetch_listings(queries) if queries is not None
                else src.fetch_listings()
            )
            count = 0
            for posting in iterator:
                if dry_run:
                    new_count += 1
                else:
                    _job_id, is_new = store.upsert_job(posting)
                    if is_new:
                        new_count += 1
                    else:
                        re_seen_count += 1
                count += 1
            succeeded.append(name)
            log.info("fetch.%s: complete, %d postings", name, count)
        except Exception as e:
            log.exception("fetch.%s: failed: %s", name, e)
            failed.append(name)

    return succeeded, failed, new_count, re_seen_count


# --- Filter + score -------------------------------------------------------

def _row_to_posting(row: Dict[str, Any]):
    """Reconstruct a Posting from a get_active_jobs dict row. Drops the
    bookkeeping columns (id, first_seen_at, last_seen_at)."""
    from crawler.base import Posting  # noqa: PLC0415
    return Posting(
        source=row["source"],
        source_job_id=row["source_job_id"],
        title=row["title"],
        employer=row["employer"],
        url=row["url"],
        raw_text=row["raw_text"],
        classification=row.get("classification"),
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        location=row.get("location"),
        all_locations=row.get("all_locations"),
        telework_flag=row.get("telework_flag"),
        posted_date=row.get("posted_date"),
    )


def _filter_and_score(
    log: logging.Logger,
) -> Tuple[List[Tuple[Any, Dict[str, Any]]], Dict[str, int], int]:
    """Pull active jobs, filter, score survivors.

    Returns (kept_with_scores, rejected_reasons, total_active).
    kept_with_scores: list of (Posting, score_result_dict).
    """
    import filter as filter_mod  # noqa: PLC0415
    import store  # noqa: PLC0415
    from score import score_posting  # noqa: PLC0415

    active_rows = store.get_active_jobs(seen_within_days=14)
    log.info("filter: %d jobs in active window", len(active_rows))

    kept: List[Tuple[Any, Dict[str, Any]]] = []
    rejected_reasons: Counter = Counter()
    for row in active_rows:
        posting = _row_to_posting(row)
        ok, reason = filter_mod.should_keep(posting)
        if ok:
            kept.append((posting, score_posting(posting)))
        else:
            rejected_reasons[reason] += 1

    log.info(
        "filter: kept=%d rejected=%d (floor=%s)",
        len(kept), sum(rejected_reasons.values()), filter_mod.SALARY_FLOOR,
    )
    for reason, n in rejected_reasons.most_common():
        log.info("filter.reject: %d × %s", n, reason)

    return kept, dict(rejected_reasons), len(active_rows)


# --- Render ----------------------------------------------------------------

def _posting_to_row(posting, result: Dict[str, Any]):
    """Convert a (Posting, score-result) pair to a render.RowView."""
    from render import RowView, WhyPanel  # noqa: PLC0415
    from score import _has_overseas_location  # noqa: PLC0415

    score = result["score"]
    comps = result["components"]
    annual_min = posting.salary_min * 12 if posting.salary_min is not None else None
    annual_max = posting.salary_max * 12 if posting.salary_max is not None else None

    breakdown = (
        f"T1 {score} = kw {comps['keyword_match']['points']}/40 + "
        f"title {comps['title_match']['points']:+d}/±25 + "
        f"flex {comps['flexibility']['points']}/10 + "
        f"person {comps['person_facing']['points']}/10 + "
        f"intl {comps['international']['points']}/10 + "
        f"recent {comps['recency']['points']}/5"
    )
    matched = comps["keyword_match"]["matched_keywords"]
    title_match = comps["title_match"]
    title_phrase = (
        f"YES: {title_match['matched']}" if title_match["list"] == "yes"
        else f"NO: {title_match['matched']}" if title_match["list"] == "no"
        else "neutral"
    )
    why = WhyPanel(
        rationale=f"[Narrative pending Tier 2 LLM.] {breakdown}. Title rule: {title_phrase}.",
        matched_from_background=(
            matched if matched else ["[no inventory keywords matched in raw_text]"]
        ),
        gaps=["[Gaps pending Tier 2 LLM analysis]"],
    )

    return RowView(
        source=posting.source,
        source_job_id=posting.source_job_id,
        url=posting.url,
        title=posting.title,
        employer=posting.employer,
        grade=None,
        salary_monthly_min=posting.salary_min,
        salary_monthly_max=posting.salary_max,
        salary_annual_min=annual_min,
        salary_annual_max=annual_max,
        location_display=posting.location or "—",
        all_locations=posting.all_locations,
        telework=bool(posting.telework_flag),
        is_overseas=_has_overseas_location(posting.all_locations),
        posted_date=posting.posted_date,
        tier1_score=score,
        tier2_score=None,
        emailed=False,
        why=why,
    )


def _render_html(
    kept: List[Tuple[Any, Dict[str, Any]]],
    output_path: Path,
    sources_succeeded: List[str],
    total_scanned: int,
    total_rejected_filter: int,
    log: logging.Logger,
) -> None:
    """Render the results page. Below-threshold (T1<40) survivors count
    toward 'rejected' on the page footer, matching render_demo's convention.
    """
    from render import PageStats, render_page  # noqa: PLC0415

    rows = [_posting_to_row(p, r) for p, r in kept]
    below_threshold = sum(1 for _p, r in kept if r["score"] < 40)
    surfaced = len(rows) - below_threshold

    stats = PageStats(
        last_run_at=datetime.now(),
        sources=[s.title() if s != "usajobs" else "USAJobs" for s in sources_succeeded],
        scanned=total_scanned,
        surfaced=surfaced,
        emailed_count=0,
        rejected=total_rejected_filter + below_threshold,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_page(rows, stats, output_path)
    log.info(
        "render: wrote %d rows (surfaced=%d below_threshold=%d) to %s",
        len(rows), surfaced, below_threshold, output_path,
    )


# --- Run row finalization --------------------------------------------------

def _record_run(
    run_id: int,
    sources_succeeded: List[str],
    sources_failed: List[str],
    new_count: int,
    re_seen_count: int,
    total_kept: int,
    total_rejected: int,
    top_score: int,
    status: str,
    log: logging.Logger,
) -> None:
    """Update the runs row. The store schema's three numeric columns
    (fetched/filtered/scored) hold the headline stats; per-source success
    lists and top_score live in the log file."""
    import store  # noqa: PLC0415
    store.end_run(
        run_id,
        stats={
            "fetched": new_count + re_seen_count,
            "filtered": total_rejected,
            "scored": total_kept,
        },
        status=status,
    )
    log.info(
        "run.end: status=%s sources_ok=%s sources_fail=%s "
        "new=%d re_seen=%d kept=%d rejected=%d top_score=%d",
        status, sources_succeeded, sources_failed,
        new_count, re_seen_count, total_kept, total_rejected, top_score,
    )


# --- Main ------------------------------------------------------------------

def _output_path() -> Path:
    """Resolve output path from env, defaulting to repo/output/index.html.
    Relative paths resolve against the project root, not CWD."""
    raw = os.environ.get("JOBS_OUTPUT_PATH")
    if not raw:
        return DEFAULT_OUTPUT_PATH
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Job-search agent pipeline entry point."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=("Run fetch + filter + score without writing HTML or recording "
              "a runs row. For local verification without polluting state."),
    )
    args = parser.parse_args(argv)

    # dotenv is best-effort — production cron sets env directly.
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    log = _setup_logging()
    log.info("pipeline.start dry_run=%s", args.dry_run)

    # Config load — total failure if this fails.
    try:
        sources = _build_sources()
    except Exception as e:
        log.exception("config: failed to build sources: %s", e)
        print(f"Pipeline FAILED at config load: {e}", file=sys.stderr)
        return EXIT_TOTAL_FAILURE

    # Store init + runs row (skipped on dry-run).
    import store  # noqa: PLC0415
    store.init_db(DB_PATH)
    run_id: Optional[int] = None
    if not args.dry_run:
        run_id = store.start_run()
        log.info("run.start id=%d db=%s", run_id, DB_PATH)

    # Fetch — per-source failures isolated.
    succeeded, failed, new_count, re_seen_count = _fetch_all_sources(
        sources, args.dry_run, log
    )

    if not succeeded:
        log.error("pipeline: ALL %d sources failed", len(sources))
        if run_id is not None:
            _record_run(run_id, succeeded, failed, new_count, re_seen_count,
                        0, 0, 0, "error", log)
        print(
            f"Pipeline FAILED. 0/{len(sources)} sources OK "
            f"(failed: {', '.join(failed)}).",
            file=sys.stderr,
        )
        return EXIT_TOTAL_FAILURE

    # Filter + score.
    kept, rejected_reasons, total_active = _filter_and_score(log)
    total_kept = len(kept)
    total_rejected_filter = sum(rejected_reasons.values())
    top_score = max((r["score"] for _p, r in kept), default=0)

    if args.dry_run:
        print(
            f"Pipeline complete (dry-run). {len(succeeded)}/{len(sources)} "
            f"sources OK, {total_kept} kept, top score {top_score}."
        )
        return EXIT_OK

    # Render — failure here is its own exit code so cron can distinguish.
    try:
        _render_html(kept, _output_path(), succeeded, total_active,
                     total_rejected_filter, log)
    except Exception as e:
        log.exception("render: failed: %s", e)
        if run_id is not None:
            _record_run(run_id, succeeded, failed, new_count, re_seen_count,
                        total_kept, total_rejected_filter, top_score,
                        "render_failure", log)
        print(f"Pipeline FAILED at render: {e}", file=sys.stderr)
        return EXIT_RENDER_FAILURE

    status = "success" if not failed else "partial"
    if run_id is not None:
        _record_run(run_id, succeeded, failed, new_count, re_seen_count,
                    total_kept, total_rejected_filter, top_score, status, log)

    print(
        f"Pipeline complete. {len(succeeded)}/{len(sources)} sources OK, "
        f"{total_kept} postings, top score {top_score}."
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
