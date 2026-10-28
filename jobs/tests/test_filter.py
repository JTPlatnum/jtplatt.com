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


def test_edjoin_source_deactivated_rejects(monkeypatch):
    # EdJoin DEACTIVATED 2026-07-08 (LoadJobs endpoint decommissioned; source
    # needs rebuild). Removed from ALLOWED_SOURCES so any stale edjoin rows in
    # jobs.db are rejected rather than surfaced. Restore the "passes" assertion
    # when the source is rebuilt and re-added to the allow-list.
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = filter_mod.should_keep(_posting(source="edjoin"))
    assert ok is False
    assert reason.startswith("unknown source")


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


def test_hybrid_telework_off_list_rejects(monkeypatch):
    """telework=True reads as hybrid; a hybrid office outside target
    locations is rejected (changed 2026-10-28 lane rewrite)."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=True,
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia"],
    )
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "outside target locations"


def test_fully_remote_passes_when_location_off_list(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        telework_flag=True,
        raw_text="This is a fully remote position.",
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia"],
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_explicit_remote_type_hybrid_overrides_text(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(
        remote_type="hybrid",
        raw_text="fully remote",
        location="Atlanta, Georgia",
        all_locations=["Atlanta, Georgia"],
    )
    ok, _ = filter_mod.should_keep(p)
    assert ok is False


@pytest.mark.parametrize("loc", [
    "Santa Cruz, California", "Monterey, California", "Honolulu, Hawaii",
    "Truckee, California", "Oceanside, California", "San Marcos, California",
    "Placer County", "El Dorado Hills, CA",
])
def test_home_region_locations_pass(monkeypatch, loc):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = filter_mod.should_keep(
        _posting(telework_flag=False, location=loc, all_locations=[loc]))
    assert ok is True


# --- Rule 6: premium metros (SF / NYC) --------------------------------------

def _metro(loc: str, salary: float | None) -> Posting:
    return _posting(telework_flag=False, location=loc, all_locations=[loc],
                    salary_min=salary, salary_max=salary)


def test_sf_passes_at_premium(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR_PREMIUM_METRO", 12500.0)
    ok, _ = filter_mod.should_keep(_metro("San Francisco, California", 13000.0))
    assert ok is True


def test_nyc_below_premium_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR_PREMIUM_METRO", 12500.0)
    ok, reason = filter_mod.should_keep(_metro("New York, NY", 10000.0))
    assert ok is False
    assert reason == "premium metro: below premium floor"


def test_premium_metro_unverified_pay_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR_PREMIUM_METRO", 12500.0)
    ok, reason = filter_mod.should_keep(_metro("San Francisco, CA", None))
    assert ok is False
    assert reason == "premium metro: pay unverified"


def test_premium_metro_unset_env_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR_PREMIUM_METRO", None)
    ok, reason = filter_mod.should_keep(_metro("San Francisco, CA", 20000.0))
    assert ok is False
    assert "not set" in reason


def test_new_york_state_is_not_premium_metro(monkeypatch):
    """'Albany, New York' is upstate, not NYC — plain off-list reject."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR_PREMIUM_METRO", 12500.0)
    ok, reason = filter_mod.should_keep(_metro("Albany, New York", 20000.0))
    assert ok is False
    assert reason == "outside target locations"


# --- Rule 6: international ----------------------------------------------------

def _intl(**kw) -> Posting:
    base = dict(telework_flag=False, location="Hanoi, Vietnam",
                all_locations=["Hanoi, Vietnam"])
    base.update(kw)
    return _posting(**base)


def test_100k_dong_fails_floor_after_conversion(monkeypatch):
    """The canonical case: 100,000 VND/month converts to ~$4 and fails."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    p = _intl(salary_min=100_000, salary_max=100_000, currency="VND", pay_period="monthly")
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "below salary floor"


def test_international_with_verified_pay_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    p = _intl(salary_min=110_000, salary_max=130_000, currency="EUR", pay_period="annual",
              location="Berlin, Germany", all_locations=["Berlin, Germany"])
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_international_unverified_pay_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_intl(salary_min=None, salary_max=None))
    assert ok is False
    assert reason == "international: pay unverified"


def test_unknown_currency_home_location_passes_unverified(monkeypatch):
    """Unknown currency -> salary cleared, pay unverified, let through."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    p = _posting(salary_min=500.0, salary_max=600.0, currency="ARS")
    assert p.salary_min is None
    assert p.pay_note == "unknown currency 'ARS'"
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


# --- Rule 1: sneaker-lane floor ------------------------------------------------

def _sneaker(**kw) -> Posting:
    base = dict(title="Product Line Manager", employer="Nike",
                raw_text="Own the footwear product line.", telework_flag=False,
                remote_type="full")
    base.update(kw)
    return _posting(**base)


def test_sneaker_below_100k_rejects_even_above_normal_floor(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, reason = filter_mod.should_keep(_sneaker(salary_min=8000.0))
    assert ok is False
    assert reason == "below sneaker-lane floor"


def test_sneaker_at_100k_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, _ = filter_mod.should_keep(_sneaker(salary_min=100_000 / 12))
    assert ok is True


def test_sneaker_unverified_pay_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 7000.0)
    ok, _ = filter_mod.should_keep(_sneaker(salary_min=None, salary_max=None))
    assert ok is True


# --- Rule 5: authentication ------------------------------------------------------

def test_bench_authenticator_unverified_pay_rejects(monkeypatch):
    """On-site, non-supervisory, no posted pay: rule 1 can't catch it
    (unverified passes), so rule 5 does."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _sneaker(title="Sneaker Authenticator", remote_type=None,
                 location="Sacramento, California",
                 salary_min=None, salary_max=None)
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason.startswith("authentication role")


def test_bench_authenticator_low_pay_rejects_at_floor(monkeypatch):
    """Verified low pay is caught earlier by the sneaker-lane floor."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _sneaker(title="Sneaker Authenticator", remote_type=None,
                 salary_min=3500.0, salary_max=4000.0)
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason == "below sneaker-lane floor"


def test_remote_authenticator_without_verified_pay_rejects(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _sneaker(title="Authenticator", salary_min=None, salary_max=None)
    ok, reason = filter_mod.should_keep(p)
    assert ok is False
    assert reason.startswith("authentication role")


def test_authentication_manager_at_100k_passes(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _sneaker(title="Authentication Operations Manager", remote_type=None,
                 location="Sacramento, California",
                 salary_min=10_000.0, salary_max=12_000.0)
    ok, _ = filter_mod.should_keep(p)
    assert ok is True


def test_it_authentication_engineer_not_caught_by_sneaker_rule(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    p = _posting(title="Identity and Authentication Specialist")
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
