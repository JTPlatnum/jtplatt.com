"""Tests for score.py — Tier 1 rule-based scoring with lanes."""
from datetime import date, timedelta

from crawler.base import Posting
from score import score_posting


def _make(**kw) -> Posting:
    defaults = dict(
        source="test",
        source_job_id="job-1",
        title="Generic Role",
        employer="Test Employer",
        url="https://example.com/job",
        raw_text="",
    )
    defaults.update(kw)
    return Posting(**defaults)


# --- Shape ------------------------------------------------------------------

def test_returns_score_lane_and_components_keys():
    result = score_posting(_make(raw_text=""))
    assert set(result.keys()) == {"score", "lane", "components"}
    assert set(result["components"].keys()) == {
        "lane_fit", "background_match", "title_match",
        "pay", "location", "recency", "preferred_lock", "ai_resilience",
    }
    assert set(result["lane"].keys()) == {
        "lane", "name", "reason", "overseas_region", "remote_type",
    }


# --- Lane assignment ----------------------------------------------------------

def test_sneaker_lane_needs_domain_and_title_function():
    p = _make(title="Senior Manager, Footwear Merchandising", employer="Nike")
    assert score_posting(p)["lane"]["lane"] == 1


def test_operations_title_without_sneaker_domain_is_not_lane_1():
    p = _make(title="Operations Manager", employer="Dept of Water Resources",
              location="Sacramento, California")
    assert score_posting(p)["lane"]["lane"] != 1


def test_overseas_location_is_lane_2_with_region():
    p = _make(title="Program Analyst", all_locations=["Hanoi, Vietnam"])
    lane = score_posting(p)["lane"]
    assert lane["lane"] == 2
    assert lane["overseas_region"] == "southeast_asia"


def test_sneaker_overseas_stays_lane_1_but_flags_region():
    """Overseas applies across lanes: lane 1 wins, region still reported."""
    p = _make(title="Sourcing Manager", employer="Adidas",
              all_locations=["Herzogenaurach, Germany"])
    lane = score_posting(p)["lane"]
    assert lane["lane"] == 1
    assert lane["overseas_region"] == "europe"


def test_fully_remote_analyst_is_lane_3():
    p = _make(title="Business Systems Analyst", raw_text="This is a fully remote role.")
    assert score_posting(p)["lane"]["lane"] == 3


def test_hybrid_analyst_is_not_lane_3():
    p = _make(title="Business Systems Analyst",
              raw_text="Hybrid schedule, 2 days in office.",
              location="Sacramento, California")
    assert score_posting(p)["lane"]["lane"] != 3


def test_instructional_designer_is_lane_4():
    p = _make(title="Instructional Designer", location="Santa Cruz, California")
    assert score_posting(p)["lane"]["lane"] == 4


def test_its_ii_in_sacramento_is_lane_5():
    p = _make(title=".Net Developer", classification="Information Technology Specialist II",
              location="Sacramento, California")
    assert score_posting(p)["lane"]["lane"] == 5


def test_its_i_is_not_lane_5():
    """ITS I is lateral, not a step up."""
    p = _make(title=".Net Developer", classification="Information Technology Specialist I",
              location="Sacramento, California")
    assert score_posting(p)["lane"]["lane"] is None


def test_its_ii_outside_sacramento_is_not_lane_5():
    p = _make(classification="Information Technology Specialist II",
              location="Monterey, California")
    assert score_posting(p)["lane"]["lane"] is None


# --- Lane fit vs background (kept separate) ------------------------------------

def test_lane_keywords_do_not_count_as_background():
    """'footwear', 'merchandising' are lane keywords, not JT's skills."""
    p = _make(title="Footwear Merchandising Manager", employer="Nike",
              raw_text="Own footwear merchandising and supply chain.")
    comps = score_posting(p)["components"]
    assert "footwear" in comps["lane_fit"]["matched_lane_keywords"]
    assert "footwear" not in comps["background_match"]["matched_keywords"]
    assert "merchandising" not in comps["background_match"]["matched_keywords"]


