"""Remote-board candidate-location rule (lanes.remote_board_scope), the lane
2 / lane 3 split, the Japan/Vietnam/Thailand lane-2 bonus, and the remote
type surviving a DB round trip."""
from __future__ import annotations

import pytest

import filter as filter_mod
import lanes
import main
import notify
import store
from crawler.base import Posting
from score import score_posting


def _remote(locations=None, title="Senior Data Analyst", source="himalayas", **kw) -> Posting:
    base = dict(source=source, source_job_id=f"r-{title}-{locations}", title=title,
                employer="Acme", url="https://example.test/r", raw_text="",
                location=", ".join(locations) if locations else None,
                all_locations=locations, remote_type="full")
    base.update(kw)
    return Posting(**base)


@pytest.mark.parametrize("locations,scope", [
    (None, "anywhere"),
    (["Anywhere in the World"], "anywhere"),
    (["Worldwide"], "anywhere"),
    (["United States"], "us"),
    (["USA", "Canada", "USA timezones"], "us"),
    (["Europe", "USA", "Canada"], "us"),
    (["North America Only"], "us"),
    (["Brazil"], "overseas"),
    (["Malaysia", "Philippines", "Taiwan"], "overseas"),
    (["Belarus"], "overseas"),        # "us" inside a word is not the US
    (["Europe"], "overseas"),
    (["Toronto, Ontario"], "overseas"),
    (["Ciudad de México, Mexico"], "overseas"),
    (["Japan"], "overseas"),
    # Remote OK free text: US places are US, unplaceable text is not overseas.
    (["Seattle, Washington"], "us"),
    (["Kansas"], "us"),
    (["SIHO - Columbus, IN"], "us"),
    (["Redwood City"], "unknown"),
    (["Seoul"], "unknown"),
])
def test_remote_board_scope(locations, scope):
    assert lanes.remote_board_scope(_remote(locations)) == scope


def test_scope_is_none_for_other_sources():
    assert lanes.remote_board_scope(_remote(["Brazil"], source="usajobs")) is None


def test_country_locked_goes_to_lane2_not_lane3():
    assert lanes.assign_lane(_remote(["Brazil"]))["lane"] == 2


@pytest.mark.parametrize("locations", [None, ["United States"], ["Europe", "USA"],
                                       ["United States", "Germany"], ["Redwood City"]])
def test_anywhere_or_us_eligible_goes_to_lane3(locations):
    assert lanes.assign_lane(_remote(locations))["lane"] == 3


def test_non_remote_source_overseas_rule_unchanged():
    p = Posting(source="usajobs", source_job_id="x", title="Senior Data Analyst",
                employer="Army", url="https://example.test/x", raw_text="",
                location="Wiesbaden, Germany", all_locations=["Wiesbaden, Germany"])
    assert lanes.assign_lane(p)["lane"] == 2


@pytest.mark.parametrize("locations,bonus", [
    (["Japan"], 10), (["Vietnam"], 10), (["Thailand"], 10),
    (["Brazil"], 0), (["Germany"], 0),
])
def test_preferred_country_lock_bonus(locations, bonus):
    result = score_posting(_remote(locations))
    assert result["lane"]["lane"] == 2
    assert result["components"]["preferred_lock"]["points"] == bonus


def test_preferred_lock_ranks_above_other_lock():
    jp = score_posting(_remote(["Japan"]))["score"]
    br = score_posting(_remote(["Brazil"]))["score"]
    assert jp == br + 10


def test_no_bonus_outside_lane2():
    result = score_posting(_remote(["United States", "Japan"]))
    assert result["lane"]["lane"] == 3
    assert result["components"]["preferred_lock"]["points"] == 0


def test_remote_type_full_survives_db_roundtrip(tmp_path, monkeypatch):
    """remote_type isn't stored; remote-board rows must still read as fully
    remote so country-locked roles pass the location filter."""
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    store.init_db(tmp_path / "t.db")
    store.upsert_job(_remote(["Brazil"]))
    (row,) = store.get_active_jobs()
    p = main._row_to_posting(row)
    assert p.remote_type is None
    assert lanes.remote_type(p) == "full"
    assert filter_mod.should_keep(p) == (True, "")


def test_digest_line_shows_source():
    j = notify.to_digest_job(_remote(["Japan"], source="remotive"),
                             score_posting(_remote(["Japan"], source="remotive")))
    assert j.source == "Remotive"
    assert "| via Remotive" in notify.render_text([j], 1, notify.date(2026, 11, 1))
    assert "&middot; via Remotive" in notify.render_html([j], 1, notify.date(2026, 11, 1))
