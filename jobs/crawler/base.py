"""Abstract Source class. See SPEC.md."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Iterator, List, Optional


@dataclass
class Posting:
    source: str
    source_job_id: str
    title: str
    employer: str
    url: str
    raw_text: str
    # Structural job series, distinct from the display title — e.g., CalCareers
    # posts title='.Net Developer' but classification='Information Technology
    # Specialist I'. score.py's title pattern matcher checks BOTH title and
    # classification against target_titles. Sources without a separate
    # classification (USAJobs etc.) leave this None.
    classification: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    location: Optional[str] = None
    # Full list of posting locations, e.g. ['Sacramento, California', 'Wiesbaden, Germany'].
    # filter.py evaluates the Sacramento-metro/overseas hard filter across ALL of these;
    # render.py uses it for smart display. Single-location postings may leave this None
    # and rely on `location`.
    all_locations: Optional[List[str]] = None
    telework_flag: Optional[bool] = None
    posted_date: Optional[date] = None
    # Pay as the SOURCE posted it. Sources that already emit USD-monthly (all
    # current sources) leave the defaults. __post_init__ normalizes salary to
    # USD-monthly via pay.py; after that, currency/pay_period read
    # "USD"/"monthly", or keep the unknown values when pay is unverified.
    currency: Optional[str] = "USD"
    pay_period: Optional[str] = "monthly"
    # "full" | "hybrid" | None. None = let lanes.remote_type() infer from
    # telework_flag + text. Sources that know set it explicitly.
    remote_type: Optional[str] = None
    # Set by normalization: why pay is unverified, or None when it's verified.
    pay_note: Optional[str] = None
    # ESTIMATED annual USD base range from the title level (estimate.py),
    # set only for sneaker-lane postings without verified pay. Never verified
    # pay: digest display + sneaker-floor filter only, never application output.
    estimate_min: Optional[float] = None
    estimate_max: Optional[float] = None
    estimate_source: Optional[str] = None  # "level_table" | None

    def __post_init__(self) -> None:
        from pay import normalize_posting  # noqa: PLC0415
        normalize_posting(self)


class Source(ABC):
    name: str
    delay_seconds: float = 2.0

    @abstractmethod
    def fetch_listings(self) -> Iterator[Posting]:
        ...