def test_unlaned_gets_zero_lane_fit():
    assert score_posting(_make(title="Receptionist"))["components"]["lane_fit"]["points"] == 0


def test_lane_fit_caps_at_30():
    p = _make(
        title="Footwear Product Sourcing Manufacturing Quality Operations Manager",
        employer="Nike",
        raw_text="sneaker footwear collectibles trading cards merchandising "
                 "supply chain sourcing manufacturing factory quality control",
    )
    assert score_posting(p)["components"]["lane_fit"]["points"] == 30


def test_background_match_caps_at_20():
    text = " ".join([
        "BluePrism", "ServiceNow", "SharePoint", "WordPress", "Azure",
        "Oracle", "Agile", "RPA", "QA", "HTML", "CSS", "JavaScript",
        "Git", "Jira", "QuickBooks", "PowerSchool",
    ])
    assert score_posting(_make(raw_text=text))["components"]["background_match"]["points"] == 20


def test_background_word_boundary_prevents_false_positives():
    p = _make(raw_text="Strong qualifications required. Europa was a moon.")
    matched = score_posting(p)["components"]["background_match"]["matched_keywords"]
    assert "qa" not in matched
    assert "rpa" not in matched


def test_scoped_azure_matches_on_bare_token():
    p = _make(raw_text="Azure architect needed.")
    assert "azure" in score_posting(p)["components"]["background_match"]["matched_keywords"]


# --- Title patterns -------------------------------------------------------------

def test_yes_title_scores_plus_20():
    comp = score_posting(_make(title="Instructional Designer"))["components"]["title_match"]
    assert comp == {"points": 20, "matched": "Instructional Designer", "list": "yes"}


def test_its_i_no_longer_yes():
    comp = score_posting(_make(classification="Information Technology Specialist I"))["components"]["title_match"]
    assert comp["points"] == 0


def test_bare_it_specialist_no_longer_yes():
    comp = score_posting(_make(title="IT Specialist"))["components"]["title_match"]
    assert comp["points"] == 0


def test_no_title_penalizes():
    comp = score_posting(_make(title="Senior Software Engineer"))["components"]["title_match"]
    assert comp["points"] == -20
    assert comp["list"] == "no"


def test_no_title_wins_when_both_match():
    p = _make(title="Senior Software Engineer / IT Consultant (CSU)")
    assert score_posting(p)["components"]["title_match"]["points"] == -20


def test_no_in_classification_beats_yes_in_title():
    p = _make(title="Business Systems Analyst", classification="Senior Software Engineer")
    assert score_posting(p)["components"]["title_match"]["list"] == "no"


def test_classification_matches_yes_when_title_does_not():
    p = _make(title=".Net Developer", classification="Information Technology Specialist II")
    comp = score_posting(p)["components"]["title_match"]
    assert comp["points"] == 20
    assert comp["matched"] == "Information Technology Specialist II"


def test_both_fields_matching_yes_counts_once():
    p = _make(title="Business Systems Analyst",
              classification="Information Technology Specialist II")
    assert score_posting(p)["components"]["title_match"]["points"] == 20


def test_title_word_boundary_avoids_substring_collision():
    p = _make(title="Information Technology Specialist III")
    assert score_posting(p)["components"]["title_match"]["matched"] != "Information Technology Specialist II"


# --- Pay ------------------------------------------------------------------------

def test_pay_strong_at_100k():
    p = _make(salary_min=100_000 / 12)
    comp = score_posting(p)["components"]["pay"]
    assert comp["points"] == 15
    assert comp["strong"] is True


def test_pay_verified_below_100k_is_small_bonus():
    assert score_posting(_make(salary_min=7000.0))["components"]["pay"]["points"] == 5


def test_pay_unverified_scores_zero_and_carries_note():
    comp = score_posting(_make())["components"]["pay"]
    assert comp == {"points": 0, "verified": False, "note": "no salary listed"}


