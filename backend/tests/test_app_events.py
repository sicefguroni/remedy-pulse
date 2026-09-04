"""Tests for the Phase 3 instrumentation layer: the event log (3.1), the
core assignment-time metric (3.2), and the 3.3 baseline helpers. Runs
against in-memory SQLite (see conftest.sqlite_session) — the trickiest
semantics (record_ingestion's dedup-on-reingest, assign_mention's
first-assignment-wins) are also verified against a real Postgres
container in test_app_events_postgres.py."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Event, EventType, Mention, ResponseTimeBaseline, Sentiment
from app.repository import (
    assign_mention,
    get_baseline_summary,
    get_export_activity,
    get_median_time_to_assignment,
    log_event,
    log_export,
    log_login,
    record_baseline_response_time,
    record_ingestion,
    resolve_mention,
)

# --- log_event basics ---


def test_log_event_stores_all_fields(sqlite_session):
    event = log_event(
        sqlite_session,
        EventType.LOGIN,
        mention_id=42,
        actor="gian",
        metadata={"foo": "bar"},
    )
    sqlite_session.commit()

    row = sqlite_session.execute(select(Event)).scalar_one()
    assert row.id == event.id
    assert row.event_type == EventType.LOGIN
    assert row.mention_id == 42
    assert row.actor == "gian"
    assert row.metadata_json == {"foo": "bar"}
    assert row.occurred_at is not None


def test_log_event_defaults_are_none(sqlite_session):
    log_event(sqlite_session, EventType.EXPORT_DOWNLOADED)
    sqlite_session.commit()

    row = sqlite_session.execute(select(Event)).scalar_one()
    assert row.mention_id is None
    assert row.actor is None
    assert row.metadata_json is None


# --- record_ingestion ---


def test_record_ingestion_fires_item_ingested_on_first_insert(sqlite_session):
    inserted = record_ingestion(
        sqlite_session, source="google_reviews", kind="review", external_id="rev-1", rating=5
    )
    sqlite_session.commit()

    assert inserted is True
    events = sqlite_session.execute(select(Event)).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == EventType.ITEM_INGESTED

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert events[0].mention_id == mention.id


def test_record_ingestion_does_not_refire_on_reingest(sqlite_session):
    inserted_first = record_ingestion(
        sqlite_session, source="google_reviews", kind="review", external_id="rev-1", rating=3
    )
    sqlite_session.commit()

    inserted_second = record_ingestion(
        sqlite_session, source="google_reviews", kind="review", external_id="rev-1", rating=5
    )
    sqlite_session.commit()

    assert inserted_first is True
    assert inserted_second is False

    events = sqlite_session.execute(select(Event)).scalars().all()
    assert len(events) == 1  # only the first insert logged an event

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.rating == 5  # last-write-wins still applies to the row itself


# --- assign_mention ---


def _make_mention(session, **overrides) -> Mention:
    fields = dict(source="google_reviews", kind="review", external_id="rev-1", sentiment=Sentiment.NEGATIVE)
    fields.update(overrides)
    mention = Mention(**fields)
    session.add(mention)
    session.flush()
    return mention


def test_assign_mention_first_call_sets_assigned_to_and_assigned_at(sqlite_session):
    mention = _make_mention(sqlite_session)
    sqlite_session.commit()

    assign_mention(sqlite_session, mention.id, "Gian")
    sqlite_session.commit()

    row = sqlite_session.get(Mention, mention.id)
    assert row.assigned_to == "Gian"
    assert row.assigned_at is not None


def test_assign_mention_reassignment_updates_assignee_but_not_assigned_at(sqlite_session):
    mention = _make_mention(sqlite_session)
    sqlite_session.commit()

    assign_mention(sqlite_session, mention.id, "Gian")
    sqlite_session.commit()
    row = sqlite_session.get(Mention, mention.id)
    first_assigned_at = row.assigned_at

    assign_mention(sqlite_session, mention.id, "Paul")
    sqlite_session.commit()
    row = sqlite_session.get(Mention, mention.id)

    assert row.assigned_to == "Paul"
    # The whole point of first-assignment-wins: a reassignment must never
    # move assigned_at once it's set.
    assert row.assigned_at == first_assigned_at


def test_assign_mention_logs_an_event_every_call(sqlite_session):
    mention = _make_mention(sqlite_session)
    sqlite_session.commit()

    assign_mention(sqlite_session, mention.id, "Gian")
    sqlite_session.commit()
    assign_mention(sqlite_session, mention.id, "Paul")
    sqlite_session.commit()

    events = sqlite_session.execute(
        select(Event).where(Event.event_type == EventType.ITEM_ASSIGNED)
    ).scalars().all()
    assert len(events) == 2
    assert events[0].metadata_json == {"assignee": "Gian"}
    assert events[1].metadata_json == {"assignee": "Paul"}
    assert all(e.mention_id == mention.id for e in events)


def test_assign_mention_raises_value_error_for_unknown_id(sqlite_session):
    with pytest.raises(ValueError, match="999"):
        assign_mention(sqlite_session, 999, "Gian")


# --- resolve_mention ---


def test_resolve_mention_sets_resolved_at_and_logs_event(sqlite_session):
    mention = _make_mention(sqlite_session)
    sqlite_session.commit()

    resolve_mention(sqlite_session, mention.id)
    sqlite_session.commit()

    row = sqlite_session.get(Mention, mention.id)
    assert row.resolved_at is not None

    events = sqlite_session.execute(
        select(Event).where(Event.event_type == EventType.ITEM_RESOLVED)
    ).scalars().all()
    assert len(events) == 1
    assert events[0].mention_id == mention.id


def test_resolve_mention_raises_value_error_for_unknown_id(sqlite_session):
    with pytest.raises(ValueError, match="999"):
        resolve_mention(sqlite_session, 999)


# --- log_export / get_export_activity ---


def test_log_export_records_export_type_and_item_count(sqlite_session):
    log_export(sqlite_session, "mentions_csv", actor="paul", item_count=42)
    sqlite_session.commit()

    row = sqlite_session.execute(select(Event)).scalar_one()
    assert row.event_type == EventType.EXPORT_DOWNLOADED
    assert row.actor == "paul"
    assert row.metadata_json == {"export_type": "mentions_csv", "item_count": 42}


def test_get_export_activity_counts_within_window_and_excludes_outside(sqlite_session):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    # Inside the window.
    log_export(sqlite_session, "mentions_csv")
    sqlite_session.commit()

    # Force an event outside the window by inserting directly with an
    # explicit old occurred_at, rather than relying on the wall clock.
    old_event = Event(event_type=EventType.EXPORT_DOWNLOADED, occurred_at=now - timedelta(days=30))
    sqlite_session.add(old_event)
    sqlite_session.commit()

    count = get_export_activity(sqlite_session, since=since)
    assert count == 1


def test_get_export_activity_boundary_is_inclusive(sqlite_session):
    since = datetime(2026, 1, 8, tzinfo=timezone.utc)
    on_boundary = Event(event_type=EventType.EXPORT_DOWNLOADED, occurred_at=since)
    sqlite_session.add(on_boundary)
    sqlite_session.commit()

    count = get_export_activity(sqlite_session, since=since)
    assert count == 1


def test_get_export_activity_boundary_excludes_just_before(sqlite_session):
    since = datetime(2026, 1, 8, tzinfo=timezone.utc)
    just_before = Event(event_type=EventType.EXPORT_DOWNLOADED, occurred_at=since - timedelta(seconds=1))
    sqlite_session.add(just_before)
    sqlite_session.commit()

    count = get_export_activity(sqlite_session, since=since)
    assert count == 0


# --- log_login ---


def test_log_login_records_actor(sqlite_session):
    log_login(sqlite_session, actor="mixi")
    sqlite_session.commit()

    row = sqlite_session.execute(select(Event)).scalar_one()
    assert row.event_type == EventType.LOGIN
    assert row.actor == "mixi"


# --- get_median_time_to_assignment ---


def _insert_mention_with_timestamps(session, *, sentiment, ingested_at, assigned_at, external_id):
    mention = Mention(
        source="google_reviews",
        kind="review",
        external_id=external_id,
        sentiment=sentiment,
        ingested_at=ingested_at,
        assigned_at=assigned_at,
    )
    session.add(mention)
    return mention


def test_get_median_time_to_assignment_hand_computed(sqlite_session):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Deltas (hours): 1, 2, 3, 10 -> median of [1, 2, 3, 10] is (2+3)/2 = 2.5
    deltas = [1, 2, 3, 10]
    for i, hours in enumerate(deltas):
        _insert_mention_with_timestamps(
            sqlite_session,
            sentiment=Sentiment.NEGATIVE,
            ingested_at=base,
            assigned_at=base + timedelta(hours=hours),
            external_id=f"neg-{i}",
        )
    sqlite_session.commit()

    median = get_median_time_to_assignment(sqlite_session)
    assert median == pytest.approx(2.5)


def test_get_median_time_to_assignment_excludes_non_negative_sentiment(sqlite_session):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=base,
        assigned_at=base + timedelta(hours=1), external_id="neg-1",
    )
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.POSITIVE, ingested_at=base,
        assigned_at=base + timedelta(hours=100), external_id="pos-1",
    )
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEUTRAL, ingested_at=base,
        assigned_at=base + timedelta(hours=100), external_id="neu-1",
    )
    sqlite_session.commit()

    median = get_median_time_to_assignment(sqlite_session)
    assert median == pytest.approx(1.0)


def test_get_median_time_to_assignment_excludes_unassigned_rows(sqlite_session):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=base,
        assigned_at=base + timedelta(hours=1), external_id="neg-1",
    )
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=base,
        assigned_at=None, external_id="neg-unassigned",
    )
    sqlite_session.commit()

    median = get_median_time_to_assignment(sqlite_session)
    assert median == pytest.approx(1.0)


def test_get_median_time_to_assignment_returns_none_with_no_qualifying_rows(sqlite_session):
    assert get_median_time_to_assignment(sqlite_session) is None

    # Also None when a negative row exists but is unassigned.
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=base,
        assigned_at=None, external_id="neg-unassigned",
    )
    sqlite_session.commit()
    assert get_median_time_to_assignment(sqlite_session) is None


def test_get_median_time_to_assignment_respects_since(sqlite_session):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=old,
        assigned_at=old + timedelta(hours=99), external_id="neg-old",
    )
    _insert_mention_with_timestamps(
        sqlite_session, sentiment=Sentiment.NEGATIVE, ingested_at=recent,
        assigned_at=recent + timedelta(hours=1), external_id="neg-recent",
    )
    sqlite_session.commit()

    median = get_median_time_to_assignment(sqlite_session, since=datetime(2025, 6, 1, tzinfo=timezone.utc))
    assert median == pytest.approx(1.0)


# --- record_baseline_response_time / get_baseline_summary ---


def test_record_baseline_response_time_inserts_row(sqlite_session):
    baseline = record_baseline_response_time(
        sqlite_session,
        source_description="Google review reply, Remedy BGC, 2-star, June 2026",
        response_time_hours=12.5,
        captured_by="paul",
        notes="Manual lookup from GBP dashboard",
    )
    sqlite_session.commit()

    row = sqlite_session.execute(select(ResponseTimeBaseline)).scalar_one()
    assert row.id == baseline.id
    assert row.response_time_hours == 12.5
    assert row.captured_by == "paul"
    assert row.notes == "Manual lookup from GBP dashboard"
    assert row.captured_at is not None


def test_get_baseline_summary_computes_count_median_mean(sqlite_session):
    for hours in (4.0, 8.0, 12.0):
        record_baseline_response_time(
            sqlite_session, source_description="x", response_time_hours=hours, captured_by="paul"
        )
    sqlite_session.commit()

    summary = get_baseline_summary(sqlite_session)
    assert summary["count"] == 3
    assert summary["median_hours"] == pytest.approx(8.0)
    assert summary["mean_hours"] == pytest.approx(8.0)


def test_get_baseline_summary_returns_none_not_zero_with_no_rows(sqlite_session):
    summary = get_baseline_summary(sqlite_session)
    assert summary["count"] == 0
    assert summary["no_reply_count"] == 0
    assert summary["median_hours"] is None
    assert summary["mean_hours"] is None


def test_record_baseline_response_time_accepts_none_for_no_reply_yet(sqlite_session):
    # A review with no reply as of the capture date is a real outcome
    # (see docs/response-time-baseline-template.md), not something the
    # caller should have to guess a number for.
    baseline = record_baseline_response_time(
        sqlite_session,
        source_description="Remedy Greenhills, 1-star, posted 2026-06-02",
        response_time_hours=None,
        captured_by="paul",
        notes="No reply as of 2026-09-04",
    )
    sqlite_session.commit()

    row = sqlite_session.execute(select(ResponseTimeBaseline)).scalar_one()
    assert row.id == baseline.id
    assert row.response_time_hours is None
    assert row.notes == "No reply as of 2026-09-04"


def test_get_baseline_summary_excludes_no_reply_rows_from_median_but_counts_them(sqlite_session):
    record_baseline_response_time(
        sqlite_session, source_description="a", response_time_hours=4.0, captured_by="paul"
    )
    record_baseline_response_time(
        sqlite_session, source_description="b", response_time_hours=12.0, captured_by="paul"
    )
    record_baseline_response_time(
        sqlite_session, source_description="c", response_time_hours=None, captured_by="paul",
        notes="no reply yet",
    )
    sqlite_session.commit()

    summary = get_baseline_summary(sqlite_session)
    # All three captured rows count toward `count` - a no-reply row is
    # captured data, not an omission.
    assert summary["count"] == 3
    assert summary["no_reply_count"] == 1
    # But the median/mean are over the two rows that actually have a
    # time-to-reply - a no-reply row has no time to average in.
    assert summary["median_hours"] == pytest.approx(8.0)
    assert summary["mean_hours"] == pytest.approx(8.0)
