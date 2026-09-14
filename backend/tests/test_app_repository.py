"""Repository tests against an in-memory SQLite database (see
conftest.sqlite_session). The same upsert/ledger code path is also
verified against a real Postgres container — see
test_app_repository_postgres.py — this file covers the logic quickly and
without any external dependency, for every CI run."""

import pytest
from sqlalchemy import func, select

from app.models import IngestionRun, Mention, RunStatus
from app.repository import (
    get_source_freshness,
    start_run,
    upsert_mention,
    upsert_mentions,
)


def test_upsert_mention_requires_source_and_external_id(sqlite_session):
    with pytest.raises(ValueError, match="source and external_id"):
        upsert_mention(sqlite_session, kind="review", external_id="x")
    with pytest.raises(ValueError, match="source and external_id"):
        upsert_mention(sqlite_session, source="google_reviews", kind="review")


def test_upsert_mention_inserts_new_row(sqlite_session):
    upsert_mention(
        sqlite_session, source="google_reviews", kind="review",
        external_id="rev-1", rating=5, text="Great",
    )
    sqlite_session.commit()
    row = sqlite_session.execute(select(Mention)).scalar_one()
    assert row.source == "google_reviews"
    assert row.external_id == "rev-1"
    assert row.rating == 5


def test_upsert_mention_is_idempotent_on_source_and_external_id(sqlite_session):
    upsert_mention(sqlite_session, source="google_reviews", kind="review", external_id="rev-1", rating=3)
    sqlite_session.commit()
    upsert_mention(sqlite_session, source="google_reviews", kind="review", external_id="rev-1", rating=3)
    sqlite_session.commit()

    count = sqlite_session.execute(select(func.count()).select_from(Mention)).scalar_one()
    assert count == 1


def test_upsert_mention_updates_in_place_last_write_wins(sqlite_session):
    upsert_mention(sqlite_session, source="google_reviews", kind="review", external_id="rev-1",
                   rating=3, text="original")
    sqlite_session.commit()

    upsert_mention(sqlite_session, source="google_reviews", kind="review", external_id="rev-1",
                   rating=5, text="revised")
    sqlite_session.commit()

    row = sqlite_session.execute(select(Mention)).scalar_one()
    assert row.rating == 5
    assert row.text == "revised"


def test_upsert_mention_different_external_id_same_source_is_a_new_row(sqlite_session):
    upsert_mention(sqlite_session, source="reddit", kind="mention", external_id="t3_a")
    upsert_mention(sqlite_session, source="reddit", kind="mention", external_id="t3_b")
    sqlite_session.commit()
    count = sqlite_session.execute(select(func.count()).select_from(Mention)).scalar_one()
    assert count == 2


def test_upsert_mention_same_external_id_different_source_is_a_new_row(sqlite_session):
    # (source, external_id) is the composite key - a Reddit post ID and a
    # Google review ID happening to collide as strings must not merge.
    upsert_mention(sqlite_session, source="reddit", kind="mention", external_id="1")
    upsert_mention(sqlite_session, source="google_reviews", kind="review", external_id="1")
    sqlite_session.commit()
    count = sqlite_session.execute(select(func.count()).select_from(Mention)).scalar_one()
    assert count == 2


def test_upsert_mentions_batch(sqlite_session):
    items = [
        {"source": "reddit", "kind": "mention", "external_id": f"t3_{i}"}
        for i in range(4)
    ]
    n = upsert_mentions(sqlite_session, items)
    sqlite_session.commit()
    assert n == 4
    count = sqlite_session.execute(select(func.count()).select_from(Mention)).scalar_one()
    assert count == 4


def test_start_run_marks_success_on_clean_exit(sqlite_session):
    with start_run(sqlite_session, source="google_reviews") as run:
        run.items_seen = 3
        run.items_ingested = 3
    sqlite_session.commit()

    assert run.run.status == RunStatus.SUCCESS
    assert run.run.finished_at is not None
    assert run.run.items_seen == 3
    assert run.run.items_ingested == 3


def test_start_run_marks_partial_when_fewer_ingested_than_seen(sqlite_session):
    with start_run(sqlite_session, source="reddit") as run:
        run.items_seen = 5
        run.items_ingested = 3
    sqlite_session.commit()

    assert run.run.status == RunStatus.PARTIAL


def test_start_run_marks_error_and_reraises_on_exception(sqlite_session):
    with pytest.raises(RuntimeError, match="boom"):
        with start_run(sqlite_session, source="reddit") as run:
            run.items_seen = 1
            raise RuntimeError("boom")
    sqlite_session.commit()

    assert run.run.status == RunStatus.ERROR
    assert run.run.error == "boom"
    assert run.run.finished_at is not None


def test_start_run_respects_explicit_mark_call(sqlite_session):
    with start_run(sqlite_session, source="google_places_competitor") as run:
        run.items_seen = 2
        run.items_ingested = 0
        run.mark(RunStatus.ACCESS_DENIED, error="403 from Places API")
    sqlite_session.commit()

    assert run.run.status == RunStatus.ACCESS_DENIED
    assert run.run.error == "403 from Places API"


def test_get_source_freshness_no_runs_yet(sqlite_session):
    fresh = get_source_freshness(sqlite_session, "never_run_source")
    assert fresh.last_attempt_at is None
    assert fresh.last_success_at is None
    assert fresh.last_status is None


