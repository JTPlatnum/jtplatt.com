"""Tests for crawler/sources/csu.py — parsing only. No live HTTP."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from crawler.base import Posting
from crawler.sources.csu import (
    _campus_to_employer,
    _extract_classification,
    _parse_salary,
    _telework_from_categories,
    parse_posting,
    parse_search_results,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "csu_sample.html"

SAMPLE_URL = (
    "https://csucareers.calstate.edu/en-us/job/557129/"
    "information-security-analyst-information-security-analyst-ii"
)


@pytest.fixture
def sample_html() -> str:
    return FIXTURE.read_text()


# --- Fixture-driven assertions (jcid 557129) -------------------------------

def test_parses_to_posting(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert isinstance(p, Posting)
    assert p.source == "csu"


def test_source_job_id(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.source_job_id == "557129"


def test_title_contains_working_title(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert "Information Security Analyst" in p.title


def test_classification_parsed_from_title_parenthetical(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.classification == "Information Security Analyst II"


def test_posted_date_parsed_from_time_datetime(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.posted_date == date(2026, 5, 15)


def test_telework_flag_true_from_categories(sample_html):
    # Categories contains "Telecommute eligible (...)" — Decision #6 says
    # categories is the structured source of truth.
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.telework_flag is True


def test_employer_derived_from_san_diego_campus(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.employer == "San Diego State University"


def test_location_and_all_locations(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.location == "San Diego"
    assert p.all_locations == ["San Diego"]


def test_salary_uses_csu_classification_pattern(sample_html):
    # Body has both "Initial step placement is not expected to exceed Step 1
    # ($6,492/month)" AND "CSU Classification Salary Range: $6,492-$9,458 per
    # month". Per Decision #5 priority, the CSU classification range (pattern 3)
    # wins over the step-1 ceiling (pattern 4).
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.salary_min == 6492.0
    assert p.salary_max == 9458.0


def test_raw_text_nonempty_with_body_and_categories(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.raw_text
    assert "=== Classification ===" in p.raw_text
    assert "=== Categories ===" in p.raw_text
    assert "=== Position Details ===" in p.raw_text
    assert "Position Summary" in p.raw_text


def test_url_passed_through(sample_html):
    p = parse_posting(sample_html, url=SAMPLE_URL)
    assert p.url == SAMPLE_URL


# --- Salary pattern priority (Decision #5) ---------------------------------

def test_salary_pattern1_anticipated_monthly_wins_over_csu_classification():
    text = (
        "Anticipated Salary Range: $5,025 – $9,425 per month. "
        "CSU Classification Salary Range: $4,000-$10,000 per month."
    )
    assert _parse_salary(text) == (5025.0, 9425.0)


def test_salary_pattern2_annual_converted_to_monthly():
    text = "$92,000 – $116,000 annually"
    lo, hi = _parse_salary(text)
    assert lo == pytest.approx(92000 / 12)
    assert hi == pytest.approx(116000 / 12)


def test_salary_pattern3_csu_classification_monthly():
    text = "CSU Classification Salary Range: $6,492-$9,458 per month (Step 1-Step 20)."
    assert _parse_salary(text) == (6492.0, 9458.0)


def test_salary_pattern4_step1_ceiling_min_only():
    text = "Initial step placement is not expected to exceed Step 1 ($6,492/month)."
    lo, hi = _parse_salary(text)
    assert lo == 6492.0
    assert hi is None


def test_salary_pattern5_hourly_returns_none():
    text = "$25.00 – $35.00 per hour"
    assert _parse_salary(text) == (None, None)


def test_salary_pattern6_commensurate_returns_none():
    text = "Salary commensurate with experience based on qualifications."
    assert _parse_salary(text) == (None, None)


def test_salary_no_pattern_match_returns_none():
    text = "This posting has no salary information whatsoever."
    assert _parse_salary(text) == (None, None)


def test_salary_step1_alone_returns_min_only():
    # Pattern 4 fires alone when no higher-priority pattern matches.
    text = "Initial step placement is not expected to exceed Step 1 ($7,000/month)."
    assert _parse_salary(text) == (7000.0, None)


# --- Classification edge cases ---------------------------------------------

def test_classification_none_when_no_parenthetical():
    assert _extract_classification("Instructional Designer") is None


def test_classification_strips_whitespace():
    assert (
        _extract_classification("Foo (  Information Technology Consultant - Career  )")
        == "Information Technology Consultant - Career"
    )


def test_classification_empty_title_returns_none():
    assert _extract_classification("") is None


# --- Telework edge cases ---------------------------------------------------

def test_telework_categories_telecommute_eligible():
    assert _telework_from_categories(
        "Unit 9, Full Time, Telecommute eligible (work onsite as scheduled)"
    ) is True


def test_telework_categories_on_site_returns_false():
    assert _telework_from_categories(
        "Unit 9, Full Time, On-site (work in-person at business location)"
    ) is False


def test_telework_categories_remote_in_state():
    assert _telework_from_categories(
        "Staff, Probationary, Remote in-state eligible (long distance work)"
    ) is True


def test_telework_categories_remote_out_of_state():
    assert _telework_from_categories(
        "Staff, Remote out-of-state eligible (long distance work)"
    ) is True


def test_telework_empty_returns_false():
    assert _telework_from_categories("") is False


# --- Campus → employer mapping ---------------------------------------------

def test_campus_san_marcos_maps_to_csusm():
    assert (
        _campus_to_employer("San Marcos")
        == "California State University San Marcos"
    )


def test_campus_monterey_bay_maps_correctly():
    assert (
        _campus_to_employer("Monterey Bay")
        == "California State University, Monterey Bay"
    )


def test_campus_unknown_falls_back_to_default():
    assert _campus_to_employer("Unknown Campus") == "California State University"


def test_campus_none_falls_back_to_default():
    assert _campus_to_employer(None) == "California State University"


# --- Search-results parsing ------------------------------------------------

def test_parse_search_results_dedups_by_jcid():
    html = """
    <html><body>
        <a class="job-link" href="/en-us/job/100/foo">Foo</a>
        <a class="job-link" href="/en-us/job/100/foo-again">Foo duplicate (sidebar)</a>
        <a class="job-link" href="/en-us/job/200/bar">Bar</a>
    </body></html>
    """
    out = parse_search_results(html)
    assert [jcid for jcid, _ in out] == ["100", "200"]


def test_parse_search_results_ignores_non_job_anchors():
    html = """
    <html><body>
        <a class="job-link" href="/en-us/job/300/qux">Qux</a>
        <a class="job-link" href="/about/contact">Not a job</a>
        <a class="other" href="/en-us/job/400/zzz">Not a job-link class</a>
    </body></html>
    """
    out = parse_search_results(html)
    assert [jcid for jcid, _ in out] == ["300"]


def test_parse_search_results_empty():
    assert parse_search_results("<html><body></body></html>") == []
