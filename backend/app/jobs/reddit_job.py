"""reddit_job.py — Phase 4.3: the reddit ingestion job.

Wraps fetch_reddit_mentions.py's existing get_reddit_client()/
fetch_all_mentions() (imported directly below, not reimplemented) into the
job contract described in app/jobs/__init__.py, so scheduler.py can run
this on a cadence instead of someone running the standalone script by
hand.

Not registered in app/jobs/__init__.py's JOBS list yet — see that file's
own comment: the Reddit/Meta adapters are being built in parallel by other
agents on this branch, and a separate reconciliation pass wires each one
in once it exists. This module is fully usable on its own in the
meantime (`from app.jobs.reddit_job import run`).

Credential handling deliberately mirrors news_job.py, not
fetch_reddit_mentions.py's own get_reddit_client(): a missing credential
is reported as an ERROR run via start_run()'s ledger (`recorder.mark(...)`)
rather than left to propagate get_reddit_client()'s SystemExit. A
scheduler process running multiple sources in one loop must not have one
source's missing credential take every other job's run down with it — the
same reasoning news_job.py's own module docstring gives for GNEWS_API_KEY.
This is why `missing_credentials()` is checked here BEFORE
get_reddit_client() is ever called, not by calling it inside a try/except.

external_id / raw_payload: external_id is the Reddit fullname
(fetch_reddit_mentions.normalize_submission()'s "fullname" field, e.g.
"t3_abc123") — the same value Mention.external_id's own docstring already
names as what this source should use, and the one
app/jobs/reddit_deletion_job.py re-checks against Reddit later.
raw_payload is the connector's full normalized row, not a copy of Reddit's
original API response the way news_job.py/google_reviews_job.py store
their source's raw API payload — PRAW's Submission objects aren't JSON-
serializable, so there is no raw dict to store here the way there is for
a plain HTTP JSON API. The normalized row already carries everything
reddit_deletion_job.py needs (at minimum "fullname" and "sourceUrl" — the
permalink), which is the actual requirement this satisfies.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from sqlalchemy.orm import Session

# fetch_reddit_mentions.py (and config.py, http_utils.py) live at backend/,
# two directories above this file - see google_reviews_job.py's matching
# comment for why this defensive sys.path insertion exists (this job may
# be imported however it's ultimately invoked, from an arbitrary cwd).
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import fetch_reddit_mentions  # noqa: E402
from app.models import RunStatus  # noqa: E402
from app.repository import is_within_backfill_window, record_ingestion, start_run  # noqa: E402
from fetch_reddit_mentions import fetch_all_mentions, get_reddit_client  # noqa: E402

SOURCE_NAME = "reddit"


def _parse_published_at(published_at: str | None) -> datetime | None:
    """fetch_reddit_mentions.normalize_submission() already writes
    `publishedAt` as a clean, offset-aware ISO-8601 string (built from
    Reddit's own created_utc via datetime.isoformat(), never GNews-style
    raw wire text) — so unlike news_job.py's equivalent helper this never
    needs the "Z" -> "+00:00" rewrite. Still guarded the same way (return
    None rather than raise on a missing/malformed value) so one odd row
    can't abort the whole run."""
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at)
    except ValueError:
        return None


def run(session: Session) -> None:
    """One ingestion pass: search every config.REDDIT_SUBREDDITS x
    config.REDDIT_SEARCH_TERMS pair via fetch_reddit_mentions's existing
    PRAW connector, and record_ingestion() each normalized row under
    SOURCE_NAME. Wraps the whole pass in app.repository.start_run so
    success/partial/error is recorded in the ingestion ledger (2.4) the
    same way every other source is."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        missing = fetch_reddit_mentions.missing_credentials()
        if missing:
            # See module docstring: reported as an ERROR run rather than
            # letting get_reddit_client()'s own SystemExit propagate out
            # of a scheduled job.
            recorder.mark(
                RunStatus.ERROR,
                error="Missing required Reddit credential(s): " + ", ".join(missing),
            )
            return

        reddit = get_reddit_client()
        mentions = fetch_all_mentions(reddit)

        for item in mentions:
            published_at = _parse_published_at(item.get("publishedAt"))

            # 9.2 backfill policy: excluded entirely (not counted toward
            # items_seen either) - this is Reddit keyword search, exactly
            # the "how far back do we search" cost/volume case the PRD's
            # Non-Goal is about. See
            # app.repository.is_within_backfill_window's docstring.
            if not is_within_backfill_window(published_at):
                continue

            recorder.items_seen += 1

            fullname = item.get("fullname")
            if not fullname:
                # No stable Reddit ID to upsert on - shouldn't happen for
                # anything fetch_all_mentions() actually returns, but
                # matches news_job.run()'s identical "seen, not ingested"
                # handling for an item with no usable external_id, rather
                # than raising and aborting every other item in this run.
                continue

            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="mention",
                external_id=fullname,
                venue=item.get("subreddit"),
                author=item.get("author"),
                text=item.get("text"),
                url=item.get("sourceUrl"),
                sentiment=item.get("sentiment"),
                published_at=published_at,
                raw_payload=item,
            )
            recorder.items_ingested += 1
