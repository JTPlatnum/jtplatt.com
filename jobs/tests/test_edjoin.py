"""Tests for crawler/sources/edjoin.py — pure parsers against fixtures, plus
synthetic dedup / pagination tests for the Source orchestrator. No live HTTP.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest
from bs4 import BeautifulSoup

from crawler.base import Posting
from crawler.sources import edjoin as ej
from crawler.sources.edjoin import (
    EdJoinSource,
    _build_raw_text,
    _extract_jsonld_jobposting,
    _parse_microsoft_json_date,
    _parse_salary,
    _telework_from_text,
    parse_posting,
    parse_search_results,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_HTML = (FIXTURE_DIR / "edjoin_sample.html").read_text()
SAMPLE_JSON = (FIXTURE_DIR / "edjoin_loadjobs_sample.json").read_text()

# Fixture postingID — Palo Alto Unified Elementary Teacher - 2nd Grade.
SAMPLE_POSTING_ID = "2232159"


# --- parse_search_results --------------------------------------------------

def test_parse_search_results_extracts_required_fields():
    records = parse_search_results(SAMPLE_JSON)
    assert len(records) > 0
    r = records[0]
    # Locked field set (recon §11.6 + parse_search_results docstring)
    expected = {
        "postingID", "positionTitle", "districtName", "city", "countyName",
        "postingDate", "PayRangeFrom", "PayRangeTo", "beginningSalary",
        "endingSalary", "salaryInfo", "JobSummary", "jobType", "FullTimePartTime",
    }
    assert expected.issubset(set(r.keys()))


def test_parse_search_results_postingID_preserved():
    records = parse_search_results(SAMPLE_JSON)
    # postingIDs should be ints (preserved from the API)
    pids = [r["postingID"] for r in records if r["postingID"] is not None]
    assert all(isinstance(pid, int) for pid in pids)
    assert len(pids) == len(records)  # every record has a postingID


def test_parse_search_results_empty_data():
    out = parse_search_results('{"data": [], "totalRecords": 0}')
    assert out == []


def test_parse_search_results_missing_data_key():
    out = parse_search_results('{"totalRecords": 0}')
    assert out == []


def test_parse_search_results_malformed_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_search_results("not json")


# --- _parse_microsoft_json_date --------------------------------------------

def test_parse_ms_date_happy_path():
    # 1780444800000 ms = 2026-06-03 00:00:00 UTC
    assert _parse_microsoft_json_date("/Date(1780444800000)/") == date(2026, 6, 3)


def test_parse_ms_date_sentinel_returns_none():
    assert _parse_microsoft_json_date("/Date(-62135568000000)/") is None


def test_parse_ms_date_malformed_returns_none():
    assert _parse_microsoft_json_date("2026-06-03") is None
    assert _parse_microsoft_json_date("/Date(abc)/") is None
    assert _parse_microsoft_json_date("") is None
    assert _parse_microsoft_json_date(None) is None  # type: ignore[arg-type]


def test_parse_ms_date_unreasonably_large_returns_none():
    # Numbers near sys.maxsize overflow datetime.fromtimestamp
    assert _parse_microsoft_json_date("/Date(99999999999999999999)/") is None


# --- _parse_salary (one per pattern + edges) -------------------------------

def test_parse_salary_monthly_range():
    assert _parse_salary("$5,000 – $7,500 per month") == (5000, 7500)


def test_parse_salary_annual_range_converts_to_monthly():
    # $60k - $84k annually → $5k - $7k monthly
    assert _parse_salary("$60,000 – $84,000 annually") == (5000, 7000)


def test_parse_salary_annual_alt_phrasings():
    # All three trailing phrasings work: "annually", "annual", "per year"
    assert _parse_salary("$60,000 – $84,000 per year") == (5000, 7000)
    assert _parse_salary("$60,000 – $84,000 annual") == (5000, 7000)


def test_parse_salary_hourly_returns_none():
    assert _parse_salary("$25.00 – $40.00 per hour") == (None, None)


def test_parse_salary_placement_schedule_returns_none():
    assert _parse_salary("Placement on Teachers Salary Schedule") == (None, None)


def test_parse_salary_dependent_returns_none():
    assert _parse_salary("Pay dependent on experience") == (None, None)


def test_parse_salary_no_match_returns_none():
    assert _parse_salary("Something with no salary info at all") == (None, None)


def test_parse_salary_empty_string_returns_none():
    assert _parse_salary("") == (None, None)


def test_parse_salary_priority_monthly_beats_annual():
    """Pattern 1 (monthly) wins over Pattern 2 (annual) when both present."""
    text = "$5,000 – $7,500 per month or $60,000 – $90,000 annually"
    assert _parse_salary(text) == (5000, 7500)


# --- _extract_jsonld_jobposting --------------------------------------------

def test_jsonld_present_returns_dict():
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org/", "@type": "JobPosting", "title": "T"}
    </script>
    """
    soup = BeautifulSoup(html, "lxml")
    d = _extract_jsonld_jobposting(soup)
    assert d is not None
    assert d.get("title") == "T"


def test_jsonld_absent_returns_none():
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    assert _extract_jsonld_jobposting(soup) is None


