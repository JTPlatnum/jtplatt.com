"""Tests for estimate.py (title-level salary estimates) and the estimate-based
sneaker-lane floor in filter.should_keep."""
from __future__ import annotations

import pytest

import estimate
import filter as filter_mod
from crawler.base import Posting


# --- Level parsing ------------------------------------------------------------

@pytest.mark.parametrize("title,level,band", [
    ("Associate Product Manager - Topps Trading Cards", "associate", (75000, 105000)),
    ("Footwear Merchandising Associate", "associate", (75000, 105000)),
    ("Associate Director, Brand Marketing", "director", (200000, 310000)),
    ("Professional, Footwear Development", "professional", (90000, 120000)),
    ("Senior Merchant, Boys Run & Train Apparel", "senior_professional", (110000, 140000)),
    ("Senior, Costing Analyst, Local Creation GC", "senior_professional", (110000, 140000)),
    ("Sr. Product Developer", "senior_professional", (110000, 140000)),
    ("Lead, Footwear Costing Data & Operations", "lead", (125000, 180000)),
    ("LEAD ASSORTMENT PLANNER, INDONESIA", "lead", (125000, 180000)),
    ("Lead Technical Product Manager, ITC", "lead", (125000, 180000)),
    ("Principal Engineer, Security & Compliance", "lead", (125000, 180000)),
    ("Expert Designer, Basketball Footwear Product Design", "lead", (125000, 180000)),
    ("Product Specialist II", "lead", (125000, 180000)),
    ("Manager, Footwear Cost Engineering", "manager", (140000, 200000)),
    ("Software Engineer Manager - Merchandising", "manager", (140000, 200000)),
    ("Senior Manager, Material Development", "senior_manager", (170000, 230000)),
    ("Sr Manager Apparel Technical Design", "senior_manager", (170000, 230000)),
    ("Director, Global Energy Footwear Product Management", "director", (200000, 310000)),
    ("Director Innovation Footwear", "director", (200000, 310000)),
    ("Senior Director, Apparel Local Creation GC", "senior_director_vp", (280000, 420000)),
    ("VP, Global Footwear", "senior_director_vp", (280000, 420000)),
    ("Vice President, Product Merchandising", "senior_director_vp", (280000, 420000)),
])
def test_level_parsing_maps_to_band(title, level, band):
    assert estimate.detect_level(title) == level
    assert estimate.estimate_for_title(title) == band


@pytest.mark.parametrize("title", [
    "Operations Specialist", "Customs Analyst", "Maintenance Coach", "", None,
])
def test_unreadable_level_uses_conservative_fallback(title):
    assert estimate.detect_level(title) == "fallback"
    assert estimate.estimate_for_title(title) == (90000, 130000)


def test_word_boundaries_avoid_false_levels():
    # "leader"/"leadership" are not "lead"; "senior" inside a word is not senior.
    assert estimate.detect_level("Footwear Leadership Program") == "fallback"


# --- apply_estimate -------------------------------------------------------------

def _nike(**overrides) -> Posting:
    defaults = dict(
        source="nike", source_job_id="R-1", title="Director, Footwear Product",
        employer="NIKE, Inc.", url="https://example.test/R-1",
        raw_text="Nike footwear sneaker merchandising.",
        location="Beaverton, Oregon", all_locations=["Beaverton, Oregon"],
        telework_flag=False,
    )
    defaults.update(overrides)
    return Posting(**defaults)


def test_apply_estimate_sets_fields_on_unverified_lane1():
    p = _nike()
    estimate.apply_estimate(p)
    assert (p.estimate_min, p.estimate_max) == (200000, 310000)
    assert p.estimate_source == "level_table"


def test_apply_estimate_skips_verified_pay():
    p = _nike(salary_min=15000.0, salary_max=20000.0)
    estimate.apply_estimate(p)
    assert p.estimate_min is None and p.estimate_source is None


def test_apply_estimate_skips_other_lanes():
    p = Posting(source="usajobs", source_job_id="X", title="Senior Manager, IT",
                employer="Dept", url="https://example.test/X", raw_text="",
                location="Sacramento, California")
    estimate.apply_estimate(p)
    assert p.estimate_min is None and p.estimate_source is None


def test_apply_estimate_clears_stale_estimate():
    p = _nike(estimate_min=1.0, estimate_max=2.0, estimate_source="level_table",
              salary_min=15000.0)
    estimate.apply_estimate(p)
    assert (p.estimate_min, p.estimate_max, p.estimate_source) == (None, None, None)


