"""Tests for filter.should_keep — hard pass/fail rules in order.

Salary floor is read from the SALARY_FLOOR env at filter.py import time, so
tests that need a specific floor patch `filter.SALARY_FLOOR` directly rather
than juggling env state across imports.
"""
from __future__ import annotations

import pytest

import filter as filter_mod
from crawler.base import Posting


def _posting(**overrides) -> Posting:
    """Build a baseline-passing posting; override only the fields under test."""
    defaults = dict(
        source="usajobs",
        source_job_id="abc-123",
        title="Information Technology Specialist",
        employer="Department of Defense",
        url="https://example.test/job/abc-123",
        raw_text="Standard duties; works closely with stakeholders.",
        salary_min=8000.0,
        salary_max=10000.0,
        location="Sacramento, California",
        all_locations=["Sacramento, California"],
        telework_flag=True,
        posted_date=None,
    )
    defaults.update(overrides)
    return Posting(**defaults)


# --- Rule 0: baseline pass --------------------------------------------------

def test_baseline_posting_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_posting())
    assert ok is True
    assert reason == ""


# --- Rule 1: salary floor ---------------------------------------------------

def test_salary_below_floor_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_posting(salary_min=5000.0))
    assert ok is False
    assert reason == "below salary floor"


def test_salary_equal_to_floor_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_posting(salary_min=7000.0))
    assert ok is True


def test_salary_none_passes(monkeypatch):
    """No salary data should not reject — only enforce when we know."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_posting(salary_min=None))
    assert ok is True


# --- Rule 2: current employer ----------------------------------------------

def test_fiscal_official_spelling_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = filter_mod.should_keep(_posting(employer="FI$CAL"))
    assert ok is False
    assert reason == "current employer"


def test_fiscal_informal_spelling_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = filter_mod.should_keep(_posting(employer="FISCal"))
    assert ok is False
    assert reason == "current employer"


def test_fiscal_case_insensitive_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = filter_mod.should_keep(_posting(employer="fiscal"))
    assert ok is False


# --- Rule 3: source allow-list ---------------------------------------------

def test_unknown_source_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = filter_mod.should_keep(_posting(source="linkedin"))
    assert ok is False
    assert reason.startswith("unknown source")


def test_calcareers_source_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = filter_mod.should_keep(_posting(source="calcareers"))
    assert ok is True


def test_csu_source_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = filter_mod.should_keep(_posting(source="csu"))
    assert ok is True


def test_edjoin_source_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = filter_mod.should_keep(_posting(source="edjoin"))
    assert ok is True


# --- Rule 4: disqualifying requirements ------------------------------------

def test_cissp_requirement_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(raw_text="Must hold an active CISSP certification.")
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "disqualifying: CISSP"


def test_ts_sci_requirement_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(raw_text="Requires active TS/SCI clearance with polygraph.")
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason.startswith("disqualifying:")


def test_masters_degree_phrase_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(raw_text="Master's degree in Computer Science required.")
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert "Master's degree in" in reason


def test_word_boundary_avoids_false_positive_on_pe(monkeypatch):
    """\\bPE\\b must not match 'people' or 'experience'."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(raw_text="Works with people; brings experience to the role.")
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


# --- Rule 5: location OR-logic ---------------------------------------------

def test_outside_target_locations_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=False,
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia"],
    )
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "outside target locations"


def test_telework_alone_passes_when_location_off_list(monkeypatch):
    """telework=True alone clears rule 5 even when location is unmatched."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=True,
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia"],
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_all_locations_match_alone_passes_when_telework_false(monkeypatch):
    """A single target-list hit in all_locations clears rule 5 with telework off."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=False,
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia", "Honolulu, Hawaii"],
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_location_fallback_used_when_all_locations_none(monkeypatch):
    """When all_locations is None, fall back to posting.location."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=False,
        location="Yolo County",
        all_locations=None,
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_target_location_substring_case_insensitive(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=False,
        location="south lake tahoe, ca",
        all_locations=["south lake tahoe, ca"],
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


# --- Rule ordering ----------------------------------------------------------

def test_salary_floor_wins_over_employer(monkeypatch):
    """Rules short-circuit in order. A FI$CAL posting under the floor reports
    the salary reason, not the employer reason."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 9000.0)
    p = _posting(employer="FI$CAL", salary_min=5000.0)
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "below salary floor"
