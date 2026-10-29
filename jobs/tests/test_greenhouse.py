"""Tests for crawler/sources/greenhouse.py.

greenhouse_goatgroup_sample.json — live GET
boards-api.greenhouse.io/v1/boards/goatgroup/jobs?content=true, 2026-10-29,
trimmed to 6 of 19 jobs (meta.total set to 6). Jobs kept as returned.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

import estimate
import filter as filter_mod
import lanes
from crawler.sources import greenhouse
from crawler.sources.greenhouse import GreenhouseSource, parse_posting, parse_salary

FIXTURES = Path(__file__).parent / "fixtures"
BOARD = json.loads((FIXTURES / "greenhouse_goatgroup_sample.json").read_text())
JOBS = {j["id"]: j for j in BOARD["jobs"]}

ASSET_PROTECTION = 4746516005    # "Hiring Range: $20 — $20 USD"
DIRECTOR_FULFILLMENT = 4740607005  # tiered ranges, 3 locations
PERF_MARKETING = 4739429005      # annual range, Remote US
HIRING_EVENT = 4748481005        # "Pay starting at $17/hr"
SUPERVISOR_NO_PAY = 4739403005   # lane 1, no salary
SUPERVISOR_PAY = 4738895005      # lane 1, hourly range


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Serves the fixture for every board unless a status is set for it."""

    def __init__(self, statuses=None, payload=BOARD):
        self.calls = []
        self._statuses = statuses or {}
        self._payload = payload

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        token = url.split("/boards/")[1].split("/")[0]
        status = self._statuses.get(token, 200)
        return _Resp(status, copy.deepcopy(self._payload) if status == 200 else {})


def _source(session, boards=None):
    return GreenhouseSource(boards=boards or {"goatgroup": "GOAT Group"},
                            session=session, sleep=lambda s: None)


# --- Fixture shape (guards against Greenhouse drift) --------------------------

def test_fixture_shape():
    assert set(BOARD) >= {"jobs", "meta"}
    job = BOARD["jobs"][0]
    assert set(job) >= {"id", "title", "location", "absolute_url", "content",
                        "updated_at", "first_published"}
    assert "name" in job["location"]


# --- Field mapping ----------------------------------------------------------------

def test_parse_posting_fields():
    p = parse_posting(JOBS[PERF_MARKETING], "goatgroup")
    assert p.source == "greenhouse"
    assert p.source_job_id == f"goatgroup-{PERF_MARKETING}"
    assert p.title == "Senior Performance Marketing Specialist"
    assert p.employer == "GOAT Group"
    assert p.url == JOBS[PERF_MARKETING]["absolute_url"]
    assert p.location == "Remote US"
    assert p.posted_date == date.fromisoformat(JOBS[PERF_MARKETING]["first_published"][:10])


def test_raw_text_is_unescaped_and_tag_free():
    p = parse_posting(JOBS[PERF_MARKETING], "goatgroup")
    assert "&lt;" not in p.raw_text and "<p>" not in p.raw_text and "<div" not in p.raw_text
    assert "GOAT Group" in p.raw_text


def test_multiple_locations_split():
    p = parse_posting(JOBS[DIRECTOR_FULFILLMENT], "goatgroup")
    assert p.all_locations == [
        "Fontana, California, United States",
        "Los Angeles, California, United States",
        "Teterboro, New Jersey, United States",
    ]


def test_employer_is_brand_per_board():
    job = JOBS[PERF_MARKETING]
    assert parse_posting(job, "stockx").employer == "StockX"
    assert parse_posting(job, "fanaticsinc").employer == "Fanatics"
    assert parse_posting(job, "fanaticscollectibles").employer == "Fanatics Collectibles"


def test_fanatics_betting_board_not_configured():
    assert "fanaticsfbg" not in greenhouse.BOARDS
    assert set(greenhouse.BOARDS) == {"stockx", "fanaticsinc", "fanaticscollectibles", "goatgroup"}


# --- Pay parsing (real pay, normalized by pay.py to USD-monthly) --------------------

def test_tiered_range_takes_first_tier():
    p = parse_posting(JOBS[DIRECTOR_FULFILLMENT], "goatgroup")
    assert (p.salary_min * 12, p.salary_max * 12) == pytest.approx((166900, 208600))
    assert p.pay_note is None


def test_annual_range():
    p = parse_posting(JOBS[PERF_MARKETING], "goatgroup")
    assert (p.salary_min * 12, p.salary_max * 12) == pytest.approx((88800, 111000))


def test_hourly_range_normalized():
    p = parse_posting(JOBS[ASSET_PROTECTION], "goatgroup")
    assert p.salary_min * 12 == pytest.approx(20 * 2080)


def test_single_hourly_figure():
    p = parse_posting(JOBS[HIRING_EVENT], "goatgroup")
    assert p.salary_min * 12 == pytest.approx(17 * 2080)


def test_no_salary_is_unverified():
    p = parse_posting(JOBS[SUPERVISOR_NO_PAY], "goatgroup")
    assert p.salary_min is None and p.pay_note == "no salary listed"


