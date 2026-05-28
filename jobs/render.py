"""Build HTML results page. See SPEC.md.

Pure view layer: input is already-scored, already-filtered RowView objects plus
PageStats. No scraping, no scoring, no DB I/O. The view structure is deliberately
decoupled from `crawler.base.Posting` and from the score module's internal types
so that:
  - adding a new posting source means writing source -> RowView glue
  - widening the scorer means widening RowView, not touching score internals
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = REPO_ROOT / "templates"


@dataclass
class WhyPanel:
    rationale: str
    matched_from_background: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)


@dataclass
class RowView:
    source: str
    source_job_id: str
    url: str
    title: str
    employer: str
    grade: Optional[str]

    salary_monthly_min: Optional[float]
    salary_monthly_max: Optional[float]
    salary_annual_min: Optional[float]
    salary_annual_max: Optional[float]

    location_display: str
    all_locations: Optional[List[str]]
    telework: bool
    is_overseas: bool
    posted_date: Optional[date]

    tier1_score: int
    tier2_score: Optional[float]
    emailed: bool

    why: WhyPanel


@dataclass
class PageStats:
    last_run_at: datetime
    sources: List[str]
    scanned: int
    surfaced: int
    emailed_count: int
    rejected: int


def _bucket(rows: List[RowView]) -> Tuple[List[RowView], List[RowView], List[RowView]]:
    """Split rows into (perfect, strong, decent). Below-40 rows are dropped here;
    they survive only in PageStats.rejected."""
    perfect: List[RowView] = []
    strong: List[RowView] = []
    decent: List[RowView] = []
    for r in rows:
        if r.emailed:
            perfect.append(r)
        elif r.tier1_score >= 80:
            strong.append(r)
        elif r.tier1_score >= 40:
            decent.append(r)
    key = lambda r: (-r.tier1_score, r.title.lower())
    return sorted(perfect, key=key), sorted(strong, key=key), sorted(decent, key=key)


def render_page(rows: Iterable[RowView], stats: PageStats, out_path: Path) -> Path:
    """Render the results template and write HTML to out_path. Returns out_path."""
    rows = list(rows)
    perfect, strong, decent = _bucket(rows)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("results.html")
    html = template.render(
        stats=stats,
        perfect=perfect,
        strong=strong,
        decent=decent,
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
