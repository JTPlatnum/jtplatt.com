"""Tests for crawler/sources/nike.py against captured Workday fixtures.

nike_sample.json        — live list POST (offset 0, limit 20, no facets), 2026-10-28
nike_detail_sample.json — live detail GET for R-96534, 2026-10-28
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from crawler.sources import nike
from crawler.sources.nike import NikeSource

FIXTURES = Path(__file__).parent / "fixtures"
LIST = json.loads((FIXTURES / "nike_sample.json").read_text())
DETAIL = json.loads((FIXTURES / "nike_detail_sample.json").read_text())


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)[:300]

    def json(self):
        return self._payload


class _FakeSession:
    """Serves list pages from the fixture and the one detail for any job."""

    def __init__(self, list_payload=LIST, total=None, detail_status=200, list_status=200):
        self.calls = []
        self._list = list_payload
        self._total = total
        self._detail_status = detail_status
        self._list_status = list_status

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, json))
        if method == "POST":
            if self._list_status != 200:
                return _Resp(self._list_status, {})
            page = copy.deepcopy(self._list)
            if json["appliedFacets"]:
                page["total"] = self._total if self._total is not None else page["total"]
                if json["offset"] >= page["total"]:
                    page["jobPostings"] = []
            return _Resp(200, page)
        return _Resp(self._detail_status, DETAIL)


def _source(session, known=None):
    return NikeSource(session=session, known_jobs_loader=lambda: known or {},
                      sleep=lambda s: None)


# --- Fixture shape (guards against Workday drift) ---------------------------

def test_list_fixture_shape():
    assert set(LIST) >= {"total", "jobPostings", "facets"}
    item = LIST["jobPostings"][0]
    assert set(item) >= {"title", "externalPath", "locationsText", "bulletFields"}


def test_detail_fixture_shape():
    info = DETAIL["jobPostingInfo"]
    assert set(info) >= {"jobDescription", "startDate", "country", "externalUrl", "jobReqId"}


# --- Facets -----------------------------------------------------------------

def test_family_ids_excludes_retail_stores():
    ids = nike.family_ids(LIST["facets"])
    fam = next(f for f in LIST["facets"] if f["facetParameter"] == "jobFamilyGroup")
    retail = next(v["id"] for v in fam["values"] if v["descriptor"] == "Retail Stores")
    assert retail not in ids
    assert len(ids) == len(fam["values"]) - 1


def test_family_ids_empty_without_facet():
    assert nike.family_ids([]) == []


# --- Parsing ----------------------------------------------------------------

def test_parse_posting_maps_fields():
    item = LIST["jobPostings"][0]
    p = nike.parse_posting(item, DETAIL)
    assert p.source == "nike"
    assert p.source_job_id == "R-96534"
    assert p.title == "Senior Technical Developer, Global Apparel Graphics - PCC"
    assert p.employer == "NIKE, Inc."
    assert p.url.startswith("https://nike.wd1.myworkdayjobs.com/nke/job/")
    assert p.location == "Beaverton, Oregon"
    assert p.all_locations == ["Beaverton, Oregon"]
    assert p.posted_date == date(2026, 10, 28)
    assert "<" not in p.raw_text and "WHO YOU" in p.raw_text
    assert p.salary_min is None and p.pay_note == "no salary listed"


def test_with_country_appends_when_missing():
    assert nike.with_country("NFS IT TORINO", "Italy") == "NFS IT TORINO, Italy"


def test_with_country_skips_when_present_or_us():
    assert nike.with_country("Paris, France", "France") == "Paris, France"
    assert nike.with_country("Beaverton, Oregon", "United States of America") == "Beaverton, Oregon"


def test_multi_location_uses_detail_locations():
    item = dict(LIST["jobPostings"][0], locationsText="2 Locations")
    detail = copy.deepcopy(DETAIL)
    detail["jobPostingInfo"].update(
        location="Shanghai, China Mainland",
        additionalLocations=["Ho Chi Minh City, Vietnam"],
        country={"descriptor": "China"},
    )
    p = nike.parse_posting(item, detail)
    assert p.all_locations == ["Shanghai, China Mainland", "Ho Chi Minh City, Vietnam, China"]


def test_strip_html():
    assert nike.strip_html("<h1>Hi</h1><p>there <b>you</b></p>") == "Hi\nthere\nyou"


@pytest.mark.parametrize("text,expected", [
    ("The annual base salary for this position ranges from $120,000.00 in our "
     "lowest market to $180,000.00 in our highest.", (120_000.0, 180_000.0)),
    ("Pay range: $25.00 - $30.00 per hour", (None, None)),
    ("We offer great benefits.", (None, None)),
])
def test_parse_salary(text, expected):
    assert nike.parse_salary(text) == expected


def test_parsed_annual_salary_normalizes_to_monthly():
    detail = copy.deepcopy(DETAIL)
    detail["jobPostingInfo"]["jobDescription"] = (
        "<p>The annual base salary ranges from $120,000.00 to $180,000.00.</p>")
    p = nike.parse_posting(LIST["jobPostings"][0], detail)
    assert p.salary_min == pytest.approx(10_000)
    assert p.salary_max == pytest.approx(15_000)


def test_job_id_falls_back_to_path():
    assert nike.job_id({"externalPath": "/job/X/Some-Role_R-91798"}) == "R-91798"


# --- fetch_listings -----------------------------------------------------------

def test_fetch_paginates_and_fetches_details_for_new_jobs():
    session = _FakeSession(total=20)
    postings = list(_source(session).fetch_listings())
    posts = [c for c in session.calls if c[0] == "POST"]
    gets = [c for c in session.calls if c[0] == "GET"]
    # unfiltered facet probe + one filtered page (total=20, limit=20)
    assert len(posts) == 2
    assert posts[0][2]["appliedFacets"] == {}
    assert "jobFamilyGroup" in posts[1][2]["appliedFacets"]
    assert all(c[2]["limit"] == 20 for c in posts)
    assert len(gets) == len({nike.job_id(i) for i in LIST["jobPostings"]})
    assert len(postings) == len(gets)


def test_fetch_reads_total_from_first_page_and_steps_offset():
    session = _FakeSession(total=45)
    list(_source(session).fetch_listings())
    offsets = [c[2]["offset"] for c in session.calls if c[0] == "POST"][1:]
    assert offsets == [0, 20, 40]


def test_known_jobs_skip_detail_and_keep_stored_text():
    item = LIST["jobPostings"][0]
    known = {nike.job_id(item): {
        "source_job_id": nike.job_id(item), "title": "old title", "employer": "NIKE, Inc.",
        "url": "https://stored", "raw_text": "stored description", "classification": None,
        "salary_min": None, "salary_max": None, "location": "Beaverton, Oregon",
        "all_locations": ["Beaverton, Oregon"], "telework_flag": None,
        "posted_date": date(2026, 10, 1),
    }}
    session = _FakeSession(total=20)
    postings = {p.source_job_id: p for p in _source(session, known).fetch_listings()}
    reused = postings[nike.job_id(item)]
    assert reused.raw_text == "stored description"
    assert reused.title == item["title"]  # fresh from list
    gets = [c for c in session.calls if c[0] == "GET"]
    assert not any(item["externalPath"] in c[1] for c in gets)


def test_known_job_without_text_refetches_detail():
    item = LIST["jobPostings"][0]
    known = {nike.job_id(item): {"source_job_id": nike.job_id(item), "raw_text": ""}}
    session = _FakeSession(total=20)
    list(_source(session, known).fetch_listings())
    assert any(item["externalPath"] in c[1] for c in session.calls if c[0] == "GET")


def test_first_page_failure_raises():
    with pytest.raises(RuntimeError):
        list(_source(_FakeSession(list_status=404)).fetch_listings())


def test_5xx_backs_off_without_raising():
    session = _FakeSession(total=20, detail_status=503)
    postings = list(_source(session).fetch_listings())
    assert postings == []
    assert len([c for c in session.calls if c[0] == "GET"]) == 1


def test_detail_404_skips_job_and_continues():
    session = _FakeSession(total=20, detail_status=404)
    assert list(_source(session).fetch_listings()) == []
    assert len([c for c in session.calls if c[0] == "GET"]) > 1


def test_progress_logged_every_n_details(monkeypatch, caplog):
    monkeypatch.setattr(nike, "PROGRESS_EVERY", 5)
    caplog.set_level("INFO", logger="crawler.sources.nike")
    list(_source(_FakeSession(total=20)).fetch_listings())
    n = len({nike.job_id(i) for i in LIST["jobPostings"]})
    msgs = [r.getMessage() for r in caplog.records]
    assert f"nike: 20 listed, {n} need detail fetch" in msgs
    assert f"nike: 5/{n} details" in msgs
