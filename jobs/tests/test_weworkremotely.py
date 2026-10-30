"""Tests for crawler/sources/weworkremotely.py.

weworkremotely_sample.rss — live GET
weworkremotely.com/categories/remote-product-jobs.rss, 2026-10-30, trimmed
to 2 items. Items kept as returned.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from crawler.sources.weworkremotely import (
    WeWorkRemotelySource, parse_feed, parse_item, split_title,
)

FIXTURES = Path(__file__).parent / "fixtures"
RSS = (FIXTURES / "weworkremotely_sample.rss").read_text(encoding="utf-8")
ITEMS = parse_feed(RSS)


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


class _FakeSession:
    def __init__(self, statuses=None):
        self.calls = []
        self._statuses = statuses or {}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        feed = url.rsplit("/", 1)[-1].removesuffix(".rss")
        status = self._statuses.get(feed, 200)
        return _Resp(status, RSS if status == 200 else "")


def _source(session, feeds):
    return WeWorkRemotelySource(feeds=feeds, session=session, sleep=lambda s: None)


def test_fixture_shape():
    assert len(ITEMS) == 2
    assert {c.tag for c in ITEMS[0]} >= {"title", "region", "description", "pubDate",
                                         "guid", "link"}


def test_parse_item_fields():
    item = ITEMS[0]
    employer, title = split_title(item.findtext("title"))
    p = parse_item(item)
    assert p.source == "weworkremotely"
    assert (p.employer, p.title) == (employer, title)
    assert employer and title and ": " not in title
    assert p.url == item.findtext("link").strip()
    assert p.source_job_id == item.findtext("guid").rstrip("/").rsplit("/", 1)[-1]
    assert p.location == "Anywhere in the World"
    assert p.remote_type == "full"
    assert isinstance(p.posted_date, date)
    assert "<p>" not in p.raw_text


@pytest.mark.parametrize("raw,expected", [
    ("Toptal: Apigee Engineer: Food Company", ("Toptal", "Apigee Engineer: Food Company")),
    ("No Company Prefix", ("", "No Company Prefix")),
])
def test_split_title(raw, expected):
    assert split_title(raw) == expected


def test_feeds_configured():
    from crawler.sources import weworkremotely
    assert weworkremotely.FEEDS == [
        "remote-programming-jobs", "remote-product-jobs",
        "remote-management-and-finance-jobs", "remote-sales-and-marketing-jobs",
    ]


def test_duplicate_guids_across_feeds_yielded_once():
    session = _FakeSession()
    out = list(_source(session, ["remote-programming-jobs", "remote-product-jobs"]).fetch_listings())
    assert len(out) == len(ITEMS)
    assert len(session.calls) == 2


def test_every_feed_failing_raises():
    session = _FakeSession(statuses={"a": 404, "b": 404})
    with pytest.raises(RuntimeError, match="every feed failed"):
        list(_source(session, ["a", "b"]).fetch_listings())


def test_429_backs_off_rest_of_source():
    session = _FakeSession(statuses={"a": 429})
    assert list(_source(session, ["a", "b"]).fetch_listings()) == []
    assert len(session.calls) == 1
