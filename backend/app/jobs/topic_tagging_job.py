"""app/jobs/topic_tagging_job.py — the scheduled job that calls
app.topic_tagging.tag_untagged_batch().

Why this exists
----------------
The same defect classification_job.py was written to fix, one module
over, still open. app/topic_tagging.py is complete and tested and exposes
tag_untagged_batch() as "what a scheduled job would call" — and nothing
called it. Every Mention any ingestion job wrote sat at topics=NULL
forever.

That is not a cosmetic gap. The Topics tab reads
app.repository.get_topics_summary(), which reads Mention.topics; the
topic sentiment split (P0-8) reads the same column. With every row at
NULL, that whole tab renders empty against a database full of real,
correctly-classified mentions — a working pipeline that looks broken,
which is indistinguishable from the reverse to anyone looking at the
screen.

It was found the only way this class of bug is ever found: by running
the pipeline end to end on real data and looking at what the columns
actually contained afterwards. 48 rows classified, 48 rows with topics
NULL.

Relationship to classification_job.py
--------------------------------------
Two separate jobs, deliberately, not one job making both model calls per
mention:

  - They fail independently and for different reasons. Topic tagging
    degrades a failed call to [] per tag_topics()'s own contract;
    sentiment classification degrades to a low-confidence Neutral/digest.
    Merged, one module's bad day would be recorded under the other's
    ledger source, and "topics are stale but sentiment is current" would
    stop being expressible — the same reasoning meta_job.py gives for
    keeping three ledger sources rather than one.
  - Their cadences answer to different deadlines. Sentiment feeds the
    PRD's core v1 metric (a negative mention routed to a human in under
    4 business hours), which is why classification runs every 15 minutes.
    A topic is an aggregate-reporting attribute; nobody is paged because
    a mention was not categorized. See app.scheduler.CADENCE_HOURS.

Both are IS_DATA_SOURCE = False for the same reason: they re-process rows
another job already fetched, so their status has no bearing on "is the
DATA stale", which is the one question the Overview tab's lastSyncedAt
and the sync pill exist to answer.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.classification import ClassifierNotConfiguredError
from app.models import RunStatus
from app.repository import start_run
from app.topic_tagging import tag_untagged_batch

SOURCE_NAME = "topic_tagging"

# See app/jobs/__init__.py's contract and classification_job.py's
# identical flag: this job writes no new Mention rows, so it must not
# count toward lastSyncedAt.
IS_DATA_SOURCE = False

# Matches classification_job.BATCH_SIZE. One model call per mention, same
# as the classifier, so the same batch size means one pass of each costs
# comparably and neither silently becomes the bottleneck the other waits
# behind.
BATCH_SIZE = 100


def run(session: Session) -> None:
    """One tagging pass: tag up to BATCH_SIZE never-tagged mentions and
    record the outcome in the ingestion ledger under SOURCE_NAME.

    items_seen and items_ingested are both the count tagged, the same
    repurposing classification_job.py and reddit_deletion_job.py document
    — there is no per-item partial-failure state to report separately at
    this layer, because tag_topics() already degrades a failed call to a
    stored [] rather than raising.

    ClassifierNotConfiguredError is the one exception that propagates out
    of tag_untagged_batch() (no GROQ_API_KEY, or the groq package
    missing). It is caught and recorded as an ERROR run with the real
    reason, rather than left to escape into app.scheduler.run_due_jobs()
    — matching every sibling job's convention. That specific error is
    worth catching by name rather than broadly: it is the exact failure
    a live run hit when the scheduler was invoked with the system Python
    instead of the project venv, and the ledger saying so in words is
    what made it diagnosable at all."""
    with start_run(session, source=SOURCE_NAME) as recorder:
        try:
            count = tag_untagged_batch(session, limit=BATCH_SIZE)
        except ClassifierNotConfiguredError as exc:
            recorder.mark(RunStatus.ERROR, error=str(exc))
            return
        recorder.items_seen = count
        recorder.items_ingested = count