def test_pay_converted_from_eur_annual():
    p = _make(salary_min=120_000, currency="EUR", pay_period="annual")
    assert score_posting(p)["components"]["pay"]["strong"] is True


# --- Location -------------------------------------------------------------------

def test_location_fully_remote_10():
    assert score_posting(_make(raw_text="Fully remote."))["components"]["location"]["points"] == 10


def test_location_europe_10():
    p = _make(all_locations=["Lisbon, Portugal"])
    assert score_posting(p)["components"]["location"]["points"] == 10


def test_location_other_overseas_7():
    p = _make(all_locations=["Seoul, South Korea"])
    assert score_posting(p)["components"]["location"]["points"] == 7


def test_location_hybrid_5():
    p = _make(telework_flag=True, location="Sacramento, California")
    assert score_posting(p)["components"]["location"]["points"] == 5


def test_location_us_abbrev_not_overseas():
    p = _make(location="Sacramento, CA")
    assert score_posting(p)["lane"]["overseas_region"] is None


# --- Recency + clamping ----------------------------------------------------------

def test_recency_fires_within_seven_days():
    p = _make(posted_date=date.today() - timedelta(days=3))
    assert score_posting(p)["components"]["recency"]["points"] == 5


def test_recency_does_not_fire_past_seven_days():
    p = _make(posted_date=date.today() - timedelta(days=10))
    assert score_posting(p)["components"]["recency"]["points"] == 0


def test_recency_handles_missing_date():
    comp = score_posting(_make(posted_date=None))["components"]["recency"]
    assert comp == {"points": 0, "age_days": None}


def test_total_clamps_to_zero_floor():
    assert score_posting(_make(title="Senior Software Engineer"))["score"] == 0


def test_strong_lane_3_posting_clears_80():
    p = _make(
        title="Business Systems Analyst",
        raw_text=(
            "Fully remote. Requirements gathering, business analysis, stakeholder "
            "workflow automation in ServiceNow. SharePoint, BluePrism, RPA, Agile, "
            "QA, project management, Azure, WordPress, HTML, CSS."
        ),
        salary_min=110_000 / 12,
        posted_date=date.today(),
    )
    result = score_posting(p)
    assert result["lane"]["lane"] == 3
    assert result["score"] >= 80


# --- AI resilience (re-rank only) ---------------------------------------------

def test_authenticator_outscores_identical_pay_junior_dev():
    common = dict(salary_min=9000.0, salary_max=10000.0, raw_text="Standard duties.")
    auth = score_posting(_make(title="Authenticator", **common))
    dev = score_posting(_make(title="Junior Software Engineer", **common))
    assert auth["components"]["ai_resilience"]["points"] == 4
    assert dev["components"]["ai_resilience"]["points"] == -3
    others = [k for k in auth["components"] if k != "ai_resilience"]
    assert all(auth["components"][k]["points"] == dev["components"][k]["points"] for k in others)
    assert auth["score"] == dev["score"] + 7


def test_senior_or_lead_dev_has_no_exposed_penalty():
    for title in ("Senior Software Engineer", "Lead Developer", "Sr. Full Stack Developer",
                  "Software Engineering Manager", "Software Architect"):
        assert score_posting(_make(title=title))["components"]["ai_resilience"]["points"] >= 0, title


def test_physical_goods_qa_resilient_software_qa_neutral():
    from score import ai_resilience
    assert ai_resilience(_make(title="QA Specialist", raw_text="Inspect footwear at the factory."))["tier"] == "resilient"
    assert ai_resilience(_make(title="QA Specialist", raw_text="Test our web app."))["tier"] == "neutral"


def test_body_boilerplate_does_not_award_bonus():
    from score import ai_resilience
    p = _make(title="Graphic Designer", raw_text="EEO compliance. Training provided. Operations team.")
    assert ai_resilience(p)["points"] == 0

