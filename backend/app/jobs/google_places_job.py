"""google_places_job.py — Phase 4.2: the google_places_competitor ingestion
job.

Wraps fetch_competitor_ratings.py's existing fetch_place_details() and
normalize() (imported directly below, not reimplemented) into the job
contract described in app/jobs/__init__.py.

HEADS-UP carried over from fetch_competitor_ratings.py / the 4.2 checklist
item: the Places "Place Details" endpoint returns at most 5 sample reviews,
chosen by an undocumented rule that can change between calls. That sample
is NOT stored as its own Mention rows here - only the stable, trendable
aggregate (`rating`, `userRatingsTotal`) is written per competitor. Do not
add per-sample-review Mention rows later and call the result a competitor
sentiment trend without re-reading that HEADS-UP first: any trend computed
from that 5-review sample is noise, not signal.

external_id choice: one row per competitor per run, keyed on the
competitor's own Google `place_id`. There is no per-review ID to key on
(see the HEADS-UP above) - place_id is what's actually stable across
re-fetches, and it is what makes this an idempotent upsert (2.5) of "this
competitor's current aggregate rating," which is exactly what this source
can honestly provide.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy.orm import Session

# fetch_competitor_ratings.py (and config.py, http_utils.py) live at
# backend/, two directories above this file - see google_reviews_job.py's
# matching comment for why this defensive sys.path insertion exists.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.models import RunStatus  # noqa: E402
from app.repository import record_ingestion, start_run  # noqa: E402
from config import COMPETITOR_PLACE_IDS  # noqa: E402
from fetch_competitor_ratings import fetch_place_details, normalize  # noqa: E402
from http_utils import RetryExhaustedError  # noqa: E402

SOURCE_NAME = "google_places_competitor"


def run(session: Session) -> None:
    """One ingestion pass over every configured competitor with a real
    (non-placeholder) place_id.

    Unlike the reviews job, Places' Place Details endpoint has no distinct
    "access denied" signal (see fetch_competitor_ratings.fetch_place_details
    - a non-"OK" API status is treated uniformly as "not_found"), so there
    is no equivalent RunStatus.ACCESS_DENIED case here. A competitor whose
    fetch failed after retries, or came back "not_found", is not written as
    a Mention row (there is no real aggregate to record) and is not counted
    in items_ingested - so start_run's own items_seen/items_ingested
    inference already produces PARTIAL for a run with any such competitor,
    with no need for an explicit mark() for status alone. mark() is still
    used, but only to attach the human-readable reason(s) to the ledger's
    `error` column, which the counts alone can't carry.
    """
    with start_run(session, source=SOURCE_NAME) as run_recorder:
        errors: list[str] = []

        for name, place_id in COMPETITOR_PLACE_IDS.items():
            if place_id.startswith("REPLACE_ME"):
                # Not yet configured - matches fetch_competitor_ratings.
                # main(), which skips these entirely rather than treating
                # them as attempted-and-failed.
                continue

            run_recorder.items_seen += 1
            try:
                details = fetch_place_details(place_id)
            except RetryExhaustedError as exc:
                errors.append(f"{name}: request failed (retries exhausted): {exc}")
                continue

            if not details:
                errors.append(f"{name}: Places API returned no result for place_id={place_id!r}")
                continue

            normalized = normalize(name, details, status="ok")
            record_ingestion(
                session,
                source=SOURCE_NAME,
                kind="review",
                external_id=place_id,
                venue=name,
                rating=normalized["rating"],
                raw_payload=details,
            )
            run_recorder.items_ingested += 1

        if errors:
            run_recorder.mark(RunStatus.PARTIAL, error="; ".join(errors))