# --- Estimate-based sneaker floor --------------------------------------------------

def _filtered(p: Posting):
    estimate.apply_estimate(p)
    return filter_mod.should_keep(p)


def test_low_estimate_dropped(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = _filtered(_nike(title="Product Operations Specialist"))  # fallback $90k
    assert ok is False
    assert reason == "below sneaker-lane floor (estimated)"


def test_professional_estimate_dropped(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = _filtered(_nike(title="Professional, Footwear Product"))  # $90k min
    assert ok is False


def test_high_estimate_kept(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = _filtered(_nike(title="Senior Product Merchant"))  # $110k min
    assert (ok, reason) == (True, "")


def test_verified_pay_used_instead_of_estimate(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    # Fallback estimate would drop it; verified $120k/yr keeps it.
    ok, _ = _filtered(_nike(title="Product Operations Specialist",
                            salary_min=10000.0, salary_max=12000.0))
    assert ok is True
    # Director estimate would keep it; verified $60k/yr drops it.
    ok, reason = _filtered(_nike(title="Director, Footwear Product",
                                 salary_min=5000.0, salary_max=6000.0))
    assert (ok, reason) == (False, "below sneaker-lane floor")


def test_no_estimate_means_unverified_still_passes(monkeypatch):
    """Without apply_estimate (estimate_min None), rule 1 behaves as before."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = filter_mod.should_keep(_nike(title="Product Operations Specialist"))
    assert ok is True


# --- Overseas: local-hire marker vs expat ----------------------------------------

SHANGHAI = dict(location="Shanghai, China Mainland", all_locations=["Shanghai, China Mainland"])


@pytest.mark.parametrize("title,expected", [
    ("Senior, Costing Analyst, Local Creation GC", True),
    ("Digital Merch Lead, GC Local", True),
    ("Local Marketing Lead", True),
    ("Lead Technical Product Manager, ITC", True),
    ("Director of Cyber Defense Operations , ITC", True),
    ("Pitch Product Lead", False),  # "itc" inside a word is not the token
    ("Senior Manager, Material Development", False),
    ("Locally Sourced Materials Lead", False),  # "locally" is not the token
    (None, False),
])
def test_local_hire_marker(title, expected):
    assert estimate.is_local_hire(title) is expected


def test_estimate_basis():
    assert estimate.estimate_basis(_nike()) == "us"
    assert estimate.estimate_basis(_nike(**SHANGHAI)) == "expat"
    assert estimate.estimate_basis(
        _nike(title="Senior Manager, Local Creation GC", **SHANGHAI)) == "local_hire"
    # A "local" title in the US is not discounted.
    assert estimate.estimate_basis(_nike(title="Local Creation Director")) == "us"


def test_overseas_without_marker_gets_full_us_band():
    p = _nike(title="Director, Footwear Product", **SHANGHAI)
    estimate.apply_estimate(p)
    assert (p.estimate_min, p.estimate_max) == (200000, 310000)


def test_local_hire_band_multiplied():
    p = _nike(title="Director, Footwear Product, Local Creation GC", **SHANGHAI)
    estimate.apply_estimate(p)
    assert (p.estimate_min, p.estimate_max) == pytest.approx((80000, 124000))


def test_us_local_title_keeps_full_band():
    p = _nike(title="Director, Local Creation Footwear Product")
    estimate.apply_estimate(p)
    assert (p.estimate_min, p.estimate_max) == (200000, 310000)


def test_overseas_expat_senior_kept_and_local_analyst_dropped(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    assert _filtered(_nike(title="Senior Manager, Footwear Product", **SHANGHAI)) == (True, "")
    ok, reason = _filtered(_nike(title="Senior, Footwear Product Analyst, Local Creation GC",
                                 **SHANGHAI))  # 110k x 0.4 = 44k
    assert (ok, reason) == (False, "below sneaker-lane floor (estimated)")


def test_local_hire_senior_director_survives(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, _ = _filtered(_nike(title="Senior Director, Footwear Product, Local Creation GC",
                            **SHANGHAI))  # 280k x 0.4 = 112k
    assert ok is True


def test_itc_overseas_lead_dropped(monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    ok, reason = _filtered(_nike(title="Lead Technical Product Manager, ITC",
                                 location="Karnataka, India",
                                 all_locations=["Karnataka, India"]))  # 125k x 0.4 = 50k
    assert (ok, reason) == (False, "below sneaker-lane floor (estimated)")
