"""Tests for pay.py — USD-monthly normalization."""
import pytest

from crawler.base import Posting
from pay import USD_PER_UNIT, to_usd_monthly


def _make(**kw) -> Posting:
    base = dict(source="test", source_job_id="1", title="t", employer="e",
                url="u", raw_text="")
    base.update(kw)
    return Posting(**base)


def test_currency_table_has_required_codes():
    for code in ["USD", "EUR", "GBP", "VND", "IDR", "THB", "CNY", "JPY", "KRW"]:
        assert code in USD_PER_UNIT


def test_usd_monthly_default_is_unchanged():
    p = _make(salary_min=8000.0, salary_max=9000.0)
    assert (p.salary_min, p.salary_max) == (8000.0, 9000.0)
    assert (p.currency, p.pay_period, p.pay_note) == ("USD", "monthly", None)


def test_annual_usd_divides_by_12():
    p = _make(salary_min=120_000, pay_period="annual")
    assert p.salary_min == pytest.approx(10_000)


def test_hourly_usd_uses_2080_hours():
    assert to_usd_monthly(50, "USD", "hourly") == pytest.approx(50 * 2080 / 12)


def test_100k_dong_is_about_four_dollars():
    assert to_usd_monthly(100_000, "VND", "monthly") == pytest.approx(3.9, abs=0.5)


def test_currency_code_case_insensitive():
    p = _make(salary_min=1000, currency="gbp", pay_period="Monthly")
    assert p.salary_min == pytest.approx(1270)
    assert p.currency == "USD"


def test_unknown_currency_clears_salary_and_notes():
    p = _make(salary_min=1000, salary_max=2000, currency="ZZZ")
    assert p.salary_min is None and p.salary_max is None
    assert p.pay_note == "unknown currency 'ZZZ'"


def test_unknown_period_clears_salary_and_notes():
    p = _make(salary_min=1000, pay_period="per-gig")
    assert p.salary_min is None
    assert p.pay_note == "unknown pay period 'per-gig'"


def test_no_salary_notes_unverified():
    assert _make().pay_note == "no salary listed"


def test_normalization_is_idempotent():
    """A Posting rebuilt from DB (USD/monthly defaults) must not re-convert."""
    p = _make(salary_min=96_000, currency="EUR", pay_period="annual")
    rebuilt = _make(salary_min=p.salary_min)
    assert rebuilt.salary_min == pytest.approx(p.salary_min)