def test_get_source_freshness_distinguishes_last_attempt_from_last_success(sqlite_session):
    with start_run(sqlite_session, source="reddit"):
        pass  # succeeds
    sqlite_session.commit()

    try:
        with start_run(sqlite_session, source="reddit"):
            raise RuntimeError("transient failure")
    except RuntimeError:
        pass
    sqlite_session.commit()

    fresh = get_source_freshness(sqlite_session, "reddit")
    # The most recent attempt failed, but a successful run happened before
    # it - both facts must be independently recoverable, which is exactly
    # the "Reddit is 3 days stale but Google is current" scenario 2.4's
    # docstring describes.
    assert fresh.last_status == RunStatus.ERROR
    assert fresh.last_success_at is not None
    assert fresh.last_attempt_at is not None


def test_get_source_freshness_is_scoped_per_source(sqlite_session):
    with start_run(sqlite_session, source="google_reviews"):
        pass
    sqlite_session.commit()

    fresh_other = get_source_freshness(sqlite_session, "reddit")
    assert fresh_other.last_attempt_at is None


def test_ingestion_run_row_is_queryable_directly(sqlite_session):
    with start_run(sqlite_session, source="news_gnews") as run:
        run.items_seen = 10
        run.items_ingested = 10
    sqlite_session.commit()

    row = sqlite_session.execute(
        select(IngestionRun).where(IngestionRun.source == "news_gnews")
    ).scalar_one()
    assert row.status == RunStatus.SUCCESS
    assert row.items_ingested == 10


# --- enrichment preservation on re-ingest (see repository.ENRICHMENT_FIELDS) ---
#
# These cover a bug that was live and silent: every ingestion adapter
# passes sentiment=None (correctly - a connector must not invent one), and
# the upsert's last-write-wins rule applied that NULL to the stored value,
# erasing the classifier's verdict on every 12-hourly re-fetch. Because
# classified_at is not among the fields an adapter passes, it survived -
# leaving the row classified_at-set and sentiment-NULL, which
# classify_unclassified_batch() never selects again. The mention stayed
# blank forever while every status surface called it classified.
#
# Only a SECOND ingestion pass over the same item can produce it, which is
# why one clean run and a green suite both missed it.


def _classify(session, mention):
    """Stand in for app.jobs.classification_job + topic_tagging_job having
    run over this row, without calling a model."""
    from datetime import datetime, timezone

    mention.sentiment = "Negative"
    mention.sentiment_confidence = 0.93
    mention.alert_category = "crisis"
    mention.topics = ["staff-service"]
    mention.classified_at = datetime.now(timezone.utc)
    session.flush()


def _reingest(session, **overrides):
    fields = {
        "source": "reddit_public",
        "kind": "mention",
        "external_id": "t3_abc",
        "text": "Terrible service at the BGC branch",
        # Exactly what every ingestion adapter passes.
        "sentiment": None,
        **overrides,
    }
    upsert_mention(session, **fields)
    session.flush()


def test_reingest_does_not_erase_the_classifier_verdict(sqlite_session):
    _reingest(sqlite_session)
    mention = sqlite_session.execute(select(Mention)).scalars().one()
    _classify(sqlite_session, mention)

    _reingest(sqlite_session)  # the next 12-hourly pass

    sqlite_session.refresh(mention)
    assert mention.sentiment == "Negative"
    assert mention.sentiment_confidence == 0.93
    assert mention.alert_category == "crisis"
    assert mention.topics == ["staff-service"]


def test_reingest_still_updates_fields_the_adapter_owns(sqlite_session):
    """The narrowing must not turn into "never update anything". A review
    whose text changed upstream should still reflect the new state -
    last-write-wins remains correct for adapter-owned fields."""
    _reingest(sqlite_session)
    mention = sqlite_session.execute(select(Mention)).scalars().one()
    _classify(sqlite_session, mention)

    _reingest(sqlite_session, text="Edited: actually they fixed it", url="https://example.com/new")

    sqlite_session.refresh(mention)
    assert mention.text == "Edited: actually they fixed it"
    assert mention.url == "https://example.com/new"
    assert mention.sentiment == "Negative"  # still preserved


def test_a_real_incoming_sentiment_still_overwrites(sqlite_session):
    """google_reviews_job derives a placeholder sentiment from the star
    rating (6.2). A non-NULL incoming value is a real value and must win -
    this preserves NULLs only, not every prior value."""
    _reingest(sqlite_session, source="google_reviews", kind="review", sentiment="Positive")
    mention = sqlite_session.execute(select(Mention)).scalars().one()
    _classify(sqlite_session, mention)

    _reingest(sqlite_session, source="google_reviews", kind="review", sentiment="Positive")

    sqlite_session.refresh(mention)
    assert mention.sentiment == "Positive"


def test_reingest_leaves_the_row_reclassifiable_if_never_classified(sqlite_session):
    """A row ingested twice before the classifier ever saw it must still
    be picked up: classified_at stays NULL, sentiment stays NULL."""
    _reingest(sqlite_session)
    _reingest(sqlite_session)

    mention = sqlite_session.execute(select(Mention)).scalars().one()
    assert mention.sentiment is None
    assert mention.classified_at is None
