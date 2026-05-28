"""Tests for crawler/sources/usajobs.py — parsing only. No live HTTP."""
import json
from datetime import date
from pathlib import Path

import pytest

from crawler.base import Posting
from crawler.sources.usajobs import (
    parse_posting,
    _build_params,
    _parse_telework,
    _strip_port,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "usajobs_sample.json"


@pytest.fixture
def fixture_items() -> list:
    data = json.loads(FIXTURE.read_text())
    return data["SearchResult"]["SearchResultItems"]


@pytest.fixture
def first_descriptor(fixture_items) -> dict:
    return fixture_items[0]["MatchedObjectDescriptor"]


def test_parses_to_posting(first_descriptor):
    p = parse_posting(first_descriptor)
    assert isinstance(p, Posting)
    assert p.source == "usajobs"


def test_title_and_employer(first_descriptor):
    p = parse_posting(first_descriptor)
    assert p.title == "Azure Architect"
    assert p.employer == "Other Agencies and Independent Organizations / Railroad Retirement Board"


def test_source_job_id_matches_position_id(first_descriptor):
    p = parse_posting(first_descriptor)
    assert p.source_job_id == first_descriptor["PositionID"]


def test_url_strips_port_443(first_descriptor):
    p = parse_posting(first_descriptor)
    assert ":443" not in p.url
    assert p.url.startswith("https://www.usajobs.gov/")


def test_salary_monthly_conversion(first_descriptor):
    # PositionRemuneration[0] is "90925" / "118204", PA. Monthly = /12.
    p = parse_posting(first_descriptor)
    assert p.salary_min == pytest.approx(90925.0 / 12.0)
    assert p.salary_max == pytest.approx(118204.0 / 12.0)


def test_all_locations_length_matches_fixture(first_descriptor):
    p = parse_posting(first_descriptor)
    expected_count = len(first_descriptor["PositionLocation"])
    assert p.all_locations is not None
    assert len(p.all_locations) == expected_count


def test_multi_location_uses_position_location_display(first_descriptor):
    # Fixture posting has multiple PositionLocation entries.
    p = parse_posting(first_descriptor)
    assert p.location == first_descriptor.get("PositionLocationDisplay")


def test_raw_text_non_empty_and_sourced_from_user_area_details(first_descriptor):
    p = parse_posting(first_descriptor)
    assert p.raw_text  # non-empty
    # The real description content (JobSummary, MajorDuties) lives in
    # UserArea.Details — confirm that content (not PositionFormattedDescription's
    # 'Dynamic Teaser' placeholder) drives raw_text.
    js = first_descriptor["UserArea"]["Details"]["JobSummary"]
    assert js[:60] in p.raw_text
    assert "=== Job Summary ===" in p.raw_text
    assert "Dynamic Teaser" not in p.raw_text
    assert "Hit highlighting" not in p.raw_text


def test_raw_text_includes_qualification_summary(first_descriptor):
    p = parse_posting(first_descriptor)
    qs = first_descriptor["QualificationSummary"]
    assert "=== Qualification Summary ===" in p.raw_text
    assert qs[:60] in p.raw_text


def test_posted_date_parses_3_10_safe(first_descriptor):
    # PublicationStartDate format: "2026-05-26T07:11:05.6100" (4-digit fractional
    # seconds, no Z). date.fromisoformat(value[:10]) is the 3.10-safe parse.
    p = parse_posting(first_descriptor)
    pub = first_descriptor["PublicationStartDate"]
    assert p.posted_date == date.fromisoformat(pub[:10])
    assert isinstance(p.posted_date, date)


def test_all_five_fixture_items_parse_without_error(fixture_items):
    postings = [parse_posting(it["MatchedObjectDescriptor"]) for it in fixture_items]
    assert len(postings) == 5
    for p in postings:
        assert isinstance(p, Posting)
        assert p.source_job_id
        assert p.title
        assert p.url
        assert p.raw_text


# --- Parameter / helper tests ----------------------------------------------

def test_build_params_joins_codes_with_semicolons():
    query = {
        "job_category_codes": [2210, 343, 1750],
        "location_name": "Sacramento, California",
        "radius": 50,
        "who_may_apply": "Public",
        "sort_field": "OpenDate",
        "sort_direction": "Desc",
        "results_per_page": 250,
    }
    params = _build_params(query, page=1)
    assert params["JobCategoryCode"] == "2210;343;1750"
    assert params["LocationName"] == "Sacramento, California"
    assert params["Radius"] == 50
    assert params["WhoMayApply"] == "Public"
    assert params["SortField"] == "OpenDate"
    assert params["SortDirection"] == "Desc"
    assert params["ResultsPerPage"] == 250
    assert params["Page"] == 1


def test_build_params_omits_unspecified_fields():
    params = _build_params({"job_category_codes": [2210]}, page=3)
    assert params["JobCategoryCode"] == "2210"
    assert params["Page"] == 3
    assert "LocationName" not in params
    assert "Radius" not in params
    assert "Organization" not in params


def test_strip_port_handles_missing_443():
    assert _strip_port("https://www.usajobs.gov/job/123") == "https://www.usajobs.gov/job/123"
    assert _strip_port("") == ""


# --- Telework parsing -------------------------------------------------------

def test_telework_structured_true_wins():
    mod = {"UserArea": {"Details": {"TeleworkEligible": True}}}
    assert _parse_telework(mod, "no signal") is True


def test_telework_structured_false_with_no_keyword_returns_false():
    mod = {"UserArea": {"Details": {"TeleworkEligible": False}}}
    assert _parse_telework(mod, "fully on-site role") is False


def test_telework_keyword_fallback_when_structured_missing():
    mod = {"UserArea": {"Details": {}}}
    assert _parse_telework(mod, "Hybrid schedule available.") is True


def test_telework_keyword_overrides_structured_false():
    # Per recon §4: structured flag is unreliable. Description-keyword scan
    # is the truth source; if the description says "remote", we trust it.
    mod = {"UserArea": {"Details": {"TeleworkEligible": False}}}
    assert _parse_telework(mod, "Position is fully remote.") is True


def test_telework_none_when_nothing_known():
    mod = {"UserArea": {"Details": {}}}
    assert _parse_telework(mod, "in-office") is None


# --- Salary edge cases ------------------------------------------------------

def test_non_annual_remuneration_returns_none_salary():
    mod = {
        "PositionID": "x", "PositionTitle": "x",
        "PositionRemuneration": [
            {"MinimumRange": "30", "MaximumRange": "45", "RateIntervalCode": "PH"},
        ],
        "UserArea": {"Details": {}},
    }
    p = parse_posting(mod)
    assert p.salary_min is None
    assert p.salary_max is None


def test_salary_picks_lowest_min_and_highest_max_across_entries():
    mod = {
        "PositionID": "x", "PositionTitle": "x",
        "PositionRemuneration": [
            {"MinimumRange": "60000", "MaximumRange": "78000", "RateIntervalCode": "PA"},
            {"MinimumRange": "50000", "MaximumRange": "70000", "RateIntervalCode": "PA"},
            {"MinimumRange": "90000", "MaximumRange": "110000", "RateIntervalCode": "PA"},
        ],
        "UserArea": {"Details": {}},
    }
    p = parse_posting(mod)
    assert p.salary_min == pytest.approx(50000.0 / 12.0)
    assert p.salary_max == pytest.approx(110000.0 / 12.0)
