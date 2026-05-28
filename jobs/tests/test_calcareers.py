"""Tests for crawler/sources/calcareers.py — parsing only. No live Playwright."""
from pathlib import Path

import pytest

from crawler.base import Posting
from crawler.sources.calcareers import (
    _normalize_classification,
    _parse_salary,
    _parse_telework,
    parse_posting,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "calcareers_sample.html"


@pytest.fixture
def sample_html() -> str:
    return FIXTURE.read_text()


# --- Fixture-driven posting assertions -------------------------------------

def test_parses_to_posting(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert isinstance(p, Posting)
    assert p.source == "calcareers"


def test_title_is_working_title(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.title == ".Net Developer"


def test_classification_is_title_cased(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.classification == "Information Technology Specialist I"


def test_employer(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.employer == "Department of General Services"


def test_salary_already_monthly(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.salary_min == 6513.0
    assert p.salary_max == 8729.0


def test_location(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.location == "Yolo County"


def test_all_locations_is_single_element_list(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.all_locations == ["Yolo County"]


def test_telework_hybrid_resolves_true(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.telework_flag is True


def test_source_job_id_keeps_jc_prefix(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.source_job_id == "JC-505623"


def test_url_is_web_view_with_integer_id(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert "JobPosting.aspx?JobControlId=505623" in p.url
    assert "JC-" not in p.url
    assert "Print" not in p.url


def test_posted_date_is_none(sample_html):
    p = parse_posting(sample_html, jcid=505623)
    assert p.posted_date is None


def test_raw_text_includes_all_six_panels_and_classification(sample_html):
    # Minimum Requirements is intentionally absent — the print page punts that
    # content to the separate class-spec page (see notes/calcareers-recon.md §2
    # corrections). Class-spec fetching is deferred to v1.1.
    p = parse_posting(sample_html, jcid=505623)
    assert "=== Classification ===" in p.raw_text
    assert "Information Technology Specialist I" in p.raw_text
    assert "=== Minimum Requirements ===" not in p.raw_text
    for header in (
        "=== Job Description and Duties ===",
        "=== Working Conditions ===",
        "=== Position Details ===",
        "=== Department Information ===",
        "=== Special Requirements ===",
        "=== Desirable Qualifications ===",
    ):
        assert header in p.raw_text, f"missing section header {header!r}"


# --- Helper / unit tests ---------------------------------------------------

def test_normalize_classification_preserves_roman_numerals():
    assert _normalize_classification("INFORMATION TECHNOLOGY SPECIALIST I") == "Information Technology Specialist I"
    assert _normalize_classification("INFORMATION TECHNOLOGY SPECIALIST II") == "Information Technology Specialist II"
    assert _normalize_classification("STAFF SERVICES ANALYST") == "Staff Services Analyst"


def test_normalize_classification_handles_empty():
    assert _normalize_classification("") == ""


def test_salary_parses_monthly_band():
    lo, hi = _parse_salary("$6,513.00 - $8,729.00 per Month")
    assert lo == 6513.0
    assert hi == 8729.0


def test_salary_handles_per_month_case_insensitive():
    lo, hi = _parse_salary("$5,000.00 - $7,500.00 PER MONTH")
    assert lo == 5000.0
    assert hi == 7500.0


def test_salary_non_monthly_returns_none():
    assert _parse_salary("$78,156.00 - $104,748.00 per Year") == (None, None)
    assert _parse_salary("$25.00 - $35.00 per Hour") == (None, None)


def test_salary_empty_returns_none():
    assert _parse_salary("") == (None, None)


def test_telework_yes_returns_true():
    assert _parse_telework("Yes", "") is True


def test_telework_hybrid_returns_true():
    assert _parse_telework("Hybrid", "") is True


def test_telework_no_without_keyword_returns_false():
    assert _parse_telework("No", "in-office only role") is False


def test_telework_no_overridden_by_raw_text_keyword():
    assert _parse_telework("No", "Fully remote position.") is True


def test_telework_empty_keyword_fallback_yes():
    assert _parse_telework("", "Hybrid schedule available.") is True


def test_telework_empty_and_no_keyword_returns_none():
    assert _parse_telework("", "Standard on-site role.") is None
