"""Pay normalization. See SPEC.md "Hard Filters -> Pay".

Every salary is normalized to USD-monthly when a Posting is constructed
(crawler.base.Posting.__post_init__ calls normalize_posting). After that, the
invariant downstream code relies on is:

    salary_min / salary_max are USD per month, or None.

None means "pay unverified": either no salary was posted, or the currency or
pay period is unknown and the number can't be trusted as USD. The original
figure still lives in raw_text for a human to read.

Normalization is idempotent: a normalized posting carries currency="USD",
pay_period="monthly", so re-normalizing (e.g. a Posting rebuilt from a DB row)
is a no-op.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
CURRENCY_TABLE_PATH = REPO_ROOT / "data" / "currency.yaml"

# Multiplier from one pay period to one month. Hourly/daily/weekly assume a
# full-time 2080-hour, 260-day, 52-week year.
PERIOD_TO_MONTHLY: Dict[str, float] = {
    "hourly": 2080 / 12,
    "daily": 260 / 12,
    "weekly": 52 / 12,
    "biweekly": 26 / 12,
    "semimonthly": 2.0,
    "monthly": 1.0,
    "annual": 1 / 12,
}


def _load_rates(path: Path = CURRENCY_TABLE_PATH) -> Dict[str, float]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping of CODE: usd_per_unit")
    return {str(k).upper(): float(v) for k, v in data.items()}


USD_PER_UNIT = _load_rates()


def to_usd_monthly(
    amount: Optional[float], currency: Optional[str], period: Optional[str],
) -> Optional[float]:
    """Convert amount to USD/month. None when amount, currency, or period
    is missing or unknown."""
    if amount is None or not currency or not period:
        return None
    rate = USD_PER_UNIT.get(currency.upper())
    factor = PERIOD_TO_MONTHLY.get(period.lower())
    if rate is None or factor is None:
        return None
    return float(amount) * rate * factor


def normalize_posting(posting) -> None:
    """Rewrite posting.salary_min/max to USD-monthly in place and set
    pay_note. Unknown currency/period -> salaries cleared, pay unverified."""
    if posting.salary_min is None and posting.salary_max is None:
        posting.pay_note = "no salary listed"
        return

    currency = (posting.currency or "").upper()
    period = (posting.pay_period or "").lower()
    if currency not in USD_PER_UNIT or period not in PERIOD_TO_MONTHLY:
        log.info(
            "pay: %s/%s unverified (currency=%r period=%r)",
            posting.source, posting.source_job_id, posting.currency, posting.pay_period,
        )
        posting.salary_min = None
        posting.salary_max = None
        posting.pay_note = (
            f"unknown currency {posting.currency!r}" if currency not in USD_PER_UNIT
            else f"unknown pay period {posting.pay_period!r}"
        )
        return

    posting.salary_min = to_usd_monthly(posting.salary_min, currency, period)
    posting.salary_max = to_usd_monthly(posting.salary_max, currency, period)
    posting.currency = "USD"
    posting.pay_period = "monthly"
    posting.pay_note = None


def pay_verified(posting) -> bool:
    return posting.salary_min is not None
