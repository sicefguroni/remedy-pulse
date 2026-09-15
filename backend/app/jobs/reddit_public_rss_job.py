"""app/jobs/reddit_public_rss_job.py — Reddit mentions via the public
search feed. No script app, no account credentials, no OAuth.

Why this exists alongside reddit_job.py
----------------------------------------
reddit_job.py uses praw, which needs REDDIT_CLIENT_ID, REDDIT_CLIENT_
SECRET, REDDIT_USERNAME and REDDIT_PASSWORD in .env. Registering that
script app is free and self-serve — no approval queue, unlike the
elevated commercial Data Access tier — and it still never got done, so
every scheduled run of that job recorded the same ERROR: "Missing
required Reddit credential(s)". A source that has been failing on a
missing free credential since the day it was written is, in practice, no
source at all.

reddit.com/search.rss needs none of those four values. A live check
returned real, current threads (r/skincare_ph, posted the same week) on
the first request. This job is what makes Reddit an actually-populated
source today; reddit_job.py stays registered because praw returns richer
per-post data (score, subreddit, edited state) and remains the better
source the day those credentials exist.

Deletion propagation
---------------------
reddit_deletion_job.py re-checks stored Reddit rows for upstream deletion
(docs/decisions/03-reddit-deletion-propagation.md) and is scoped to
reddit_job's SOURCE_NAME. Rows this job writes carry SOURCE_NAME
"reddit_public" and are therefore NOT covered by it — deliberately,
because that job re-checks via praw, which is exactly the credential this
job exists to not need. Extending deletion propagation to this source
needs its own unauthenticated re-check path; it is listed as an open item
in docs/live-run-evidence.md rather than silently assumed to work.

Item types
-----------
search.rss returns SUBREDDITS as well as posts — r/MANILA came back for
"skin clinic manila" with a 2008 creation date. config.REDDIT_PUBLIC_
ITEM_PREFIXES filters those out by Reddit's fullname type code; without
it, a subreddit's sidebar blurb would land in the Mentions feed as
something a person had said.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

from sqlalchemy.orm import Session

from app.models import RunStatus
from app.repository import is_within_backfill_window, record_ingestion, start_run
from config import (
    REDDIT_PUBLIC_CATEGORY_TERMS,
    REDDIT_PUBLIC_ENTITY_TERMS,
    REDDIT_PUBLIC_ITEM_PREFIXES,
    REDDIT_PUBLIC_QUERIES,
    REDDIT_PUBLIC_SEARCH_URL,
)
from fetch_rss import FeedParseError, fetch_feed, stable_external_id
from http_utils import RetryExhaustedError

SOURCE_NAME = "reddit_public"

# Items per query. Reddit's feed caps this at 100; 50 is deliberately
# under that — these queries run on the standard 12-hour ingestion
# cadence, and a query returning more than 50 genuinely new items in
# twelve hours would mean the query is too broad to be useful in the
# Mentions feed, not that the limit is too low.
RESULTS_PER_QUERY = 50

# Seconds to wait between queries within one pass.
#
# Measured, not guessed: firing all seven REDDIT_PUBLIC_QUERIES back to
# back got three of them 429'd through http_utils's full four-retry
# backoff, and the pass recorded PARTIAL with 4 threads ingested instead
# of the full set. Reddit's unauthenticated allowance is roughly ten
# requests a minute, and the retry backoff is the wrong tool for a limit
# that is known in advance — it turns a predictable wait into several
# failed round trips first.
#
# This costs about 40 seconds on a job that runs twice a day. That is
# free, in exchange for the source being complete rather than partial.
SECONDS_BETWEEN_QUERIES = 6.0


def feed_url(query: str) -> str:
    """The search feed URL for one query, newest-first.

    sort=new, not Reddit's default relevance sort: this is a monitoring
    feed re-read on a cadence, so "what is new since last time" is the
    only ordering that makes the cadence meaningful. Relevance ordering
    would re-surface the same high-scoring year-old thread every run and
    bury the thread posted an hour ago."""
    params = {"q": query, "sort": "new", "limit": RESULTS_PER_QUERY}
    return f"{REDDIT_PUBLIC_SEARCH_URL}?{urllib.parse.urlencode(params)}"


def _is_user_item(guid: str | None) -> bool:
    """True for a post or comment, False for a subreddit or anything else
    Reddit's search happens to match. See module docstring."""
    return bool(guid) and guid.startswith(REDDIT_PUBLIC_ITEM_PREFIXES)


