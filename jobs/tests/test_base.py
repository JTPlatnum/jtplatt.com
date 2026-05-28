"""Tests for crawler.base."""
import pytest

from crawler.base import Posting, Source


def test_source_is_abstract():
    with pytest.raises(TypeError):
        Source()


class _NoOpSource(Source):
    name = "noop"

    def fetch_listings(self):
        yield from ()


def test_concrete_subclass_instantiable():
    src = _NoOpSource()
    assert src.name == "noop"
    assert src.delay_seconds == 2.0
    assert list(src.fetch_listings()) == []


def test_posting_only_required_fields():
    p = Posting(
        source="test",
        source_job_id="abc-123",
        title="Test Job",
        employer="Test Employer",
        url="https://example.com/job/abc-123",
        raw_text="full posting body",
    )
    assert p.salary_min is None
    assert p.salary_max is None
    assert p.location is None
    assert p.all_locations is None
    assert p.telework_flag is None
    assert p.posted_date is None


def test_posting_accepts_all_locations():
    p = Posting(
        source="test",
        source_job_id="abc-123",
        title="Test Job",
        employer="Test Employer",
        url="https://example.com/job/abc-123",
        raw_text="full posting body",
        location="Sacramento, California",
        all_locations=["Sacramento, California", "Wiesbaden, Germany"],
    )
    assert p.location == "Sacramento, California"
    assert p.all_locations == ["Sacramento, California", "Wiesbaden, Germany"]
