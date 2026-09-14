"""app/jobs — the ingestion job contract (4.1, 4.2, 4.6, 4.7).

Each ingestion job is a plain module at app/jobs/<name>_job.py, and must
expose exactly two things:

    SOURCE_NAME: str
        The `source` value used on Mention/IngestionRun rows for this
        adapter (e.g. "google_reviews", "google_places_competitor").

    def run(session: sqlalchemy.orm.Session) -> None:
        One ingestion pass. Wraps app.repository.start_run(session,
        source=SOURCE_NAME) as a context manager, fetches and normalizes
        items via that source's existing fetch_*.py module (imported
        directly - never reimplemented here), and calls
        app.repository.record_ingestion(session, source=SOURCE_NAME,
        kind=..., external_id=..., **other_fields) once per normalized
        item, incrementing the run recorder's items_seen/items_ingested as
        it goes. Let start_run's own exit logic infer
        RunStatus.SUCCESS/PARTIAL/ERROR from those counts; call
        run_recorder.mark(...) explicitly only for a status the counts
        alone can't express (e.g. RunStatus.ACCESS_DENIED on a 403 - see
        google_reviews_job.run()).

Optionally, a job module may also expose:

    IS_DATA_SOURCE: bool
        Checklist 0.24 (docs/decisions/14-last-synced-excludes-non-data-
        sources.md). Defaults to True (via getattr(job, "IS_DATA_SOURCE",
        True) at every call site that reads it - app/api/routes/
        overview.py's lastSyncedAt and app/api/routes/status.py's
        isDataSource field) for the common case: an adapter that fetches
        from an external source and writes new Mention rows. Set to False
        on a job that only re-processes rows some OTHER job already
        wrote (reddit_deletion_job.py, classification_job.py,
        topic_tagging_job.py) - such a
        job's own success/failure has no bearing on "is the DATA stale,"
        which is the one thing lastSyncedAt and the mockup's sync pill
        exist to answer, so it must not count toward either.

This is intentionally not a Protocol-enforced plugin system with
discovery/registration machinery - there are 2-5 of these jobs total, and
a plain module plus a list is all that scale needs. `JOBS` below is the
one place scheduler.py (4.6) and status_report.py (4.7) both read from, so
a new job existing means adding one line here, not touching either of
those files' internals.
"""

from __future__ import annotations

from app.jobs import (
    bing_news_rss_job,
    classification_job,
    google_news_rss_job,
    google_places_job,
    google_reviews_job,
    meta_facebook_comments_job,
    meta_instagram_comments_job,
    meta_instagram_mentions_job,
    news_job,
    reddit_deletion_job,
    reddit_job,
    reddit_public_deletion_job,
    reddit_public_rss_job,
    topic_tagging_job,
)

# scheduler.py and status_report.py both iterate this list - never the
# filesystem, never an import hook - so registering a new job is a
# one-line addition here, nothing else changes.
#
# meta_job.py itself is NOT registered directly - it exposes three
# independently-cadenced capabilities (SOURCE_NAMES, plural) rather than
# the single SOURCE_NAME this registry's consumers (scheduler.py,
# status_report.py) check cadence/freshness against. The three
# meta_*_job wrappers below are the reconciliation: each is a thin,
# single-SOURCE_NAME module delegating to one of meta_job's
# run_instagram_comments/run_instagram_mentions/run_facebook_comments
# functions, so "Instagram comments are current but Instagram mentions
# are 3 days stale" stays expressible per meta_job.py's own module
# docstring, decision 2.
#
# reddit_deletion_job is registered here too, alongside reddit_job -
# it's a different recurring job (re-checking already-stored Reddit rows
# for upstream deletion, not ingesting new ones), but it's still exactly
# "does this source's cadence say it's due? if so, run it," the same
# shape every other entry in this list is.
#
# classification_job (checklist 0.23) is the same kind of exception as
# reddit_deletion_job: it re-processes existing Mention rows rather than
# ingesting new ones, but still fits this same "one SOURCE_NAME, one
# is_due() cadence check" contract - see that module's own docstring for
# why it was missing entirely until now, and app.scheduler.CADENCE_HOURS
# for why its cadence is deliberately much shorter than everything else
# here. topic_tagging_job is that same shape again, for the same reason
# and found the same way - see its own docstring.
#
# google_news_rss_job, bing_news_rss_job and reddit_public_rss_job are
# the three sources that need no key, no account and no approval (see
# backend/fetch_rss.py and docs/decisions/15-free-sources-first.md).
# They are listed FIRST deliberately - app.scheduler.run_due_jobs()
# iterates this list in order, so the sources that can actually return
# data run before the ones still waiting on a credential or an approval,
# and a pass that is cut short for any reason has already done the work
# that produces rows. Each duplicates a capability an approval-gated job
# above was supposed to cover (news, news, Reddit) rather than replacing
# it: the gated jobs stay registered and return better data the day their
# access exists.
#
# reddit_public_deletion_job is the deletion re-check for the rows
# reddit_public_rss_job writes. It is listed immediately after it on
# purpose: reddit_deletion_job below covers source="reddit" only, through
# PRAW, so without this entry the rows we actually hold would be the one
# part of the store the 48-hour commitment did not reach.
JOBS = [
    google_news_rss_job,
    bing_news_rss_job,
    reddit_public_rss_job,
    reddit_public_deletion_job,
    google_reviews_job,
    google_places_job,
    news_job,
    reddit_job,
    reddit_deletion_job,
    meta_instagram_comments_job,
    meta_instagram_mentions_job,
    meta_facebook_comments_job,
    classification_job,
    topic_tagging_job,
]

# 9.2's is_within_backfill_window() lives in app.repository, NOT here,
# despite this being the more obviously-named home for it - a job module
# (e.g. news_job.py) importing it from here would be a circular import,
# since this __init__.py imports that same job module a few lines above
# (Python can't finish initializing app.jobs before news_job.py's own
# `from app.jobs import is_within_backfill_window` could resolve). See
# app.repository.is_within_backfill_window's docstring for the function
# itself.
