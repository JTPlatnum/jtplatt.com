"""Tests for crawler/sources/himalayas.py.

himalayas_sample.json — live GET himalayas.app/jobs/api?limit=100 (server
returned 20), 2026-10-30, trimmed to 4 jobs: US USD annual, multi-country
no salary, Canada CAD, Portugal hourly. Jobs kept as returned.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import lanes
from crawler.sources.himalayas import HimalayasSource, parse_posting

FIXTURES = Path(__file__).parent / "fixtures"
FEED = json.loads((FIXTURES / "himalayas_sample.json").read_text())
US_JOB, MULTI_COUNTRY, CANADA_CAD, PORTUGAL_HOURLY = FEED["jobs"]


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Serves `pages` pages of the fixture jobs, each with fresh guids."""

    def __init__(self, pages=3, status=200):
        self.calls = []
        self._pages = pages
        self._status = status

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self._status != 200:
            return _Resp(self._status, {})
        n = len(self.calls)
        page = copy.deepcopy(FEED)
        for j in page["jobs"]:
            j["guid"] = f"{j['guid']}?p={n}"
        page["nextCursor"] = f"c{n}" if n < self._pages else None
        return _Resp(200, page)


def _source(session, max_jobs=500):
    return HimalayasSource(max_jobs=max_jobs, session=session, sleep=lambda s: None)


def test_fixture_shape():
    assert set(FEED) >= {"jobs", "nextCursor", "totalCount"}
    assert set(US_JOB) >= {"guid", "title", "companyName", "applicationLink", "description",
                           "locationRestrictions", "minSalary", "maxSalary", "currency",
                           "salaryPeriod", "pubDate"}


def test_parse_posting_fields():
    p = parse_posting(US_JOB)
    assert p.source == "himalayas"
    assert p.source_job_id == US_JOB["guid"]
    assert p.title == US_JOB["title"]
    assert p.employer == US_JOB["companyName"]
    assert p.url == US_JOB["applicationLink"]
    assert p.remote_type == "full"
    assert p.all_locations == ["United States"]
    assert p.posted_date == datetime.fromtimestamp(US_JOB["pubDate"], tz=timezone.utc).date()


def test_usd_annual_salary():
    p = parse_posting(US_JOB)
    assert (p.salary_min * 12, p.salary_max * 12) == pytest.approx(
        (US_JOB["minSalary"], US_JOB["maxSalary"]))


def test_hourly_salary_through_pay_py():
    p = parse_posting(PORTUGAL_HOURLY)
    assert p.salary_min * 12 == pytest.approx(31 * 2080)


def test_cad_not_in_currency_table_is_unverified():
    p = parse_posting(CANADA_CAD)
    assert p.salary_min is None and "CAD" in p.pay_note


def test_candidate_location_scope():
    assert lanes.remote_board_scope(parse_posting(US_JOB)) == "us"
    assert lanes.remote_board_scope(parse_posting(MULTI_COUNTRY)) == "overseas"
    no_restriction = copy.deepcopy(US_JOB)
    no_restriction["locationRestrictions"] = []
    assert lanes.remote_board_scope(parse_posting(no_restriction)) == "anywhere"


def test_follows_cursor_until_exhausted():
    session = _FakeSession(pages=3)
    out = list(_source(session).fetch_listings())
    assert len(out) == 3 * len(FEED["jobs"])
    assert session.calls == [{"limit": 20}, {"limit": 20, "cursor": "c1"},
                             {"limit": 20, "cursor": "c2"}]


def test_caps_at_max_jobs():
    session = _FakeSession(pages=100)
    out = list(_source(session, max_jobs=6).fetch_listings())
    assert len(out) == 6
    assert len(session.calls) == 2


def test_first_page_failure_raises():
    with pytest.raises(RuntimeError, match="first page failed"):
        list(_source(_FakeSession(status=404)).fetch_listings())


def test_429_backs_off():
    session = _FakeSession(status=429)
    assert list(_source(session).fetch_listings()) == []
    assert len(session.calls) == 1
