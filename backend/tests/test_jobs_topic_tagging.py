"""Tests for app/jobs/topic_tagging_job.py.

Deliberately mirrors test_jobs_classification.py: this job exists because
topic tagging had exactly the defect classification had — a complete,
tested module that nothing ever called — so the job that fixes it should
be held to the same contract, including the ledger semantics and the
one exception it handles by name.

app.topic_tagging.tag_untagged_batch is monkeypatched in this job's own
namespace (it imports the name directly), so no model call is made.
"""

from sqlalchemy import select

import app.jobs.topic_tagging_job as topic_tagging_job
from app.classification import ClassifierNotConfiguredError
from app.models import IngestionRun, RunStatus


def _latest_run(session):
    return session.execute(
        select(IngestionRun)
        .where(IngestionRun.source == "topic_tagging")
        .order_by(IngestionRun.started_at.desc())
    ).scalars().first()


def test_run_records_the_tagged_count_on_both_counters(sqlite_session, monkeypatch):
    """items_seen and items_ingested are both "rows tagged this run" - the
    same repurposing classification_job.py and reddit_deletion_job.py
    document, since there is no per-item partial-failure state at this
    layer to report separately."""
    monkeypatch.setattr(topic_tagging_job, "tag_untagged_batch", lambda session, limit: 7)

    topic_tagging_job.run(sqlite_session)

    run = _latest_run(sqlite_session)
    assert run.status == RunStatus.SUCCESS
    assert run.items_seen == 7
    assert run.items_ingested == 7


def test_run_passes_the_module_batch_size(sqlite_session, monkeypatch):
    captured = {}

    def fake_batch(session, limit):
        captured["limit"] = limit
        return 0

    monkeypatch.setattr(topic_tagging_job, "tag_untagged_batch", fake_batch)

    topic_tagging_job.run(sqlite_session)

    assert captured["limit"] == topic_tagging_job.BATCH_SIZE


def test_run_with_nothing_to_tag_is_a_clean_success(sqlite_session, monkeypatch):
    """Every row already tagged is the steady state, not a problem."""
    monkeypatch.setattr(topic_tagging_job, "tag_untagged_batch", lambda session, limit: 0)

    topic_tagging_job.run(sqlite_session)

    assert _latest_run(sqlite_session).status == RunStatus.SUCCESS


def test_missing_api_key_is_an_error_run_with_the_real_reason(sqlite_session, monkeypatch):
    """Recorded in the ledger rather than allowed to escape into
    scheduler.run_due_jobs(). This is the exact failure a live run hit -
    the scheduler invoked with the system Python instead of the project
    venv - and the ledger saying so in words is what made it
    diagnosable."""

    def not_configured(session, limit):
        raise ClassifierNotConfiguredError(
            "The `groq` package is not installed. Topic tagging needs the Groq Python SDK."
        )

    monkeypatch.setattr(topic_tagging_job, "tag_untagged_batch", not_configured)

    topic_tagging_job.run(sqlite_session)  # must not raise

    run = _latest_run(sqlite_session)
    assert run.status == RunStatus.ERROR
    assert "groq" in run.error


def test_job_is_not_counted_as_a_data_source():
    """It re-processes rows another job fetched, so it must not move
    lastSyncedAt - the one thing the Overview sync pill exists to answer
    is whether the DATA is stale."""
    assert topic_tagging_job.IS_DATA_SOURCE is False


def test_job_is_registered_in_the_scheduler_with_its_own_cadence():
    """The whole defect this job fixes was a complete module that nothing
    called. Registration is the part that was missing, so it is the part
    worth a test."""
    from app.jobs import JOBS
    from app.scheduler import _cadence_for

    assert topic_tagging_job in JOBS
    # Slower than the classifier's 15 minutes: a topic is an aggregate
    # reporting attribute, and nobody is paged over an uncategorized
    # mention. See the job's module docstring.
    assert _cadence_for("topic_tagging") == 1.0
