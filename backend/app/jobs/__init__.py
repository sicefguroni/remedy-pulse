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

This is intentionally not a Protocol-enforced plugin system with
discovery/registration machinery - there are 2-5 of these jobs total, and
a plain module plus a list is all that scale needs. `JOBS` below is the
one place scheduler.py (4.6) and status_report.py (4.7) both read from, so
a new job existing means adding one line here, not touching either of
those files' internals.
"""

from __future__ import annotations

from app.jobs import (
    google_places_job,
    google_reviews_job,
    meta_facebook_comments_job,
    meta_instagram_comments_job,
    meta_instagram_mentions_job,
    news_job,
    reddit_deletion_job,
    reddit_job,
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
JOBS = [
    google_reviews_job,
    google_places_job,
    news_job,
    reddit_job,
    reddit_deletion_job,
    meta_instagram_comments_job,
    meta_instagram_mentions_job,
    meta_facebook_comments_job,
]
