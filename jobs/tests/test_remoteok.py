"""Tests for crawler/sources/remoteok.py.

remoteok_sample.json — live GET remoteok.com/api, 2026-10-30, trimmed to the
metadata element + 3 jobs (salary + no location; no salary + location;
neither). Elements kept as returned.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from crawler.sources.remoteok import RemoteOKSource, parse_posting

FIXTURES = Path(__file__).parent / "fixtures"
FEED = json.loads((FIXTURES / "remoteok_sample.json").read_text())
META, JOBS = FEED[0], FEED[1:]
WITH_PAY, WITH_LOCATION, BARE = JOBS


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
        self.calls.append((url, headers))
        return _Resp(self._status, copy.deepcopy(FEED) if self._status == 200 else {})


def test_fixture_shape():
    assert "legal" in META and "id" not in META
    assert set(JOBS[0]) >= {"id", "position", "company", "location", "salary_min",
                            "salary_max", "description", "url", "date"}


def test_parse_posting_fields():
    p = parse_posting(WITH_LOCATION)
    assert p.source == "remoteok"
    assert p.source_job_id == str(WITH_LOCATION["id"])
    assert p.title == WITH_LOCATION["position"]
    assert p.employer == WITH_LOCATION["company"]
    assert p.url == WITH_LOCATION["url"]
    assert p.location == "Toronto, Ontario"
    assert p.remote_type == "full"
    assert p.posted_date == date.fromisoformat(WITH_LOCATION["date"][:10])


def test_salary_is_usd_annual():
    p = parse_posting(WITH_PAY)
    assert (p.salary_min * 12, p.salary_max * 12) == pytest.approx((80000, 100000))


def test_zero_salary_is_unverified_and_empty_location_is_none():
    p = parse_posting(BARE)
    assert p.salary_min is None and p.pay_note == "no salary listed"
    assert p.location is None and p.all_locations is None


def test_fetch_skips_metadata_and_sends_user_agent():
    session = _FakeSession()
    out = list(RemoteOKSource(session=session).fetch_listings())
    assert len(out) == len(JOBS)
    url, headers = session.calls[0]
    assert url == "https://remoteok.com/api"
    assert "Mozilla" in headers["User-Agent"]


def test_bad_status_raises():
    with pytest.raises(RuntimeError, match="status 403"):
        list(RemoteOKSource(session=_FakeSession(status=403)).fetch_listings())
