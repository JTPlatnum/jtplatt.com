"""Abstract Source class. See SPEC.md."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Optional


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
    telework_flag: Optional[bool] = None
    posted_date: Optional[date] = None


class Source(ABC):
    name: str
    delay_seconds: float = 2.0

    @abstractmethod
    def fetch_listings(self) -> Iterator[Posting]:
        ...
