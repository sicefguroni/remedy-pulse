"""google_reviews_job.py — Phase 4.1: the google_reviews ingestion job.

Wraps fetch_owned_reviews.py's existing fetch/normalize functions
(load_credentials, get_accounts, get_locations, get_reviews,
normalize_reviews — imported directly below, not reimplemented) into the
job contract described in app/jobs/__init__.py, so scheduler.py can run
this on a cadence instead of someone running the standalone script by
hand.

This is a thin wrapper, not a rewrite: `run()` iterates
accounts/locations exactly like fetch_owned_reviews.main() already does
(same config.OWNED_LISTINGS location_id -> listing name lookup, same
per-listing try/except around ReviewsAccessDenied/RetryExhaustedError, same
"a location Google returns that isn't in config.py yet" handling). The
difference is what happens with each normalized review: instead of being
collected into a JSON file, it's written into the `mentions` table via
app.repository.record_ingestion(), and the run's outcome is reported into
the IngestionRun ledger instead of stdout.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

# fetch_owned_reviews.py (and config.py, http_utils.py) live at backend/,
# one level above backend/app/ and two above backend/app/jobs/. This job
# may be imported however it's ultimately invoked (`python -m
# app.jobs.google_reviews_job`, a scheduler process launched from an
# arbitrary cwd, ...) so don't assume the caller's cwd already has
# backend/ on sys.path - make sure it does. Mirrors the same defensive
# sys.path handling backend/tests/conftest.py already does for tests, for
# the identical reason.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.models import RunStatus  # noqa: E402
from app.repository import record_ingestion, start_run  # noqa: E402
from config import OWNED_LISTINGS  # noqa: E402
from fetch_owned_reviews import (  # noqa: E402
    ReviewsAccessDenied,
    get_accounts,
    get_locations,
    get_reviews,
    load_credentials,
    normalize_reviews,
)
from http_utils import RetryExhaustedError  # noqa: E402

SOURCE_NAME = "google_reviews"


def _review_external_id(review: dict[str, Any]) -> str:
    """Google's Business Profile review objects carry a `name` field like
    "accounts/123/locations/456/reviews/AbCdEf" - that whole string is
    already the identifier Google itself uses for this exact review, so it
    is used verbatim as external_id, rather than just the trailing
    reviewId segment (which is only unique within one location, not across
    every location Remedy owns)."""
    name = review.get("name")
    if name:
        return name
    # Defensive fallback only - the Business Profile API always includes
    # `name` in practice, but this keeps external_id deterministic across
    # re-fetches of the same review even if a future response ever omits it.
    return f"reviewId:{review.get('reviewId', '')}"


def _parse_published_at(create_time: str | None) -> datetime | None:
    if not create_time:
        return None
    try:
        return datetime.fromisoformat(create_time.replace("Z", "+00:00"))
    except ValueError:
        return None


def run(session: Session) -> None:
    """One ingestion pass over every configured owned listing.

    Preserves fetch_owned_reviews.main()'s existing resilience: a 403 or a
    retry-exhausted failure on one listing does not stop the others from
    being fetched, and a Google location that isn't yet in
    config.OWNED_LISTINGS is skipped (not an error) rather than crashing
    the run.
    """
    with start_run(session, source=SOURCE_NAME) as run_recorder:
        creds = load_credentials()
        accounts = get_accounts(creds)
        if not accounts:
            # A plain exception (not SystemExit, unlike fetch_owned_reviews.
            # main()'s CLI-oriented handling) so start_run's own `except
            # Exception` catches it and marks the run ERROR, instead of an
            # uncaught SystemExit propagating out of a scheduled job.
            raise RuntimeError("No Business Profile accounts found for this login.")

        configured_ids = {
            v["location_id"]: k
            for k, v in OWNED_LISTINGS.items()
            if not v["location_id"].startswith("REPLACE_ME")
        }

        access_denied_errors: list[str] = []
        retry_errors: list[str] = []

        for account in accounts:
            account_name = account["name"]  # e.g. "accounts/123456789"
            locations = get_locations(creds, account_name)

            for loc in locations:
                loc_name = loc["name"]  # e.g. "locations/987654321"
                loc_id = loc_name.split("/")[-1]

                if loc_id not in configured_ids:
                    # Informational, not an error - matches
                    # fetch_owned_reviews.main()'s unmatched_locations
                    # handling: a location Google returns that isn't yet
                    # mapped in config.OWNED_LISTINGS shouldn't fail the run.
                    continue

                listing_name = configured_ids[loc_id]

                try:
                    raw_reviews = get_reviews(creds, account_name, loc_name)
                except ReviewsAccessDenied as exc:
                    access_denied_errors.append(f"{listing_name}: {exc}")
                    continue
                except RetryExhaustedError as exc:
                    retry_errors.append(f"{listing_name}: {exc}")
                    continue

                # 9.2's backfill policy (config.BACKFILL_WINDOW_DAYS) is
                # deliberately NOT applied to owned reviews here - see
                # app.jobs.is_within_backfill_window's own docstring for
                # the general rule, but this source is the documented
                # exception to it: a branch's Reviews-tab rating/count is
                # the branch's TRUE, all-time Google rating, matching what
                # a customer sees on the actual listing page. Filtering
                # reviews older than 90 days would silently understate
                # both, for no cost/volume benefit (this endpoint already
                # returns the full history in one bounded paginated call
                # regardless of any filter applied after the fact). The
                # PRD's "no deep historical backfill" Non-Goal reads as
                # being about search/discovery-volume sources (Reddit,
                # news, Meta) where "how far back do we search" is a real
                # cost/volume question - not about truncating a known
                # entity's own current-state data.
                normalized = normalize_reviews(listing_name, raw_reviews)
                for raw_review, item in zip(raw_reviews, normalized):
                    run_recorder.items_seen += 1
                    record_ingestion(
                        session,
                        source=SOURCE_NAME,
                        kind="review",
                        external_id=_review_external_id(raw_review),
                        venue=listing_name,
                        author=item["author"],
                        rating=item["rating"],
                        text=item["text"],
                        sentiment=item["sentiment"],
                        has_reply=item["hasReply"],
                        published_at=_parse_published_at(raw_review.get("createTime")),
                        raw_payload=raw_review,
                    )
                    run_recorder.items_ingested += 1

        # A 403 is a status start_run's own items_seen/items_ingested
        # inference can't express (it would just look like PARTIAL, same as
        # a handful of individual reviews silently failing to insert) - so
        # it gets an explicit, distinct status per the job contract.
        if access_denied_errors:
            run_recorder.mark(RunStatus.ACCESS_DENIED, error="; ".join(access_denied_errors))
        elif retry_errors:
            # Not a distinct RunStatus - PARTIAL already correctly captures
            # "some listings ingested, one or more request(s) failed after
            # retries were exhausted"; the explicit mark() here exists only
            # to carry a human-readable error message into the ledger, since
            # a listing whose fetch failed outright never touched
            # items_seen/items_ingested for start_run's own inference to
            # explain *why* in the ledger's `error` column.
            run_recorder.mark(RunStatus.PARTIAL, error="; ".join(retry_errors))
