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


class Source(ABC):
    name: str
    delay_seconds: float = 2.0

    @abstractmethod
    def fetch_listings(self) -> Iterator[Posting]:
        ...
