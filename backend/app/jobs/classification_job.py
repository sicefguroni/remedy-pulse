"""app/jobs/classification_job.py — checklist 0.23: the scheduled job
that actually calls app.classification.classify_unclassified_batch().

Why this exists
----------------
app/classification.py (Phase 6) is complete and tested, but its own
docstring says outright: "This is what a future scheduled job (app/jobs/,
out of this module's scope) would call — this function only does the
classification work, not the scheduling/wiring." That job was never
built. Concretely, without it: every real Mention an ingestion job writes
sits at `sentiment=None`/`classified_at=None` forever — the alert routing
(6.3), the sentiment filter (P0-2), the topic sentiment split (P0-8), and
the competitor sentiment card (P0-10) all read real classifier output,
and none of it populates against live data until something calls
classify_unclassified_batch() on a schedule. This module is that
schedule.

Job contract, and how this job's use of it differs from an ingestion job
--------------------------------------------------------------------------
Like reddit_deletion_job.py (a precedent for this exact shape), this job
does not ingest new external items — it re-processes EXISTING Mention
rows already written by some other job. It still reports through
start_run()/the ledger exactly like an ingestion job (so the same "last
synced"/freshness machinery works for it), with the two counters
repurposed the same way reddit_deletion_job.py's own docstring explains:
  - items_seen      = rows classified this run (classify_unclassified_
                       batch()'s own return value) — not "items ingested
                       from an external source."
  - items_ingested  = same value. Every mention_id classify_unclassified_
                       batch() attempts succeeds from this job's point of
                       view (see "Failure modes" below) - there is no
                       per-item partial-failure state at this layer to
                       report separately, unlike an ingestion job where
                       items_seen > items_ingested is a meaningful,
                       common outcome.
SOURCE_NAME ("classification") is its own independent ledger source, not
folded into whichever job most recently ingested the row being
classified — a stalled classifier (e.g. GROQ_API_KEY revoked) is a
different, independently-diagnosable fact from a stalled ingestion
source, and collapsing the two would hide one from a snapshot of the
other, the same reasoning meta_job.py's own module docstring gives for
its three separate ledger sources.

Failure modes
--------------
classify_unclassified_batch() -> classify_and_store() -> classify_
sentiment() already degrades every per-item failure gracefully (a
malformed model response, a transient Groq API error) to a low-confidence
Neutral/digest result rather than raising — see classify_sentiment()'s
own docstring for why that guarantee lives there and not here. The ONE
exception that still propagates is ClassifierNotConfiguredError (no
GROQ_API_KEY, or the `groq` package missing) — deliberately, per that
class's own docstring, since every remaining item in the batch would fail
identically. Because Mention.text is guaranteed non-null by classify_
unclassified_batch()'s own query (its WHERE clause excludes NULL text),
a missing/broken config surfaces on the very first mention_id attempted,
before anything is classified — so this run() below never has a
partially-classified batch to account for: either the whole call
succeeds, or it raises immediately with nothing yet done. Caught here and
recorded as an ERROR run with the real reason, rather than left to
propagate out of app.scheduler.run_due_jobs() uncaught - the exact
`SystemExit`-shaped class of bug fixed in google_reviews_job.py/
news_job.py (checklist 0.21) does not apply here (ClassifierNotConfigured
Error is already a plain RuntimeError, by that class's own explicit
design), but treating a missing API key as "ERROR run, ledger says why"
rather than an uncaught exception matches every sibling job's convention
regardless.

Cadence
--------
See app.scheduler.CADENCE_HOURS's own comment for why this job's cadence
is deliberately much shorter than every ingestion job's 12-hour default:
the PRD's core v1 success metric is median time from a negative mention
APPEARING to being ASSIGNED, target under 4 business hours - a mention
sitting unclassified (and therefore unrouted to the alert workflow) for
up to 12 hours before this job even looks at it would make that target
unreachable regardless of how fast a human then responds.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.classification import ClassifierNotConfiguredError, classify_unclassified_batch
from app.models import RunStatus
from app.repository import start_run

SOURCE_NAME = "classification"

# Checklist 0.24 (docs/decisions/14-last-synced-excludes-non-data-sources.md):
# this job re-processes rows another job already fetched - it never
# touches an external data source itself, so its own success/failure has
# no bearing on "is the DATA stale," which is what GET /api/overview's
# lastSyncedAt and the mockup's sync pill both exist to answer (see
# app/api/routes/overview.py and app/api/routes/status.py, both of which
# read this attribute). Absent on every other job module in app/jobs/,
# which all default to counting via getattr(job, "IS_DATA_SOURCE", True) -
# see app/jobs/__init__.py's own docstring for the full contract.
IS_DATA_SOURCE = False

# How many not-yet-classified mentions to attempt in one run. 100, not
# classify_unclassified_batch()'s own default of 50 - deliberately
# generous given this job's short cadence (see module docstring) is meant
# to clear the backlog quickly, mirroring reddit_deletion_job.BATCH_SIZE's
# identical "raise later if the backlog grows faster than this keeps up
# with" reasoning rather than tuning this precisely from real volume data
# that doesn't exist yet.
BATCH_SIZE = 100


def run(session: Session) -> None:
    """One classification pass: classify up to BATCH_SIZE not-yet-
    classified mentions (oldest-ingested-first, per classify_unclassified_
    batch()'s own ordering) and record the outcome in the ingestion
    ledger under SOURCE_NAME. See module docstring for the counter
    semantics and the one failure mode this explicitly handles."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        try:
            count = classify_unclassified_batch(session, limit=BATCH_SIZE)
        except ClassifierNotConfiguredError as exc:
            recorder.mark(RunStatus.ERROR, error=str(exc))
            return
        recorder.items_seen = count
        recorder.items_ingested = count
