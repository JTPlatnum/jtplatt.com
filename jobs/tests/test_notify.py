"""Tests for notify.py — weekly digest. SMTP is always faked; no real email."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import filter as filter_mod
import notify
import store
from crawler.base import Posting

TODAY = date(2026, 11, 1)


def _p(job_id: str, **kw) -> Posting:
    base = dict(source="usajobs", source_job_id=job_id, title="Generic Role",
                employer="Test Employer", url=f"https://example.test/{job_id}",
                raw_text="", location="Sacramento, California",
                all_locations=["Sacramento, California"], telework_flag=False)
    base.update(kw)
    return Posting(**base)


# Lane-1 Nike jobs score differently via lane-keyword hits in raw_text.
NIKE_HI = _p("N1", source="nike", title="Director, Footwear Product Line Manager",
             employer="NIKE, Inc.", location="Shanghai, China Mainland",
             all_locations=["Shanghai, China Mainland"],
             raw_text="footwear sneaker merchandising supply chain sourcing factory")
NIKE_LO = _p("N2", source="nike", title="Senior Operations Specialist", employer="NIKE, Inc.",
             location="Beaverton, Oregon", all_locations=["Beaverton, Oregon"],
             raw_text="Nike.")
# No readable level -> fallback estimate $90k min, below the $100k sneaker floor.
NIKE_NO_LEVEL = _p("N3", source="nike", title="Operations Specialist", employer="NIKE, Inc.",
                   location="Beaverton, Oregon", all_locations=["Beaverton, Oregon"],
                   raw_text="Nike.")
STATE_IT = _p("S1", title="Information Technology Specialist II",
              salary_min=9000.0, salary_max=11000.0)
EDU = _p("E1", source="csu", title="Instructional Designer",
         location="Santa Cruz, California", all_locations=["Santa Cruz, California"],
         salary_min=8500.0, salary_max=9500.0)
UNLANED = _p("U1", title="Receptionist", salary_min=8200.0, salary_max=8200.0)
FILTERED_OUT = _p("X1", title="Business Analyst", location="Atlanta, Georgia",
                  all_locations=["Atlanta, Georgia"])
STALE = _p("OLD", title="Information Technology Specialist III", salary_min=12000.0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(filter_mod, "SALARY_FLOOR", 0.0)
    store.init_db(tmp_path / "test.db")
    for p in (NIKE_LO, NIKE_NO_LEVEL, STATE_IT, EDU, UNLANED, FILTERED_OUT, NIKE_HI, STALE):
        store.upsert_job(p)
    # Push STALE outside the 14-day active window.
    store._get_conn().execute(
        "UPDATE jobs SET last_seen_at = '2020-01-01T00:00:00' WHERE source_job_id = 'OLD'")
    store._get_conn().commit()
    return tmp_path / "test.db"


# --- Selection / ordering / grouping ------------------------------------------

def test_digest_order_lane_then_score(db):
    jobs, _ = notify.build_digest()
    assert [j.title for j in jobs] == [
        "Director, Footwear Product Line Manager",   # lane 1, higher score
        "Senior Operations Specialist",              # lane 1, lower score
        "Instructional Designer",                    # lane 4
        "Information Technology Specialist II",      # lane 5
        "Receptionist",                              # unlaned last
    ]
    assert jobs[0].score > jobs[1].score


def test_stale_and_filtered_jobs_excluded(db):
    titles = {j.title for j in notify.build_digest()[0]}
    assert "Information Technology Specialist III" not in titles  # last_seen 2020
    assert "Business Analyst" not in titles                       # Atlanta, filtered
    assert "Operations Specialist" not in titles                  # est. below sneaker floor


def test_low_scores_included_no_threshold(db):
    jobs, _ = notify.build_digest()
    assert any(j.score < 40 for j in jobs)


def test_size_cap_keeps_priority_order(db):
    jobs, total = notify.build_digest(size=2)
    assert [j.lane for j in jobs] == [1, 1]
    assert total == 5


def test_group_by_lane_labels_in_order(db):
    groups = notify.group_by_lane(notify.build_digest()[0])
    assert [label for label, _ in groups] == [
        "Lane 1: Sneakers / collectibles",
        "Lane 4: Education",
        "Lane 5: Sacramento state IT (fallback)",
        "Unlaned",
    ]
    assert len(groups[0][1]) == 2


def test_pay_text_verified_and_unverified(db):
    by_title = {j.title: j for j in notify.build_digest()[0]}
    assert by_title["Information Technology Specialist II"].pay == "$108,000-$132,000/yr"
    assert by_title["Receptionist"].pay == "$98,400/yr"
    assert by_title["Senior Operations Specialist"].pay == (
        "est. $110k-$140k *actual salary range not posted")
    assert by_title["Director, Footwear Product Line Manager"].pay == (   # Shanghai
        "est. $200k-$310k *actual salary not posted; assumes expat/US pay")


# --- Rendering ---------------------------------------------------------------------

def test_html_has_links_and_fields_and_no_images(db):
    jobs, total = notify.build_digest()
    out = notify.render_html(jobs, total, TODAY)
    assert "href='https://example.test/N1'" in out
    assert "NIKE, Inc." in out and "Shanghai, China Mainland" in out
    assert "est. $200k-$310k *actual salary not posted; assumes expat/US pay" in out
    assert "score " in out
    assert "<img" not in out.lower()
    assert "name='viewport'" in out


def test_html_escapes_content():
    j = notify.DigestJob(title="<script>x</script>", employer="A & B", location="L",
                         pay="pay unverified", lane=None, lane_name="Unlaned",
                         score=1, url="https://x.test/?a=1&b='2'")
    out = notify.render_html([j], 1, TODAY)
    assert "<script>" not in out
    assert "A &amp; B" in out
    assert "&#x27;2&#x27;" in out


def test_empty_digest_says_so():
    assert "No active jobs passed the filters" in notify.render_html([], 0, TODAY)
    assert "No active jobs passed the filters" in notify.render_text([], 0, TODAY)


def test_message_is_multipart_text_and_html(db):
    jobs, total = notify.build_digest()
    msg = notify.build_message(jobs, total, TODAY, "me@test", "you@test")
    assert msg["Subject"] == "Job digest 2026-11-01: 5 jobs"
    types = [part.get_content_type() for part in msg.iter_parts()]
    assert types == ["text/plain", "text/html"]


# --- Sending (faked) ------------------------------------------------------------

class _FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"]))


_ENV = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "me@test",
        "SMTP_PASSWORD": "app-pw", "EMAIL_TO": "you@test"}


@pytest.fixture
def cli(db, monkeypatch):
    monkeypatch.setattr(notify, "DB_PATH", db)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.instances.clear()
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_send_digest_uses_starttls_on_587(cli):
    for k, v in _ENV.items():
        cli.setenv(k, v)
    assert notify.main(["--send-digest"]) == notify.EXIT_OK
    smtp = _FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert smtp.calls == ["starttls", ("login", "me@test"), ("send", "you@test")]


def test_send_digest_missing_env_fails_without_connecting(cli, capsys):
    assert notify.main(["--send-digest"]) == notify.EXIT_FAILURE
    assert _FakeSMTP.instances == []
    assert "missing env vars" in capsys.readouterr().err


def test_preview_writes_html_and_sends_nothing(cli, tmp_path):
    out = tmp_path / "preview.html"
    assert notify.main(["--preview", str(out)]) == notify.EXIT_OK
    assert "Lane 1: Sneakers / collectibles" in out.read_text()
    assert _FakeSMTP.instances == []


def test_cli_requires_a_mode(cli):
    with pytest.raises(SystemExit):
        notify.main([])


# --- Per-lane cap -----------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

_NAMES = {1: "Sneakers / collectibles", 2: "Overseas", 3: "Remote knowledge work",
          4: "Education", 5: "Sacramento state IT (fallback)", None: None}


def _kept(lane, score, title):
    posting = SimpleNamespace(title=title, employer="E", location="L", all_locations=None,
                              salary_min=None, salary_max=None, estimate_min=None,
                              estimate_max=None, url=f"https://x.test/{title}")
    return posting, {"score": score, "lane": {"lane": lane, "name": _NAMES[lane]}}


def test_lane1_capped_at_6_and_others_fill():
    kept = [_kept(1, 50 - i, f"s{i}") for i in range(20)]
    kept += [_kept(2, 30, "o1"), _kept(4, 45, "e1"), _kept(3, 20, "r1"),
             _kept(5, 10, "t1"), _kept(2, 5, "o2"), _kept(4, 1, "e2"), _kept(5, 0, "t2")]
    jobs = notify.select_jobs(kept)
    assert len(jobs) == 12
    assert [j.title for j in jobs if j.lane == 1] == ["s0", "s1", "s2", "s3", "s4", "s5"]
    # 6 other-lane slots by score: e1 45, o1 30, r1 20, t1 10, o2 5, e2 1 (t2 0 cut)
    assert {j.title for j in jobs if j.lane != 1} == {"e1", "o1", "r1", "t1", "o2", "e2"}


def test_display_order_is_lane_then_score():
    kept = [_kept(1, 10, "s"), _kept(5, 90, "t"), _kept(2, 40, "o"), _kept(None, 99, "u"),
            _kept(4, 60, "e")]
    assert [j.title for j in notify.select_jobs(kept)] == ["s", "o", "e", "t", "u"]


def test_laned_beat_unlaned_even_at_lower_score():
    kept = [_kept(None, 90, f"u{i}") for i in range(10)] + [_kept(2, 5, "o"), _kept(4, 1, "e")]
    jobs = notify.select_jobs(kept, size=4, lane1_cap=6)
    assert [j.title for j in jobs] == ["o", "e", "u0", "u1"]


def test_unlaned_fill_only_leftover_slots():
    kept = [_kept(1, 50, f"s{i}") for i in range(3)] + [_kept(None, 80, f"u{i}") for i in range(20)]
    jobs = notify.select_jobs(kept)
    assert len(jobs) == 12
    assert sum(j.lane == 1 for j in jobs) == 3
    assert sum(j.lane is None for j in jobs) == 9


def test_lane1_cap_is_hard_when_others_run_dry():
    kept = [_kept(1, 50 - i, f"s{i}") for i in range(20)] + [_kept(4, 10, "e")]
    jobs = notify.select_jobs(kept)
    assert len(jobs) == 7
    assert sum(j.lane == 1 for j in jobs) == 6


# --- Estimate display ---------------------------------------------------------------

def _pay_posting(**kw):
    base = dict(title="Lead, Footwear", salary_min=None, salary_max=None,
                estimate_min=None, estimate_max=None,
                location="Beaverton, Oregon", all_locations=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_pay_text_estimate_marked_as_not_posted():
    p = _pay_posting(estimate_min=200000.0, estimate_max=310000.0)
    assert notify._pay_text(p) == "est. $200k-$310k *actual salary range not posted"


def test_pay_text_verified_pay_wins_over_estimate():
    p = _pay_posting(salary_min=10000.0, salary_max=12500.0,
                     estimate_min=200000.0, estimate_max=310000.0)
    assert notify._pay_text(p) == "$120,000-$150,000/yr"


def test_pay_text_no_pay_no_estimate_is_unverified():
    assert notify._pay_text(_pay_posting()) == "pay unverified"


def test_estimate_styled_as_unverified_in_html():
    j = notify.DigestJob(title="t", employer="e", location="l",
                         pay="est. $90k-$130k *actual salary range not posted",
                         lane=1, lane_name="Sneakers / collectibles", score=1,
                         url="https://x.test/t")
    out = notify.render_html([j], 1, TODAY)
    assert "<span style='color:#8a5a00;'>est. $90k-$130k *actual salary range not posted</span>" in out
    assert "est. $90k-$130k *actual salary range not posted" in notify.render_text([j], 1, TODAY)


def test_pay_text_overseas_estimate_assumes_expat():
    p = _pay_posting(estimate_min=125000.0, estimate_max=180000.0,
                     location="South, Singapore, Singapore")
    assert notify._pay_text(p) == (
        "est. $125k-$180k *actual salary not posted; assumes expat/US pay")


def test_pay_text_overseas_local_hire_estimate():
    p = _pay_posting(title="Senior Director, Apparel Local Creation GC",
                     estimate_min=112000.0, estimate_max=168000.0,
                     location="Shanghai, China Mainland")
    assert notify._pay_text(p) == "est. $112k-$168k *local-hire role"