def test_jsonld_malformed_returns_none():
    html = '<script type="application/ld+json">{ malformed</script>'
    soup = BeautifulSoup(html, "lxml")
    assert _extract_jsonld_jobposting(soup) is None


def test_jsonld_wrong_type_returns_none():
    html = '<script type="application/ld+json">{"@type": "Organization"}</script>'
    soup = BeautifulSoup(html, "lxml")
    assert _extract_jsonld_jobposting(soup) is None


def test_jsonld_picks_jobposting_among_multiple_blocks():
    html = """
    <script type="application/ld+json">{"@type": "Organization", "name": "x"}</script>
    <script type="application/ld+json">{"@type": "JobPosting", "title": "right"}</script>
    """
    soup = BeautifulSoup(html, "lxml")
    d = _extract_jsonld_jobposting(soup)
    assert d is not None
    assert d.get("title") == "right"


# --- _telework_from_text ---------------------------------------------------

def test_telework_remote():
    assert _telework_from_text("This is a remote position") is True


def test_telework_virtual():
    assert _telework_from_text("Virtual teaching role") is True


def test_telework_hybrid():
    assert _telework_from_text("Hybrid schedule available") is True


def test_telework_telework():
    assert _telework_from_text("Telework eligible 3 days/week") is True


def test_telework_no_match_default_false():
    assert _telework_from_text("In-person classroom teaching") is False


def test_telework_empty_returns_false():
    assert _telework_from_text("") is False


# --- parse_posting precedence chain (recon §11.6) --------------------------

def test_parse_posting_uses_listing_api_when_present():
    listing = {
        "postingID": 2232159,
        "positionTitle": "Elementary Teacher - 2nd Grade",
        "districtName": "Palo Alto Unified School District",
        "city": "Palo Alto",
        "postingDate": "/Date(1780444800000)/",
    }
    p = parse_posting(SAMPLE_HTML, listing)
    assert p.source == "edjoin"
    assert p.source_job_id == "2232159"
    assert p.title == "Elementary Teacher - 2nd Grade"
    assert p.employer == "Palo Alto Unified School District"
    assert p.url == f"https://www.edjoin.org/Home/JobPosting/{SAMPLE_POSTING_ID}"
    assert p.posted_date == date(2026, 6, 3)
    assert p.classification is None


def test_parse_posting_falls_back_to_jsonld_when_listing_missing():
    # Empty listing forces JSON-LD fallback (postingID 2232159 has JSON-LD)
    p = parse_posting(SAMPLE_HTML, {})
    # Title from JSON-LD (`title` field)
    assert p.title == "Elementary Teacher - 2nd Grade"
    # postingID from JSON-LD identifier.value (= 2232159)
    assert p.source_job_id == "2232159"
    # Employer from JSON-LD hiringOrganization.name
    assert p.employer == "Palo Alto Unified School District"
    # posted_date from JSON-LD datePosted (`2026-06-03T07:00:00Z`)
    assert p.posted_date == date(2026, 6, 3)


def test_parse_posting_dom_h2_last_resort_for_title():
    """When neither listing-API nor JSON-LD has title, fall back to DOM <h2>."""
    html = "<html><body><h2>DOM Last-Resort Title</h2></body></html>"
    p = parse_posting(html, {})
    assert p.title == "DOM Last-Resort Title"


def test_parse_posting_employer_defaults_to_edjoin_when_all_absent():
    """When neither listing-API nor JSON-LD names an employer, default to
    'EdJoin' rather than crashing or emitting empty."""
    html = "<html><body></body></html>"
    p = parse_posting(html, {})
    assert p.employer == "EdJoin"


def test_parse_posting_all_locations_is_single_element_list():
    listing = {
        "postingID": 2232159,
        "positionTitle": "x",
        "districtName": "Palo Alto Unified School District",
        "city": "Palo Alto",
    }
    p = parse_posting(SAMPLE_HTML, listing)
    assert p.all_locations == ["Palo Alto, Palo Alto Unified School District"]
    assert p.location == "Palo Alto, Palo Alto Unified School District"


def test_parse_posting_url_empty_when_no_id():
    # No listing-API postingID and no JSON-LD identifier → empty url
    html = "<html><body></body></html>"
    p = parse_posting(html, {})
    assert p.source_job_id == ""
    assert p.url == ""


def test_parse_posting_salary_falls_through_to_pay_dependent_pattern():
    """The Palo Alto fixture has no listing-API PayRange and 'Pay dependent
    on experience' in the DOM body. Should resolve to (None, None) via
    Pattern 5."""
    p = parse_posting(SAMPLE_HTML, {"postingID": 2232159, "positionTitle": "x"})
    assert p.salary_min is None
    assert p.salary_max is None


def test_parse_posting_telework_false_for_in_person_posting():
    """Fixture is an in-person elementary teacher — no telework keywords
    should match."""
    p = parse_posting(SAMPLE_HTML, {"postingID": 2232159, "positionTitle": "x"})
    assert p.telework_flag is False


