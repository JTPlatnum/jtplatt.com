"""Tests for score.py — Tier 1 rule-based scoring."""
from datetime import date, timedelta

import pytest

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


# --- Title patterns ---------------------------------------------------------

def test_yes_title_scores_high_and_clears_strong_bucket():
    p = _make(
        title="Information Technology Specialist I (Policy & Planning)",
        raw_text=(
            "Use BluePrism RPA. Manage ServiceNow workflows, SharePoint, "
            "WordPress. Lead training. Telework eligible. Accessibility "
            "and ADA compliance required. Excel automation experience a plus."
        ),
        telework_flag=True,
        posted_date=date.today(),
    )
    result = score_posting(p)
    assert result["components"]["title_match"]["points"] == 25
    assert result["components"]["title_match"]["list"] == "yes"
    assert result["score"] >= 80


def test_no_title_penalizes():
    p = _make(
        title="Senior Software Engineer",
        raw_text="Generic backend role. Some Python.",
    )
    result = score_posting(p)
    assert result["components"]["title_match"]["points"] == -25
    assert result["components"]["title_match"]["list"] == "no"
    assert result["score"] < 40


def test_no_title_wins_when_both_match():
    p = _make(
        title="Senior Software Engineer / IT Consultant (CSU)",
        raw_text="",
    )
    result = score_posting(p)
    assert result["components"]["title_match"]["points"] == -25


def test_neutral_title_is_zero():
    p = _make(title="Receptionist II", raw_text="")
    result = score_posting(p)
    assert result["components"]["title_match"]["points"] == 0


def test_title_word_boundary_avoids_substring_collision():
    """'Information Technology Specialist I' must not match 'Specialist III'."""
    p = _make(title="Information Technology Specialist III", raw_text="")
    result = score_posting(p)
    matched = result["components"]["title_match"]["matched"]
    # 'I' and 'II' patterns shouldn't grab 'III'; only an entry whose bare form
    # is exactly the prefix would match. None of the YES titles fit 'III'.
    assert matched != "Information Technology Specialist I"
    assert matched != "Information Technology Specialist II"


# --- Title-OR-classification (CalCareers pattern) ---------------------------

def test_classification_matches_yes_when_title_does_not():
    """CalCareers: title='.Net Developer' (non-matching display name),
    classification='Information Technology Specialist I' (YES). Score the YES."""
    p = _make(
        title=".Net Developer",
        classification="Information Technology Specialist I",
        raw_text="",
    )
    comp = score_posting(p)["components"]["title_match"]
    assert comp["points"] == 25
    assert comp["list"] == "yes"
    assert comp["matched"] == "Information Technology Specialist I"


def test_both_fields_matching_yes_counts_once():
    """When both title and classification match YES entries, score +25 (not +50)."""
    p = _make(
        title="IT Specialist",
        classification="Information Technology Specialist I",
        raw_text="",
    )
    comp = score_posting(p)["components"]["title_match"]
    assert comp["points"] == 25
    assert comp["list"] == "yes"


def test_no_in_classification_beats_yes_in_title():
    """NO penalty wins regardless of which field carries it."""
    p = _make(
        title="IT Specialist",  # YES
        classification="Senior Software Engineer",  # NO
        raw_text="",
    )
    comp = score_posting(p)["components"]["title_match"]
    assert comp["points"] == -25
    assert comp["list"] == "no"


def test_classification_none_falls_back_to_title_only():
    """USAJobs-shaped Posting (no classification) still scores from title."""
    p = _make(
        title="Senior Software Engineer",
        classification=None,
        raw_text="",
    )
    comp = score_posting(p)["components"]["title_match"]
    assert comp["points"] == -25
    assert comp["list"] == "no"


# --- Keyword density --------------------------------------------------------

def test_keyword_density_lifts_score():
    base = dict(title="Generic Role", posted_date=None, telework_flag=None)
    low = _make(**base, raw_text="A boring job description with no signal.")
    high = _make(
        **base,
        raw_text=(
            "Use BluePrism, ServiceNow, SharePoint, WordPress, Azure, "
            "Excel automation, Oracle, Agile, RPA, QA, HTML, CSS."
        ),
    )
    low_r = score_posting(low)
    high_r = score_posting(high)
    assert high_r["components"]["keyword_match"]["points"] > low_r["components"]["keyword_match"]["points"]
    assert high_r["score"] > low_r["score"]


