"""reddit_deletion_job.py — Phase 5.1: the Reddit 48-hour deletion-
propagation worker (implements checklist 0.7 / requirement C-2).

Read docs/decisions/reddit-deletion-propagation.md before touching this
file — this job IS the "ingestion in reverse" described there: a recurring
re-check of already-stored Reddit rows' Reddit-assigned IDs, not a delete
webhook. Reddit does not notify integrators when a post or comment is
deleted — there is no webhook, no deletion feed, nothing pushed to this
codebase — so compliance has to be built as a PULL: keep the Reddit-
assigned ID of every stored row, and periodically re-fetch each one to
check whether Reddit now 404s or returns a "[deleted]"/"[removed]" body.

Job Contract, and how this job's use of it differs from every other
job's
------------------------------------------------------------------------
Every other job in app/jobs/ ingests NEW items and calls
app.repository.record_ingestion() once per item. This job ingests
nothing — it re-checks EXISTING Mention rows (source="reddit") against
Reddit and scrubs the ones Reddit no longer serves. It still reports
through start_run()/the ledger exactly like an ingestion job (so the same
"last synced"/freshness machinery works for it), but the two ledger
counters are REPURPOSED for this job specifically, and a future reader of
the ledger for source="reddit_deletion_check" needs to know that:
  - items_seen      = rows re-checked THIS RUN (a batch, see BATCH_SIZE
                       below) — not "items ingested."
  - items_ingested  = rows found deleted-upstream and scrubbed THIS RUN
                       (not "new items ingested" — there are none here;
                       this job never inserts a Mention row).
SOURCE_NAME ("reddit_deletion_check") is deliberately distinct from
"reddit" itself (reddit_job.py's SOURCE_NAME) because this is a different
recurring job with its OWN independent success/failure history in the
ledger — a failed ingestion run and a failed deletion-check run are two
different facts (one means stale data, the other means a live retention-
commitment risk — see the Reasoning below), and collapsing both under one
ledger source would hide whichever failure isn't visible from a snapshot
of the other.

Why this uses fetch_reddit_mentions.get_reddit_client(), unlike
reddit_job.py's own credential handling
------------------------------------------------------------------------
reddit_job.py checks missing_credentials() itself before ever calling
get_reddit_client(), so a missing credential is an ERROR run rather than a
propagating SystemExit (see that module's docstring). This job does the
same, for the identical reason: a scheduler running both the ingestion
job and this deletion-check job in one process must not have one job's
missing credential take the other down with it.

Batching
---------
BATCH_SIZE caps how many stored rows this job re-checks in ONE run —
"don't re-check every stored Reddit mention on every single run if the
volume ever grows," per this task's own spec. 100 is a deliberately
conservative first value: at one PRAW call per row, well within a single
run's share of Reddit's API rate limit for a same-day/next-day-freshness
product (see the PRD) even running frequently, with headroom to raise it
later if the backlog of never-yet-rechecked rows grows faster than this
can keep up with. Rows are selected oldest-checked-first, ordered by
Mention.updated_at ascending:
  - updated_at advances every time upsert_mention() touches a row (see
    repository._upsert_insert's "updated_at always advances to now on a
    re-ingest" comment) - a row nobody has re-ingested since ingestion
    naturally sorts before one Reddit has touched since.
  - This job's own scrub also updates the row it touches (SQLAlchemy's
    onupdate=func.now() on Mention.updated_at fires on any column
    change), so a re-check always pushes a row to the back of the queue.
  - Together, this guarantees every stored row eventually gets its turn
    across enough runs, rather than the same oldest rows starving out
    ones ingested more recently, or vice versa.

Detection
----------
For each candidate row, Mention.external_id IS the Reddit fullname (see
models.Mention's own docstring: "a Reddit fullname like 't3_abc123'") -
fullname_kind() reads its "t3_"/"t1_" prefix to decide whether to call
reddit.submission(id=...) (a submission) or reddit.comment(id=...) (a
comment); PRAW's id= argument wants the bare base36 ID, with the prefix
already stripped, which _reddit_id() does. A row is treated as deleted
upstream when either:
  - Reddit's fetch for that ID raises at all (praw/prawcore's "not
    found"/banned/suspended-lookup errors, or any other fetch failure —
    see _is_deleted_upstream()'s own comment on why a fetch error is
    treated the same as "confirmed gone" here), or
  - Reddit still returns the object, but its body now reads
    "[deleted]"/"[removed]" (Reddit's own tombstone placeholders — the
    object can survive with a tombstoned body long after the actual
    content is gone).
Either case scrubs identically (_scrub()): text, author, and raw_payload
are cleared and deleted_at is set to now(). venue/url/published_at/
sentiment/rating are deliberately left alone — none of those are
"content" or "author-identifying data" (the submitted use-case PDF's exact
wording for what must be removed), and stripping them would erase
provenance (which subreddit, when Remedy Pulse first saw it) this system
still legitimately needs for its own audit trail without violating the
48-hour commitment, which is about REDDIT'S content/author data
specifically, not Remedy Pulse's own metadata about having once observed
it.

Detecting and scrubbing as soon as this job runs — rather than waiting
until close to the 48-hour deadline — is what keeps this safely within
that window; a scheduler that runs this job daily (or more often) turns
"48 hours" into "next run," which is the entire point of running this on
a recurring cadence instead of as a one-time cleanup.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

# fetch_reddit_mentions.py lives at backend/, two directories above this
# file - see google_reviews_job.py's matching comment for why this
# defensive sys.path insertion exists.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import fetch_reddit_mentions  # noqa: E402
from app.models import Mention, RunStatus  # noqa: E402
from app.repository import start_run  # noqa: E402
from fetch_reddit_mentions import DELETED_MARKERS, fullname_kind, get_reddit_client  # noqa: E402

SOURCE_NAME = "reddit_deletion_check"

# See module docstring's "Batching" section for why this exists and why
# 100 specifically.
BATCH_SIZE = 100


def _select_batch(session: Session) -> list[Mention]:
    """The oldest-checked-first batch of up to BATCH_SIZE stored,
    not-yet-deleted Reddit rows. See module docstring's "Batching"
    section for why Mention.updated_at ascending is what makes this fair
    across runs."""
    return list(
        session.execute(
            select(Mention)
            .where(Mention.source == "reddit", Mention.deleted_at.is_(None))
            .order_by(Mention.updated_at.asc())
            .limit(BATCH_SIZE)
        ).scalars()
    )


def _reddit_id(fullname: str) -> str:
    """"t3_abc123" -> "abc123" - PRAW's submission(id=...)/comment(id=...)
    want the bare base36 ID, not the "t3_"/"t1_"-prefixed fullname."""
    return fullname.split("_", 1)[1]


def _is_deleted_upstream(reddit, fullname: str, kind: str) -> bool:
    """Re-fetches one stored item from Reddit. Returns True if it now
    404s (or any other fetch failure — see the comment below) or if it
    still exists but its body is Reddit's own "[deleted]"/"[removed]"
    tombstone. See module docstring's "Detection" section."""
    reddit_id = _reddit_id(fullname)
    try:
        if kind == "submission":
            obj = reddit.submission(id=reddit_id)
            body = getattr(obj, "selftext", "") or ""
        else:
            obj = reddit.comment(id=reddit_id)
            body = getattr(obj, "body", "") or ""
    except Exception:
        # Any fetch failure (404/"not found", a banned or suspended
        # lookup, a transient network error, etc.) is treated as "no
        # longer there" - see the module docstring's Detection section
        # for why. A transient error mistakenly scrubbing a row that's
        # actually still present is an acceptable trade-off against the
        # alternative this job exists to prevent (a real deletion missed
        # because a fetch error was mistaken for "still there"), and
        # nothing about this job's design can "undo" a scrub — deleted_at
        # is never cleared once set — so this trade-off is made
        # deliberately, not by accident of a bare except.
        return True
    return body.strip() in DELETED_MARKERS


