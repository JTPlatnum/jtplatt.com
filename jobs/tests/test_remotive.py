"""Tests for crawler/sources/remotive.py.

remotive_sample.json — live GET remotive.com/api/remote-jobs?limit=5,
2026-10-30, trimmed to 6 jobs chosen for salary variety. Jobs kept as returned.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

import estimate
from crawler.sources.remotive import RemotiveSource, parse_posting, parse_salary

FIXTURES = Path(__file__).parent / "fixtures"
FEED = json.loads((FIXTURES / "remotive_sample.json").read_text())
BY_SALARY = {j["salary"]: j for j in FEED["jobs"]}


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, status=200):
        self.calls = []
        self._status = status

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return _Resp(self._status, copy.deepcopy(FEED) if self._status == 200 else {})


def test_fixture_shape():
    job = FEED["jobs"][0]
    assert set(job) >= {"id", "title", "company_name", "candidate_required_location",
                        "salary", "description", "url", "publication_date"}


def test_parse_posting_fields():
    job = BY_SALARY["$170k - $200k"]
    p = parse_posting(job)
    assert p.source == "remotive"
    assert p.source_job_id == str(job["id"])
    assert p.title == job["title"].strip()
    assert p.employer == job["company_name"]
    assert p.url == job["url"]
    assert p.remote_type == "full"
    assert p.all_locations == ["USA", "Canada", "USA timezones"]
    assert p.posted_date == date.fromisoformat(job["publication_date"][:10])
    assert "<p>" not in p.raw_text


def test_annual_range_is_real_pay():
    p = parse_posting(BY_SALARY["$170k - $200k"])
    assert (p.salary_min * 12, p.salary_max * 12) == pytest.approx((170000, 200000))


def test_hourly_range_is_real_pay():
    p = parse_posting(BY_SALARY["$50-$75 /hour"])
    assert p.salary_min * 12 == pytest.approx(50 * 2080)


@pytest.mark.parametrize("salary", ["70K-80K", "OTE $25k - $35k", ""])
def test_unparseable_salary_is_unverified(salary):
    p = parse_posting(BY_SALARY[salary])
    assert p.salary_min is None


@pytest.mark.parametrize("text,expected", [
    ("$35,3k- $52k", (35300, 52000, "annual")),
    ("$170,000 - $200,000", (170000, 200000, "annual")),
    ("$40/hour", (40, 40, "hourly")),
    ("$1000 per task", (None, None, None)),
    ("$120k", (None, None, None)),  # single annual figure: not parsed
])
def test_parse_salary_formats(text, expected):
    assert parse_salary(text) == expected


def test_no_estimate_even_if_lane1():
    job = copy.deepcopy(BY_SALARY[""])
    job["title"], job["description"] = "Product Manager", "<p>Nike footwear sneaker</p>"
    p = parse_posting(job)
    estimate.apply_estimate(p, lane=1)
    assert p.estimate_min is None and p.estimate_source is None


def test_fetch_yields_all_jobs_one_request():
    session = _FakeSession()
    out = list(RemotiveSource(session=session).fetch_listings())
    assert len(out) == len(FEED["jobs"])
    assert session.calls == ["https://remotive.com/api/remote-jobs"]


def test_bad_status_raises():
    with pytest.raises(RuntimeError, match="status 429"):
        list(RemotiveSource(session=_FakeSession(status=429)).fetch_listings())