def is_relevant(title: str | None, body: str | None) -> bool:
    """True if this Reddit item is about a tracked entity or the category.

    Reddit's search matches loosely, and config.REDDIT_PUBLIC_QUERIES has
    to be broad or it returns nothing — the first live pass brought back
    "26 [M4A] Looking for someone interesting to hang out with" as a top
    brand mention. Filtering on the returned text rather than narrowing
    the queries keeps coverage wide while keeping the human triage queue
    clean.

    The two term lists are checked against different scopes, which is the
    whole point of this function rather than one flat substring test:

      - A NAMED ENTITY (config.REDDIT_PUBLIC_ENTITY_TERMS — Remedy, its
        aliases, the tracked competitors) counts anywhere in the post.
        Someone naming a clinic in the body of an unrelated thread is
        still a real mention of that clinic; a "credit card cashback
        promos" roundup listing Aivee Clinic genuinely is competitor
        coverage, and dropping it would lose real signal.

      - A CATEGORY WORD (config.REDDIT_PUBLIC_CATEGORY_TERMS —
        "dermatologist", "skin clinic") counts only in the TITLE. This is
        the rule that rejects the [M4A] post, which earned its match on
        one incidental "dermatologist" some four hundred words into an
        off-topic body. Someone actually asking for a derma recommendation
        puts it in the title — "Dermatologist reco in BGC please" — and a
        long post that mentions the word once in passing is not what the
        Mentions feed is for.

    A filtered item is counted in items_seen but not items_ingested, so
    the ledger shows the filter working (seen > ingested) rather than
    silently shrinking the result — the same distinction news_job.py
    already draws for an article it fetched but could not store."""
    title_lower = (title or "").lower()
    body_lower = (body or "").lower()
    if not title_lower and not body_lower:
        return False
    whole = f"{title_lower} {body_lower}"
    if any(term in whole for term in REDDIT_PUBLIC_ENTITY_TERMS):
        return True
    return any(term in title_lower for term in REDDIT_PUBLIC_CATEGORY_TERMS)


def run(session: Session) -> None:
    """One ingestion pass over config.REDDIT_PUBLIC_QUERIES, deduped by
    Reddit fullname across all queries combined (the same thread matches
    several of these queries by design).

    Per-query failures are collected rather than fatal, and reported as
    PARTIAL/ERROR per the same reasoning as the two free news jobs.
    Reddit's 429s are worth noting specifically: http_utils already
    backs off and honors Retry-After on those, and fetch_rss sends a
    descriptive User-Agent, which together are what keep this source
    usable without credentials — a default python-requests User-Agent
    gets 429'd on the first call regardless of rate."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        by_fullname: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for index, query in enumerate(REDDIT_PUBLIC_QUERIES):
            # Before every query but the first - see
            # SECONDS_BETWEEN_QUERIES for why this is a fixed wait rather
            # than left to http_utils's retry backoff.
            if index:
                time.sleep(SECONDS_BETWEEN_QUERIES)
            try:
                items = fetch_feed(feed_url(query))
            except (RetryExhaustedError, FeedParseError) as exc:
                failures.append(f"{query}: {exc}")
                continue
            for item in items:
                guid = item.get("guid")
                if _is_user_item(guid):
                    by_fullname.setdefault(guid, item)

        if failures and not by_fullname:
            recorder.mark(RunStatus.ERROR, error="; ".join(failures[:3]))
            return

        for fullname, item in by_fullname.items():
            if not is_within_backfill_window(item["published_at"]):
                continue

            recorder.items_seen += 1

            if not item["link"]:
                continue

            # Title and body are separate fields on Reddit and the title
            # frequently carries the whole point ("Clinics or
            # Dermatologists Reco"), so both are passed to the
            # classifier joined — not the body alone, which is sometimes
            # empty on a link post, and not the title alone, which drops
            # the detail that determines sentiment.
            title = item["title"] or ""
            summary = item["summary"] or ""
            text = f"{title}\n\n{summary}".strip() if summary else title or None

            if not is_relevant(item["title"], item["summary"]):
                continue

            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="mention",
                external_id=stable_external_id(fullname),
                headline=item["title"],
                text=text,
                url=item["link"],
                published_at=item["published_at"],
                # Reddit's Atom author is "/u/name"; the leading "/u/" is
                # stripped so the Mentions feed shows the same "u/name"
                # shape fetch_reddit_mentions.py's praw path already
                # stores, rather than two spellings of one author format.
                author=_author(item.get("author")),
                sentiment=None,
                raw_payload=_serializable(item),
            )
            recorder.items_ingested += 1

        if failures:
            recorder.mark(RunStatus.PARTIAL, error="; ".join(failures[:3]))


def _author(value: str | None) -> str | None:
    if not value:
        return None
    return value[len("/u/") :] if value.startswith("/u/") else value


def _serializable(item: dict[str, Any]) -> dict[str, Any]:
    """See google_news_rss_job._serializable() — Mention.raw_payload is a
    JSON column and published_at is a datetime."""
    payload = dict(item)
    published_at = payload.get("published_at")
    if published_at is not None:
        payload["published_at"] = published_at.isoformat()
    return payload
