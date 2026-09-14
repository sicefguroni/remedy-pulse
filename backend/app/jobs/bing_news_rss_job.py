"""app/jobs/bing_news_rss_job.py — a second key-free news source, over
Bing News RSS.

Why a second free news source
------------------------------
Not redundancy for its own sake. Google News and Bing News index
overlapping but materially different outlet sets — the same live query
("Belo Medical Group") returned Rappler and Manila Bulletin from Google
and a partly different set from Bing. For a product whose EMV tab prices
whatever outlet ran the story, missing an outlet entirely is worse than
seeing the same story twice, and seeing it twice is already handled:
record_ingestion() keys uniqueness on (source, external_id), so an
article both feeds carry is stored once per source and the EMV tab's own
dedupe decides what to do with the pair, rather than two feeds silently
racing to own one row.

The cost of this second source is one more HTTP request per query per
cadence and nothing else — no key, no account, no quota to apply for.

Differences from google_news_rss_job.py
----------------------------------------
Same shape, two real format differences that are why this is its own
module rather than a parameter on that one. Both were found by reading a
live response, not by reading documentation:

  - Bing's <link> is a bing.com/news/apiclick.aspx redirector carrying
    the publisher's real URL in its `url=` query parameter. Unlike Google
    News's opaque redirector token, that parameter IS the destination and
    is trivially recoverable, so resolve_publisher_url() below unwraps it
    — giving a stable, human-readable `url` and `external_id`, and one
    that matches across runs even if Bing's tracking parameters change.
  - Bing emits no <guid> at all, so the (unwrapped) link is the only
    stable key available.

The outlet name lives in <News:Source>, whose namespace URI is the query
URL itself and therefore differs on every request; fetch_rss matches it
on local name for that reason. Getting it right is what lets this job
share config.OUTLET_TIER_MAP with the Google News job unchanged — a null
outlet means a null EMV tier, i.e. an unpriced article.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from sqlalchemy.orm import Session

from app.models import RunStatus
from app.repository import is_within_backfill_window, record_ingestion, start_run
from config import BING_NEWS_RSS_PARAMS, BING_NEWS_RSS_URL, FREE_NEWS_QUERIES, OUTLET_TIER_MAP
from fetch_rss import FeedParseError, fetch_feed, stable_external_id
from http_utils import RetryExhaustedError

SOURCE_NAME = "news_bing_rss"


def feed_url(query: str) -> str:
    """The RSS URL for one search query. format=RSS is what switches
    Bing's news search from an HTML page to a feed at all — without it
    this endpoint returns a full web page and fetch_rss would raise
    FeedParseError on every call."""
    params = {"q": query, **BING_NEWS_RSS_PARAMS}
    return f"{BING_NEWS_RSS_URL}?{urllib.parse.urlencode(params)}"


def resolve_publisher_url(link: str | None) -> str | None:
    """Unwrap Bing's apiclick.aspx redirector to the publisher's own URL.

    A real link from a live response:

        http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=6aa78...
            &url=https%3a%2f%2fwww.pep.ph%2fnews%2f44776%2f...&c=912170...

    Unwrapped here rather than stored as-is for two reasons that matter
    beyond tidiness. It is the external_id, so it is the dedupe key: the
    redirector carries per-request tracking parameters (`tid`, `c`) that
    would make the same article look like a new one on every run and
    re-insert it forever. And a human clicking through from the Mentions
    feed should land on the publisher, not on a Bing tracker.

    Returns `link` unchanged if it is not a redirector or carries no
    `url` parameter — an unrecognized shape is passed through, never
    dropped."""
    if not link or "apiclick.aspx" not in link:
        return link
    query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    target = query.get("url", [None])[0]
    return target or link


def run(session: Session) -> None:
    """One ingestion pass over config.FREE_NEWS_QUERIES. Per-query
    failures are collected and reported (PARTIAL if anything was
    ingested, ERROR if nothing was) rather than either aborting the pass
    or being swallowed into a SUCCESS with items_seen=0 — see
    google_news_rss_job.run()'s docstring for why that distinction is
    treated as load-bearing in this project."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        by_url: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for query in FREE_NEWS_QUERIES:
            try:
                items = fetch_feed(feed_url(query))
            except (RetryExhaustedError, FeedParseError) as exc:
                failures.append(f"{query}: {exc}")
                continue
            for item in items:
                # Unwrapped BEFORE it is used as the dedupe key, or the
                # redirector's per-request tracking parameters make the
                # same article look new on every pass.
                link = resolve_publisher_url(item.get("link"))
                if link:
                    item = {**item, "link": link}
                    by_url.setdefault(link, item)

        if failures and not by_url:
            recorder.mark(RunStatus.ERROR, error="; ".join(failures[:3]))
            return

        for link, item in by_url.items():
            if not is_within_backfill_window(item["published_at"]):
                continue

            recorder.items_seen += 1

            outlet = item.get("outlet")
            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="article",
                external_id=stable_external_id(link),
                headline=item["title"],
                text=item["summary"] or item["title"],
                url=link,
                published_at=item["published_at"],
                venue=outlet,
                tier=OUTLET_TIER_MAP.get(outlet),
                sentiment=None,
                raw_payload=_serializable(item),
            )
            recorder.items_ingested += 1

        if failures:
            recorder.mark(RunStatus.PARTIAL, error="; ".join(failures[:3]))


def _serializable(item: dict[str, Any]) -> dict[str, Any]:
    """See google_news_rss_job._serializable() — Mention.raw_payload is a
    JSON column and published_at is a datetime."""
    payload = dict(item)
    published_at = payload.get("published_at")
    if published_at is not None:
        payload["published_at"] = published_at.isoformat()
    return payload
