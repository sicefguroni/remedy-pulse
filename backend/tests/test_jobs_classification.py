"""Tests for app/jobs/classification_job.py (checklist 0.23) — the
scheduled job that actually calls
app.classification.classify_unclassified_batch(). The LLM call is always
mocked here too, same rule as test_classification.py: monkeypatch
app.classification._call_model, never make a real Groq API call.
"""

import json

from sqlalchemy import select

import app.classification as classification
from app.jobs import classification_job
from app.models import IngestionRun, Mention, RunStatus
from app.repository import get_source_freshness


def _make_mention(session, **overrides) -> Mention:
    fields = dict(source="google_reviews", kind="review", external_id="rev-1", text="Great service!")
    fields.update(overrides)
    mention = Mention(**fields)
    session.add(mention)
    session.flush()
    return mention


def _well_formed_response(**overrides) -> str:
    data = {
        "sentiment": "Positive",
        "confidence": 0.8,
        "alert_category": "digest",
        "reasoning": "Routine positive review.",
    }
    data.update(overrides)
    return json.dumps(data)


def test_run_classifies_unclassified_mentions_and_marks_success(sqlite_session, monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())
    _make_mention(sqlite_session, external_id="rev-1")
    _make_mention(sqlite_session, external_id="rev-2")
    sqlite_session.commit()

    classification_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.source == classification_job.SOURCE_NAME
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 2

    mentions = sqlite_session.execute(select(Mention).order_by(Mention.external_id)).scalars().all()
    assert all(m.classified_at is not None for m in mentions)
    assert all(m.sentiment == "Positive" for m in mentions)


def test_run_with_nothing_to_classify_marks_success_not_partial(sqlite_session):
    """items_seen == items_ingested == 0 must not be misread as PARTIAL -
    start_run()'s own default inference only marks PARTIAL when
    items_seen is truthy AND items_ingested < items_seen; a genuinely
    empty backlog is a clean, successful "nothing to do" run."""
    classification_job.run(sqlite_session)
    sqlite_session.commit()

    freshness = get_source_freshness(sqlite_session, classification_job.SOURCE_NAME)
    assert freshness.last_status == RunStatus.SUCCESS
    assert freshness.last_error is None


def test_run_missing_api_key_marks_error_without_crashing(sqlite_session, monkeypatch):
    """ClassifierNotConfiguredError (GROQ_API_KEY unset) must be recorded
    as an ERROR run with the real reason, not left to propagate out of
    this job uncaught - the same ledger-visibility guarantee every other
    job in app/jobs/ already gives a missing credential (checklist 0.21's
    google_reviews_job/news_job fix, and reddit_job/reddit_deletion_job's
    pre-existing upfront check)."""
    monkeypatch.setattr(classification, "GROQ_API_KEY", None)
    _make_mention(sqlite_session, external_id="rev-1")
    sqlite_session.commit()

    classification_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ERROR
    assert "GROQ_API_KEY" in run_row.error

    # Nothing was classified - the failure surfaced on the first mention
    # attempted, before anything was written.
    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.classified_at is None


def test_run_respects_batch_size(sqlite_session, monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())
    monkeypatch.setattr(classification_job, "BATCH_SIZE", 2)
    for i in range(5):
        _make_mention(sqlite_session, external_id=f"rev-{i}")
    sqlite_session.commit()

    classification_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 2

    classified_count = sqlite_session.execute(
        select(Mention).where(Mention.classified_at.isnot(None))
    ).scalars().all()
    assert len(classified_count) == 2


def test_classification_job_registered_in_jobs_registry_with_own_source_name():
    from app.jobs import JOBS

    assert classification_job in JOBS
    assert classification_job.SOURCE_NAME == "classification"


def test_classification_job_has_a_much_shorter_cadence_than_the_default():
    """The PRD's core v1 metric (median time from a negative mention
    appearing to being assigned, target under 4 business hours) can't be
    met if a mention sits unclassified for up to the 12h ingestion-job
    default - see app.scheduler.CADENCE_HOURS's own comment."""
    from app.scheduler import CADENCE_HOURS, DEFAULT_CADENCE_HOURS

    assert CADENCE_HOURS["classification"] < DEFAULT_CADENCE_HOURS
