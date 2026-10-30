"""We Work Remotely (category RSS feeds). See SPEC.md "Sources".

    https://weworkremotely.com/categories/{feed}.rss

Feeds: remote-programming-jobs, remote-product-jobs,
remote-management-and-finance-jobs, remote-sales-and-marketing-jobs. One GET
each, 2s apart; a job listed in two categories is yielded once (by guid).

Item -> Posting: title "Company: Role" is split on the first ": " into
employer / title; `region` ("Anywhere in the World", "North America Only")
is the candidate location (lanes.remote_board_scope); `pubDate` is RFC 822.
No salary field: the description goes through the Greenhouse text pay
parser, else pay unverified. No estimate is ever applied. Every posting is
fully remote.
"""
from __future__ import annotations

import html
import logging
import time
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator, List, Optional, Tuple

import requests

from crawler.base import Posting, Source
from crawler.sources.greenhouse import parse_salary
from crawler.sources.nike import strip_html

log = logging.getLogger(__name__)

FEED_BASE = "https://weworkremotely.com/categories"
FEEDS: List[str] = [
    "remote-programming-jobs",
    "remote-product-jobs",
    "remote-management-and-finance-jobs",
    "remote-sales-and-marketing-jobs",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml",
}


# --- Parsing ---------------------------------------------------------------

def split_title(raw: str) -> Tuple[str, str]:
    """"Company: Role" -> ("Company", "Role"). No ": " -> ("", raw)."""
    employer, sep, title = (raw or "").partition(": ")
    if not sep:
        return "", (raw or "").strip()
    return employer.strip(), title.strip()


def _rfc822_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        return None


def parse_item(item: ET.Element, source_name: str = "weworkremotely") -> Posting:
    """One RSS <item> -> Posting."""
    employer, title = split_title(item.findtext("title") or "")
    link = (item.findtext("link") or "").strip()
    guid = (item.findtext("guid") or link).strip()
    region = (item.findtext("region") or "").strip()
    raw_text = strip_html(html.unescape(item.findtext("description") or ""))
    lo, hi, currency, period = parse_salary(raw_text)
    return Posting(
        source=source_name,
        source_job_id=guid.rstrip("/").rsplit("/", 1)[-1],
        title=title,
        employer=employer,
        url=link or guid,
        raw_text=raw_text,
        salary_min=lo,
        salary_max=hi,
        currency=currency,
        pay_period=period or "annual",
        location=region or None,
        all_locations=[region] if region else None,
        posted_date=_rfc822_date(item.findtext("pubDate")),
        remote_type="full",
    )


def parse_feed(xml_text: str) -> List[ET.Element]:
    return ET.fromstring(xml_text).findall("./channel/item")


# --- Source -----------------------------------------------------------------

class WeWorkRemotelySource(Source):
    """Configured WWR category feeds, sequential, 2s between requests."""

    name = "weworkremotely"
    delay_seconds = 2.0

    def __init__(
        self,
        feeds: Optional[List[str]] = None,
        session: Optional[requests.Session] = None,
        request_timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._feeds = feeds if feeds is not None else FEEDS
        self._session = session or requests.Session()
        self._timeout = request_timeout
        self._sleep = sleep

    def _fetch_feed(self, feed: str) -> Optional[List[ET.Element]]:
        """Items for one feed, or None on a non-retryable failure. Raises
        _BackOff on 429/5xx."""
        url = f"{FEED_BASE}/{feed}.rss"
        try:
            resp = self._session.get(url, headers=_HEADERS, timeout=self._timeout)
        except requests.RequestException as e:
            log.error("weworkremotely.%s: request error: %s", feed, e)
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("weworkremotely.%s: %d; backing off", feed, resp.status_code)
            raise _BackOff()
        if resp.status_code != 200:
            log.error("weworkremotely.%s: unexpected status %d", feed, resp.status_code)
            return None
        try:
            return parse_feed(resp.text)
        except ET.ParseError as e:
            log.error("weworkremotely.%s: bad RSS: %s", feed, e)
            return None

    def fetch_listings(self) -> Iterator[Posting]:
        failed: List[str] = []
        seen: set[str] = set()
        try:
            for i, feed in enumerate(self._feeds):
                if i:
                    self._sleep(self.delay_seconds)
                items = self._fetch_feed(feed)
                if items is None:
                    failed.append(feed)
                    continue
                if not items:
                    log.warning("weworkremotely.%s: 0 items", feed)
                new = 0
                for item in items:
                    try:
                        posting = parse_item(item, self.name)
                    except Exception:
                        log.exception("weworkremotely.%s: parse failed; skipping", feed)
                        continue
                    if posting.source_job_id in seen:
                        continue
                    seen.add(posting.source_job_id)
                    new += 1
                    yield posting
                log.info("weworkremotely.%s: %d items, %d new", feed, len(items), new)
        except _BackOff:
            log.warning("weworkremotely: skipping rest of source this run after back-off")
            return
        if failed and len(failed) == len(self._feeds):
            raise RuntimeError(f"weworkremotely: every feed failed: {failed}")
        if failed:
            log.error("weworkremotely: feeds failed this run: %s", failed)


class _BackOff(Exception):
    """429/5xx: stop this source for the run."""