def test_parse_posting_raw_text_includes_body_sections():
    p = parse_posting(SAMPLE_HTML, {"postingID": 2232159, "positionTitle": "x"})
    # Job Summary section is concatenated with header marker
    assert "=== Job Summary ===" in p.raw_text
    # Substantive body content present
    assert "OVERVIEW" in p.raw_text
    # Short labeled field present
    assert "Date Posted:" in p.raw_text or "Date Posted" in p.raw_text


def test_parse_posting_classification_always_none():
    """EdJoin has no class-code system (locked §11.6)."""
    p = parse_posting(SAMPLE_HTML, {"postingID": 2232159, "positionTitle": "x"})
    assert p.classification is None


def test_parse_posting_listing_api_postingDate_wins_over_jsonld():
    """Precedence verification: listing-API postingDate should beat JSON-LD
    datePosted even when both are present and different."""
    # Listing says day before; JSON-LD on the page says 2026-06-03
    listing = {
        "postingID": 2232159, "positionTitle": "x",
        "postingDate": "/Date(1780358400000)/",  # 2026-06-02
    }
    p = parse_posting(SAMPLE_HTML, listing)
    assert p.posted_date == date(2026, 6, 2)


# --- Source orchestration: dedup + pagination ------------------------------

def test_fetch_listings_dedups_across_queries(monkeypatch):
    """A postingID matching multiple queries should be detail-fetched and
    yielded exactly once (locked §11.2 — dedup at fetch_listings level)."""
    source = EdJoinSource()

    # Two queries — second one overlaps postingID 200 with the first
    query_to_records: Dict[int, List[Dict[str, Any]]] = {
        0: [{"postingID": 100, "positionTitle": "T1"},
            {"postingID": 200, "positionTitle": "T2"}],
        1: [{"postingID": 200, "positionTitle": "T2-dup"},
            {"postingID": 300, "positionTitle": "T3"}],
    }
    call_counter = {"n": 0}

    def fake_fetch_all(base_query):
        idx = call_counter["n"]
        call_counter["n"] += 1
        return query_to_records.get(idx, [])

    monkeypatch.setattr(source, "_fetch_all_listings", fake_fetch_all)

    detail_calls: List[str] = []

    def fake_fetch_detail(pid):
        detail_calls.append(pid)
        return "<html><body></body></html>"

    monkeypatch.setattr(source, "_fetch_detail", fake_fetch_detail)

    def fake_parse(html, fields):
        return Posting(
            source="edjoin",
            source_job_id=str(fields.get("postingID")),
            title=str(fields.get("positionTitle") or ""),
            employer="X",
            url="",
            raw_text="",
            classification=None,
        )

    monkeypatch.setattr(ej, "parse_posting", fake_parse)

    results = list(source.fetch_listings([{"keywords": "q1"}, {"keywords": "q2"}]))

    assert sorted(r.source_job_id for r in results) == ["100", "200", "300"]
    assert sorted(detail_calls) == ["100", "200", "300"]
    assert len(detail_calls) == 3   # NOT 4 — postingID 200 dedup'd


def test_fetch_all_listings_stops_when_page_returns_lt_50(monkeypatch):
    """Pagination stop condition: stop when a page returns fewer than
    rows=50 records (locked §11.7)."""
    monkeypatch.setattr(ej.time, "sleep", lambda s: None)
    source = EdJoinSource()

    pages: List[Dict[str, Any]] = [
        # Page 1: full 50 → continue
        {"records": [{"postingID": i} for i in range(1, 51)], "total": 80},
        # Page 2: 30 (< 50) → stop after this
        {"records": [{"postingID": i} for i in range(51, 81)], "total": 80},
    ]
    call_count = {"n": 0}

    def fake_page(query_url):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(pages):
            return pages[idx]["records"], pages[idx]["total"]
        return [], 0

    monkeypatch.setattr(source, "_fetch_listings_page", fake_page)
    out = source._fetch_all_listings({"keywords": "x"})
    assert len(out) == 80
    assert call_count["n"] == 2  # stopped after exactly 2 calls


def test_fetch_all_listings_hits_20_page_safety_cap(monkeypatch):
    """If every page returns the full 50, the safety cap stops at 20 pages."""
    monkeypatch.setattr(ej.time, "sleep", lambda s: None)
    source = EdJoinSource()
    call_count = {"n": 0}

    def fake_page(query_url):
        call_count["n"] += 1
        return [{"postingID": i} for i in range(50)], 9999

    monkeypatch.setattr(source, "_fetch_listings_page", fake_page)
    out = source._fetch_all_listings({"keywords": "x"})
    assert call_count["n"] == ej.PAGE_SAFETY_CAP
    assert len(out) == ej.PAGE_SAFETY_CAP * ej.ROWS_PER_PAGE


def test_query_to_url_merges_defaults():
    """All LoadJobs defaults must be present (recon §11.2 — .NET 500s on
    missing params). Override with query-config values."""
    source = EdJoinSource()
    url = source._query_to_url({"keywords": "CTE", "jobTypes": "0"})
    # Required defaults present
    assert "rows=50" in url
    assert "sort=postingDate" in url
    assert "stateID=0" in url
    assert "searchType=all" in url
    # Override applied
    assert "keywords=CTE" in url