def test_keyword_match_caps_at_40():
    # Cram every distinct skill we can think of; cap should hold at 40.
    text = " ".join([
        "BluePrism", "ServiceNow", "SharePoint", "WordPress", "Azure",
        "Oracle", "Excel", "Agile", "RPA", "QA", "HTML", "CSS", "JavaScript",
        "Git", "Jira", "QuickBooks", "FI$CAL", "PowerSchool",
    ])
    p = _make(raw_text=text)
    assert score_posting(p)["components"]["keyword_match"]["points"] == 40


def test_keyword_word_boundary_prevents_false_positives():
    # 'qa' must not match inside 'qualification' or 'quality assurance'.
    # 'rpa' must not match inside arbitrary substrings.
    p = _make(raw_text="Strong qualifications required. Europa was a moon.")
    matched = score_posting(p)["components"]["keyword_match"]["matched_keywords"]
    assert "qa" not in matched
    assert "rpa" not in matched


def test_scoped_azure_matches_on_bare_token():
    """'Azure (website publishing/deployment)' in inventory matches any 'azure'.
    Per the deliberate-scoping commit: Tier 1 matches broadly, Tier 2 narrows.
    """
    p = _make(raw_text="Azure architect needed.")
    matched = score_posting(p)["components"]["keyword_match"]["matched_keywords"]
    assert "azure" in matched


# --- Bonus components -------------------------------------------------------

def test_flexibility_fires_on_telework_flag():
    p = _make(raw_text="In-office position.", telework_flag=True)
    assert score_posting(p)["components"]["flexibility"]["points"] == 10


def test_flexibility_fires_on_raw_text_keyword():
    p = _make(raw_text="This role is fully remote.")
    assert score_posting(p)["components"]["flexibility"]["points"] == 10


def test_flexibility_does_not_fire_without_signal():
    p = _make(raw_text="Standard schedule, on-site.")
    assert score_posting(p)["components"]["flexibility"]["points"] == 0


def test_person_facing_fires():
    p = _make(raw_text="Lead training and instruction across business units.")
    comp = score_posting(p)["components"]["person_facing"]
    assert comp["points"] == 10
    assert "training" in comp["fired_by"]
    assert "instruction" in comp["fired_by"]


def test_international_via_raw_text():
    p = _make(raw_text="Position is overseas with the Foreign Service.")
    comp = score_posting(p)["components"]["international"]
    assert comp["points"] == 10
    assert "overseas" in comp["fired_by"]


def test_international_via_overseas_location():
    p = _make(
        raw_text="Standard duties.",
        all_locations=["Wiesbaden, Germany"],
    )
    comp = score_posting(p)["components"]["international"]
    assert comp["points"] == 10
    assert "non-US location" in comp["fired_by"]


def test_international_does_not_fire_on_us_only_locations():
    p = _make(
        raw_text="Position duties here.",
        all_locations=["Sacramento, California", "Washington, District of Columbia"],
    )
    assert score_posting(p)["components"]["international"]["points"] == 0


def test_recency_fires_within_seven_days():
    p = _make(posted_date=date.today() - timedelta(days=3))
    assert score_posting(p)["components"]["recency"]["points"] == 5


def test_recency_does_not_fire_past_seven_days():
    p = _make(posted_date=date.today() - timedelta(days=10))
    assert score_posting(p)["components"]["recency"]["points"] == 0


def test_recency_handles_missing_date():
    p = _make(posted_date=None)
    comp = score_posting(p)["components"]["recency"]
    assert comp["points"] == 0
    assert comp["age_days"] is None


# --- Aggregate clamping -----------------------------------------------------

def test_total_clamps_to_zero_floor():
    p = _make(title="Senior Software Engineer", raw_text="")
    assert score_posting(p)["score"] >= 0


def test_total_clamps_to_one_hundred_ceiling():
    # Construct a posting that would naturally score >100 if uncapped.
    p = _make(
        title="Information Technology Specialist I",
        raw_text=(
            "BluePrism ServiceNow SharePoint WordPress Azure Oracle Excel "
            "Agile RPA HTML CSS JavaScript Git Jira QuickBooks PowerSchool. "
            "Training and instruction. Overseas role. Telework available."
        ),
        telework_flag=True,
        posted_date=date.today(),
        all_locations=["Wiesbaden, Germany"],
    )
    assert score_posting(p)["score"] == 100


def test_returns_score_and_components_keys():
    result = score_posting(_make(raw_text=""))
    assert set(result.keys()) == {"score", "components"}
    assert set(result["components"].keys()) == {
        "keyword_match", "title_match", "flexibility",
        "person_facing", "international", "recency",
    }
