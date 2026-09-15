"""app/jobs/google_news_rss_job.py — news/press ingestion via Google News
RSS. No API key, no account, no approval queue.

Why this exists alongside news_job.py
--------------------------------------
news_job.py (GNews) was the project's only news source, and it needs a
key whose free tier caps at 100 requests/day and delays every result by
12 hours. That key existed, and the source still ingested nothing: a live
check of every entry in config.NEWS_SEARCH_TERMS returned
totalArticles=0, because those terms are exact-phrase Remedy brand names
and Remedy has effectively no press coverage yet.

Google News RSS needs none of that and returns real Philippine coverage
today — the same live check, against config.FREE_NEWS_QUERIES, returned
48 items on a single query. This job is therefore not a fallback for when
GNews is down; it is the news source that actually populates the EMV tab,
with GNews kept registered beside it because the two index different
outlets and a key already exists for it.

SOURCE_NAME is "news_google_rss" — the "news_" prefix is load-bearing,
not decorative: app.repository._apply_source_category() classifies a row
as news by exactly that prefix, so the Overview tab's source filter and
the EMV tab both pick this source up with no other change. Its own
ledger source (not folded into news_gnews) for the same reason
meta_job.py keeps three: "Google News is fine but GNews's quota is spent"
must stay one readable fact, not two facts averaged into one status.

Article URLs
-------------
Google News RSS <link> values are news.google.com redirector URLs, not
the publisher's own. They are stored as `url` as-is: resolving each one
to its final destination would cost an extra HTTP request per article,
and Google serves a JS interstitial rather than a 301 to an unattended
client, so the redirector is what actually opens in a browser anyway.

`external_id` is keyed on the item's <guid> instead, which is what is
stable across runs and what dedupes correctly — passed through
fetch_rss.stable_external_id() because these guids routinely exceed
Mention.external_id's 512 characters (a real article during the first
live run carried an 850-char guid and the insert failed outright). See
that function for why the overflow is hashed rather than truncated.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from sqlalchemy.orm import Session

from app.models import RunStatus
from app.repository import is_within_backfill_window, record_ingestion, start_run
from config import (
    FREE_NEWS_QUERIES,
    GOOGLE_NEWS_RSS_PARAMS,
    GOOGLE_NEWS_RSS_URL,
    OUTLET_TIER_MAP,
)
from fetch_rss import FeedParseError, fetch_feed, stable_external_id
from http_utils import RetryExhaustedError

SOURCE_NAME = "news_google_rss"


def feed_url(query: str) -> str:
    """The RSS URL for one search query, with the market params from
    config applied. Built here rather than hardcoded so the locale pinning
    (en-PH) lives in config next to the query list it scopes."""
    params = {"q": query, **GOOGLE_NEWS_RSS_PARAMS}
    return f"{GOOGLE_NEWS_RSS_URL}?{urllib.parse.urlencode(params)}"


def run(session: Session) -> None:
    """One ingestion pass: fetch every config.FREE_NEWS_QUERIES entry,
    dedupe by guid across all queries combined, and record_ingestion()
    each article under SOURCE_NAME.

    Per-query failures are skipped, not fatal — matching news_job.run()'s
    own per-term try/except. The difference from news_job is that there
    is no account-wide failure mode to break out of here (no key, no
    quota), so a failure genuinely is per-query: one malformed feed
    response does not imply the next query will fail too. Every query
    failing is reported as an ERROR run rather than a SUCCESS with
    items_seen=0, which is precisely the "silent all-clear" shape a
    review already caught this project reporting once."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        by_guid: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for query in FREE_NEWS_QUERIES:
            try:
                items = fetch_feed(feed_url(query))
            except (RetryExhaustedError, FeedParseError) as exc:
                failures.append(f"{query}: {exc}")
                continue
            for item in items:
                # guid, not link: see module docstring — the link is a
                # redirector, the guid is what is stable across runs.
                # Falls back to the link only when a feed omits guid
                # entirely, so an item is never dropped for lacking the
                # preferred key.
                key = item.get("guid") or item.get("link")
                if key:
                    by_guid.setdefault(key, item)

        if failures and not by_guid:
            recorder.mark(RunStatus.ERROR, error="; ".join(failures[:3]))
            return

        for key, item in by_guid.items():
            # 9.2 backfill policy, applied before items_seen is
            # incremented — an out-of-window article is the policy
            # working, not a data-quality problem worth a PARTIAL.
            if not is_within_backfill_window(item["published_at"]):
                continue

            recorder.items_seen += 1

            if not item["link"]:
                continue

            outlet = item.get("outlet")
            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="article",
                # Google News guids exceed external_id's VARCHAR(512);
                # see fetch_rss.stable_external_id() for why this hashes
                # rather than truncates.
                external_id=stable_external_id(key),
                headline=item["title"],
                text=item["summary"] or item["title"],
                url=item["link"],
                published_at=item["published_at"],
                venue=outlet,
                # tier=None for an outlet nobody has priced yet, never a
                # guess — same contract fetch_news_articles.normalize()
                # already holds, so an unmapped outlet shows as unpriced
                # in the EMV tab instead of silently mispriced.
                tier=OUTLET_TIER_MAP.get(outlet),
                # sentiment stays None here. Phase 6's classifier owns it
                # (app/jobs/classification_job.py), and a connector
                # inventing one would put a value in the alert routing
                # that no model ever produced.
                sentiment=None,
                raw_payload=_serializable(item),
            )
            recorder.items_ingested += 1

        if failures:
            # Some queries worked, some did not — PARTIAL with the real
            # reasons, rather than letting start_run() infer SUCCESS from
            # counts that only reflect the queries that happened to run.
            recorder.mark(RunStatus.PARTIAL, error="; ".join(failures[:3]))


def _serializable(item: dict[str, Any]) -> dict[str, Any]:
    """Mention.raw_payload is a JSON column, and fetch_rss returns
    published_at as a datetime — which json can't encode. Converted to an
    ISO string here rather than having fetch_rss return a string (every
    other caller wants the datetime) or dropping the field from the
    payload (it is the one field worth having verbatim when a timestamp
    later looks wrong)."""
    payload = dict(item)
    published_at = payload.get("published_at")
    if published_at is not None:
        payload["published_at"] = published_at.isoformat()
    return payload