def _scrub(mention: Mention) -> None:
    """Clears content/author-identifying fields and marks deleted_at —
    see module docstring's "Detection" section for exactly what is and
    isn't cleared, and why."""
    mention.text = None
    mention.author = None
    mention.raw_payload = None
    mention.deleted_at = datetime.now(timezone.utc)


def run(session: Session) -> None:
    """One deletion-check pass over up to BATCH_SIZE stored Reddit rows.
    See module docstring for the full design, and for what items_seen/
    items_ingested mean for THIS job specifically (repurposed from their
    usual ingestion meaning — documented there so a future reader of the
    ledger isn't confused)."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        missing = fetch_reddit_mentions.missing_credentials()
        if missing:
            # Same reasoning as reddit_job.py: reported as an ERROR run
            # rather than letting get_reddit_client()'s own SystemExit
            # propagate out of a scheduled job.
            recorder.mark(
                RunStatus.ERROR,
                error="Missing required Reddit credential(s): " + ", ".join(missing),
            )
            return

        reddit = get_reddit_client()
        batch = _select_batch(session)

        for mention in batch:
            recorder.items_seen += 1

            kind = fullname_kind(mention.external_id)
            if kind is None:
                # No usable "t3_"/"t1_" fullname to re-check (shouldn't
                # happen for a row reddit_job.py wrote, but a hand-
                # inserted or otherwise malformed row might lack one) -
                # skip rather than guess which PRAW call to make.
                continue

            if _is_deleted_upstream(reddit, mention.external_id, kind):
                _scrub(mention)
                recorder.items_ingested += 1

        # start_run()'s own default inference (no explicit mark()) reads
        # "items_ingested < items_seen" as PARTIAL - a reasonable default
        # for an INGESTION job, where that gap means some items failed to
        # write. It is the wrong read for THIS job: items_ingested here
        # means "rows found deleted and scrubbed," so a clean run where
        # nothing needed scrubbing (the common, fully-successful case)
        # would otherwise be mislabeled PARTIAL. Mark SUCCESS explicitly
        # whenever this loop completes without raising - a skipped row
        # (unrecognized fullname prefix, see the `continue` above) is
        # already a rare, defensive edge case, not a failure of this run.
        recorder.mark(RunStatus.SUCCESS)
