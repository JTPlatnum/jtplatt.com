"""Weekly email digest. See SPEC.md "Email Digest".

Replaces the old per-job "perfect fit" alert (T1 >= 80 AND T2 >= 8). Once a
week, emails up to DIGEST_SIZE active jobs: at most LANE1_CAP sneaker-lane
jobs, LANE3_RESERVE slots held for lane 3, the rest from lanes 2-5 by score,
then unlaned (see select_jobs).
Grouped by lane (sneakers first, unlaned last), highest score first within a
lane. No score threshold: low-scoring lane-1 jobs (pay unverified) are
included.

Jobs come from the same path the results page uses (main._filter_and_score):
store.get_active_jobs(14 days) -> filter.should_keep -> score.score_posting.
The digest never re-implements filtering or scoring.

CLI (run from jobs/):
    python -m notify --send-digest          build + send now
    python -m notify --preview out.html     build + write HTML, send nothing

Env (.env): SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO.
Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587 (STARTTLS), SMTP_PASSWORD is
a Google App Password, not the account password.
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import smtplib
import sys
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "jobs.db"
DIGEST_SIZE = 12
LANE1_CAP = 6  # sneaker lane gets at most half the digest
LANE3_RESERVE = 4  # remote knowledge work: JT's target; lane 2 would crowd it out

EXIT_OK = 0
EXIT_FAILURE = 1

log = logging.getLogger("notify")


# --- Digest model -----------------------------------------------------------

@dataclass
class DigestJob:
    title: str
    employer: str
    location: str
    # "$120,000-$180,000/yr", "pay unverified", or a lane-1 estimate (never
    # verified pay): "est. $200k-$310k *actual salary range not posted" (US),
    # "... *actual salary not posted; assumes expat/US pay" (overseas), or
    # "est. $112k-$168k *local-hire role" (overseas local-hire)
    pay: str
    lane: Optional[int]
    lane_name: str
    score: int
    url: str
    # Display name of the source; Remotive / Remote OK / WWR / Himalayas
    # terms require attribution.
    source: str = ""


# Display names for the "via <source>" attribution on each job.
SOURCE_NAMES = {
    "usajobs": "USAJobs",
    "calcareers": "CalCareers",
    "csu": "CSU Careers",
    "nike": "Nike Careers",
    "greenhouse": "Greenhouse",
    "remotive": "Remotive",
    "remoteok": "Remote OK",
    "weworkremotely": "We Work Remotely",
    "himalayas": "Himalayas",
}


ESTIMATE_MARKERS = {
    "us": "*actual salary range not posted",
    "expat": "*actual salary not posted; assumes expat/US pay",
    "local_hire": "*local-hire role",
}


def _k(annual: float) -> str:
    return f"${annual / 1000:,.0f}k"


def _pay_text(posting) -> str:
    if posting.salary_min is None:
        if posting.estimate_min is not None:
            hi = posting.estimate_max or posting.estimate_min
            import estimate  # noqa: PLC0415
            marker = ESTIMATE_MARKERS[estimate.estimate_basis(posting)]
            return f"est. {_k(posting.estimate_min)}-{_k(hi)} {marker}"
        return "pay unverified"
    lo = posting.salary_min * 12
    hi = (posting.salary_max or posting.salary_min) * 12
    if round(hi) == round(lo):
        return f"${lo:,.0f}/yr"
    return f"${lo:,.0f}-${hi:,.0f}/yr"


def _location_text(posting) -> str:
    if posting.location:
        return posting.location
    if posting.all_locations:
        return "; ".join(posting.all_locations)
    return "location not listed"


def to_digest_job(posting, result: Dict[str, Any]) -> DigestJob:
    lane = result["lane"]
    return DigestJob(
        title=posting.title,
        employer=posting.employer,
        location=_location_text(posting),
        pay=_pay_text(posting),
        lane=lane["lane"],
        lane_name=lane["name"] or "Unlaned",
        score=result["score"],
        url=posting.url,
        source=SOURCE_NAMES.get(posting.source, posting.source),
    )


def _by_score(j: DigestJob) -> Tuple[int, str]:
    return (-j.score, j.title.lower())


def _lane_order(j: DigestJob) -> Tuple[int, int, str]:
    return (j.lane if j.lane is not None else 99, -j.score, j.title.lower())


def select_jobs(
    kept: Sequence[Tuple[Any, Dict[str, Any]]],
    size: int = DIGEST_SIZE,
    lane1_cap: int = LANE1_CAP,
    lane3_reserve: int = LANE3_RESERVE,
) -> List[DigestJob]:
    """Pick up to `size` jobs with a per-lane cap. No score threshold.

    1. Up to `lane1_cap` lane-1 (sneaker) jobs, highest score first.
    2. Up to `lane3_reserve` lane-3 jobs by score (reserved; fewer lane-3
       jobs -> the unused slots go back to the general pool).
    3. Remaining slots from lanes 2-5 by score (any of those lanes,
       including more lane 3).
    4. Still-empty slots from unlaned jobs by score.
    The lane-1 cap is hard: if the other lanes run dry, the digest is shorter
    rather than refilled with more sneaker jobs.

    Returned in display order: lane 1 first, unlaned last, score desc
    within a lane."""
    jobs = [to_digest_job(p, r) for p, r in kept]
    lane1 = sorted((j for j in jobs if j.lane == 1), key=_by_score)[:lane1_cap]
    remaining = size - len(lane1)
    lane3 = sorted((j for j in jobs if j.lane == 3), key=_by_score)[:min(lane3_reserve, remaining)]
    remaining -= len(lane3)
    reserved = {id(j) for j in lane3}
    others = sorted((j for j in jobs if j.lane not in (1, None) and id(j) not in reserved),
                    key=_by_score)[:remaining]
    others = lane3 + others
    remaining = size - len(lane1) - len(others)
    unlaned = sorted((j for j in jobs if j.lane is None), key=_by_score)[:max(0, remaining)]
    return sorted(lane1 + others + unlaned, key=_lane_order)


def group_by_lane(jobs: Sequence[DigestJob]) -> List[Tuple[str, List[DigestJob]]]:
    """Preserve select_jobs order; one group per lane present."""
    groups: List[Tuple[str, List[DigestJob]]] = []
    for j in jobs:
        label = f"Lane {j.lane}: {j.lane_name}" if j.lane else "Unlaned"
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append(j)
    return groups


# --- Rendering ----------------------------------------------------------------

def subject_line(jobs: Sequence[DigestJob], today: date) -> str:
    return f"Job digest {today.isoformat()}: {len(jobs)} jobs"


def render_text(jobs: Sequence[DigestJob], total_kept: int, today: date) -> str:
    lines = [f"Job digest, {today.isoformat()}",
             f"Top {len(jobs)} of {total_kept} jobs that passed the filters (seen in the last 14 days).", ""]
    if not jobs:
        lines.append("No active jobs passed the filters this week.")
    for label, group in group_by_lane(jobs):
        lines += [label.upper(), ""]
        for j in group:
            lines += [
                f"{j.title}",
                f"  {j.employer} | {j.location}",
                f"  {j.pay} | score {j.score}" + (f" | via {j.source}" if j.source else ""),
                f"  {j.url}",
                "",
            ]
    return "\n".join(lines)


# Inline styles only (mail clients strip <style>). Single column, no images,
# no remote resources, no tracking.
_FONT = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"


def render_html(jobs: Sequence[DigestJob], total_kept: int, today: date) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'></head>",
        f"<body style='margin:0;padding:16px;background:#ffffff;color:#111111;{_FONT}'>",
        "<div style='max-width:600px;margin:0 auto;'>",
        f"<h1 style='font-size:20px;margin:0 0 4px;'>Job digest, {e(today.isoformat())}</h1>",
        f"<p style='font-size:14px;color:#555555;margin:0 0 16px;'>"
        f"Top {len(jobs)} of {total_kept} jobs that passed the filters (seen in the last 14 days).</p>",
    ]
    if not jobs:
        parts.append("<p style='font-size:15px;'>No active jobs passed the filters this week.</p>")
    for label, group in group_by_lane(jobs):
        parts.append(
            f"<h2 style='font-size:16px;margin:24px 0 8px;padding-bottom:4px;"
            f"border-bottom:2px solid #111111;'>{e(label)}</h2>")
        for j in group:
            unverified = j.pay == "pay unverified" or j.pay.startswith("est. ")
            pay_style = "color:#8a5a00;" if unverified else ""
            parts.append(
                "<div style='padding:12px 0;border-bottom:1px solid #dddddd;'>"
                f"<a href='{e(j.url, quote=True)}' style='font-size:16px;font-weight:600;"
                f"color:#0b57d0;text-decoration:none;'>{e(j.title)}</a>"
                f"<div style='font-size:14px;margin-top:4px;'>{e(j.employer)}</div>"
                f"<div style='font-size:14px;color:#555555;'>{e(j.location)}</div>"
                f"<div style='font-size:14px;margin-top:4px;'>"
                f"<span style='{pay_style}'>{e(j.pay)}</span>"
                f" &middot; score {j.score}"
                + (f" &middot; via {e(j.source)}" if j.source else "")
                + "</div>"
                "</div>")
    parts.append("</div></body></html>")
    return "".join(parts)


# --- Build + send -----------------------------------------------------------------

def build_digest(log_: logging.Logger = log, size: int = DIGEST_SIZE) -> Tuple[List[DigestJob], int]:
    """Active jobs -> filter -> score (via main) -> selected digest jobs.
    Caller must have called store.init_db first."""
    import main  # noqa: PLC0415
    kept, _rejected, _total_kept = main._filter_and_score(log_)
    return select_jobs(kept, size), len(kept)


def build_message(jobs: Sequence[DigestJob], total_kept: int, today: date,
                  sender: str, to: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject_line(jobs, today)
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(render_text(jobs, total_kept, today))
    msg.add_alternative(render_html(jobs, total_kept, today), subtype="html")
    return msg


def _smtp_config() -> Dict[str, Any]:
    missing = [k for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO")
               if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"notify: missing env vars: {', '.join(missing)}")
    return {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.environ["SMTP_PORT"]),
        "user": os.environ["SMTP_USER"],
        "password": os.environ["SMTP_PASSWORD"],
        "to": os.environ["EMAIL_TO"],
    }


def send_message(msg: EmailMessage, cfg: Dict[str, Any], timeout: float = 30.0) -> None:
    """STARTTLS (Gmail: port 587). Raises on any SMTP failure."""
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout) as smtp:
        smtp.starttls()
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


# --- CLI ------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly job digest email.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--send-digest", action="store_true",
                      help="Build the digest from the current DB and email it now.")
    mode.add_argument("--preview", metavar="PATH",
                      help="Build the digest and write the HTML to PATH. Sends nothing.")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import store  # noqa: PLC0415
    store.init_db(DB_PATH)
    jobs, total_kept = build_digest()
    today = date.today()
    log.info("digest: %d jobs selected of %d active", len(jobs), total_kept)

    if args.preview:
        out = Path(args.preview)
        out.write_text(render_html(jobs, total_kept, today), encoding="utf-8")
        print(f"Preview written to {out.resolve()} ({len(jobs)} jobs). Nothing sent.")
        return EXIT_OK

    try:
        cfg = _smtp_config()
        send_message(build_message(jobs, total_kept, today, cfg["user"], cfg["to"]), cfg)
    except Exception as e:
        log.exception("digest: send failed: %s", e)
        print(f"Digest send FAILED: {e}", file=sys.stderr)
        return EXIT_FAILURE
    log.info("digest: sent %d jobs to %s", len(jobs), cfg["to"])
    print(f"Digest sent: {len(jobs)} jobs to {cfg['to']}.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
