"""news_job.py — Phase 3/4 orchestration for GNews news/press ingestion (4.5).

This is deliberately thin: `fetch_news_articles.py` already does the real
work (fetching, normalizing, deduping) as the engineering half of 1.5's
GNews evaluation. This module's only job is to drive that existing code
once per scheduler tick and report the result into the ingestion ledger
(2.4) and event log (3.1) via `app.repository`, exactly the way any other
Phase 4 source adapter is expected to.

- One request per `config.NEWS_SEARCH_TERMS` entry, combined and deduped
  by URL across all terms — mirrors `fetch_news_articles.main()` exactly,
  so this job and the standalone script never disagree about what counts
  as "the same article."
- A search term whose request exhausts retries (`RetryExhaustedError`,
  raised by `http_utils.get_with_retry` via `fetch_articles_for_term`) is
  skipped, not fatal — matching `fetch_news_articles.main()`'s own
  per-term try/except, so one bad term doesn't blank out every other
  term's results for the day.
- A missing `GNEWS_API_KEY` is reported as an ERROR run (see `run()`)
  rather than left to `fetch_news_articles`'s own `SystemExit` — a
  scheduler process running multiple sources in one loop must not have
  one source's missing credential kill every other job's run.
- `normalize()`'s `sentiment` is always `None` here (Phase 6's job, not
  this connector's) — passed through as-is, never invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

import fetch_news_articles
from app.models import RunStatus
from app.repository import is_within_backfill_window, record_ingestion, start_run
from config import NEWS_SEARCH_TERMS
from fetch_news_articles import dedupe_by_url, fetch_articles_for_term, normalize
from http_utils import RetryExhaustedError

SOURCE_NAME = "news_gnews"


def _parse_published_at(published_at: str | None) -> datetime | None:
    """Parse GNews's ISO-8601 `publishedAt` (e.g. "2026-06-29T09:03:00Z")
    into an aware datetime. Returns None for a missing or unparseable
    value rather than raising — a single article with a malformed date
    shouldn't abort the whole run."""
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def run(session: Session) -> None:
    """One ingestion pass: fetch every `config.NEWS_SEARCH_TERMS` entry via
    `fetch_news_articles`'s existing GNews connector, dedupe by URL across
    all terms combined, and `record_ingestion()` each normalized article
    under `SOURCE_NAME`. Wraps the whole pass in `app.repository.start_run`
    so success/partial/error is recorded in the ingestion ledger (2.4) the
    same way every other source will be."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        if not fetch_news_articles.API_KEY:
            # fetch_articles_for_term would otherwise fail confusingly (or
            # fetch_news_articles.main()'s own SystemExit would take the
            # whole scheduler process down with it) — one source's missing
            # credential must not do that. See module docstring.
            recorder.mark(RunStatus.ERROR, error="GNEWS_API_KEY is not set")
            return

        all_raw: list[dict[str, Any]] = []
        for term in NEWS_SEARCH_TERMS:
            try:
                raw = fetch_articles_for_term(term)
            except RetryExhaustedError:
                # Matches fetch_news_articles.main()'s own per-term
                # try/except: one exhausted-retries term must not abort
                # the others.
                continue
            all_raw.extend(raw)

        all_raw = dedupe_by_url(all_raw)

        for raw_article in all_raw:
            article = normalize(raw_article)
            published_at = _parse_published_at(article["publishedAt"])

            # 9.2 backfill policy: an article older than the window is
            # excluded entirely (not counted toward items_seen either) -
            # unlike the missing-url case below, this isn't a data-quality
            # problem worth a PARTIAL status, it's the policy working as
            # intended. See app.jobs.is_within_backfill_window's docstring.
            if not is_within_backfill_window(published_at):
                continue

            recorder.items_seen += 1

            if not article["url"]:
                # url doubles as external_id here (GNews gives no separate
                # stable ID) — an article without one can't be upserted
                # idempotently, so it's counted as seen but not ingested
                # rather than raising and aborting every other article in
                # the same run.
                continue

            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="article",
                external_id=article["url"],
                headline=article["headline"],
                text=article["description"],
                url=article["url"],
                published_at=published_at,
                venue=article["outlet"],
                tier=article["tier"],
                sentiment=article["sentiment"],
                raw_payload=raw_article,
            )
            recorder.items_ingested += 1