@pytest.mark.parametrize("text,expected", [
    # Fanatics
    ("Salary Range\n$150,000\n—\n$200,000 USD", (150000, 200000, "USD", "annual")),
    ("Los Angeles Salary Range\n$98,900\n—\n$115,700 USD", (98900, 115700, "USD", "annual")),
    # StockX
    ("the base salary range in Colorado for this position is $120,000 to $130,000, plus",
     (120000, 130000, "USD", "annual")),
    ("the base salary range is $60,000/yr to $75,000/yr plus", (60000, 75000, "USD", "annual")),
    ("the base salary is $15.65/hr. Compensation", (15.65, 15.65, "USD", "hourly")),
    ("Pursuant to the various pay transparency laws/acts, the base hourly range is $16 per hour.",
     (16, 16, "USD", "hourly")),
    ("Pay:\n$24.54/hour", (24.54, 24.54, "USD", "hourly")),
    # Non-USD code passes through so pay.py marks it unverified.
    ("Salary Range $90,000 — $110,000 CAD", (90000, 110000, "CAD", "annual")),
])
def test_parse_salary_formats(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize("text", [
    "Salary Range\n$1\n—\n$1 USD",            # Fanatics placeholder
    "compensation includes a $5,000 bonus",    # lone figure, no unit
    "Salary Range $2,000 — $3,000 USD",        # ambiguous (monthly?)
    "No pay details here.",
])
def test_parse_salary_rejects(text):
    assert parse_salary(text)[:2] == (None, None)


def test_cad_range_left_unverified():
    job = copy.deepcopy(JOBS[PERF_MARKETING])
    job["content"] = "&lt;p&gt;Salary Range $90,000 — $110,000 CAD&lt;/p&gt;"
    p = parse_posting(job, "goatgroup")
    assert p.salary_min is None and "CAD" in p.pay_note


# --- Lanes, estimate, filter -----------------------------------------------------

def test_lane1_via_employer_domain_keyword():
    p = parse_posting(JOBS[SUPERVISOR_PAY], "goatgroup")
    assert lanes.assign_lane(p)["lane"] == 1


def test_lane1_without_pay_gets_estimate_with_pay_does_not():
    no_pay = parse_posting(JOBS[SUPERVISOR_NO_PAY], "goatgroup")
    estimate.apply_estimate(no_pay)
    assert no_pay.estimate_source == "level_table"
    with_pay = parse_posting(JOBS[SUPERVISOR_PAY], "goatgroup")
    estimate.apply_estimate(with_pay)
    assert with_pay.estimate_source is None


def test_lane1_hourly_pay_dropped_by_sneaker_floor(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = parse_posting(JOBS[SUPERVISOR_PAY], "goatgroup")
    estimate.apply_estimate(p)
    assert filter_mod.should_keep(p) == (False, "below sneaker-lane floor")


def test_greenhouse_is_allowed_source():
    assert "greenhouse" in filter_mod.ALLOWED_SOURCES


# --- Source behavior ----------------------------------------------------------------

def test_fetch_yields_every_job_one_request_per_board():
    session = _FakeSession()
    out = list(_source(session, {"goatgroup": "GOAT Group", "stockx": "StockX"}).fetch_listings())
    assert len(out) == 2 * len(BOARD["jobs"])
    assert session.calls == [
        "https://boards-api.greenhouse.io/v1/boards/goatgroup/jobs?content=true",
        "https://boards-api.greenhouse.io/v1/boards/stockx/jobs?content=true",
    ]


def test_sleeps_between_boards_not_before_first():
    slept = []
    src = GreenhouseSource(boards={"a": "A", "b": "B", "c": "C"}, session=_FakeSession(),
                           sleep=slept.append)
    list(src.fetch_listings())
    assert slept == [2.0, 2.0]


def test_one_board_failing_keeps_the_others():
    session = _FakeSession(statuses={"stockx": 404})
    out = list(_source(session, {"stockx": "StockX", "goatgroup": "GOAT Group"}).fetch_listings())
    assert len(out) == len(BOARD["jobs"])


def test_every_board_failing_raises():
    session = _FakeSession(statuses={"stockx": 404, "goatgroup": 404})
    with pytest.raises(RuntimeError, match="every board failed"):
        list(_source(session, {"stockx": "StockX", "goatgroup": "GOAT Group"}).fetch_listings())


def test_429_backs_off_rest_of_source():
    session = _FakeSession(statuses={"stockx": 429})
    out = list(_source(session, {"stockx": "StockX", "goatgroup": "GOAT Group"}).fetch_listings())
    assert out == []
    assert len(session.calls) == 1


def test_meta_total_mismatch_logged_but_jobs_kept(caplog):
    payload = copy.deepcopy(BOARD)
    payload["meta"]["total"] = 999
    out = list(_source(_FakeSession(payload=payload)).fetch_listings())
    assert len(out) == len(BOARD["jobs"])
    assert "meta.total=999" in caplog.text
