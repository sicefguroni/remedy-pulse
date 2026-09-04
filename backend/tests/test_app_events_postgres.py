"""Integration tests for the two trickiest Phase 3 semantics —
record_ingestion()'s dedup-on-reingest behavior and assign_mention()'s
first-assignment-wins behavior — against a REAL Postgres database, not
just an analogous SQLite path. See test_app_repository_postgres.py's
module docstring for why this project keeps a dedicated Postgres suite
alongside the SQLite-backed one; this file follows that exact same
skip-cleanly-when-unreachable pattern.

Skips automatically (not a failure) if no Postgres is reachable — CI
doesn't run one; a local `docker compose up -d` in backend/ does. Reuses
the same `remedy_pulse_test` database as test_app_repository_postgres.py,
dropping/recreating tables around every test so runs never see another
run's leftover rows.
"""

import os

import pytest
import sqlalchemy
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import make_engine
from app.models import Base, Event, EventType, Mention, Sentiment
from app.repository import assign_mention, record_ingestion

_BASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://remedy_pulse:remedy_pulse_dev@localhost:5434/remedy_pulse",
)
_TEST_URL = _BASE_URL.rsplit("/", 1)[0] + "/remedy_pulse_test"


def _postgres_reachable() -> bool:
    try:
        admin_engine = make_engine(_BASE_URL)
        with admin_engine.connect():
            pass
        admin_engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="No reachable Postgres (expected in CI) - run `docker compose up -d` "
    "in backend/ to exercise this suite locally.",
)


@pytest.fixture
def pg_session():
    admin_engine = make_engine(_BASE_URL).execution_options(isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        try:
            conn.execute(sqlalchemy.text("CREATE DATABASE remedy_pulse_test"))
        except Exception:
            pass  # already exists from a previous run
    admin_engine.dispose()

    engine = make_engine(_TEST_URL)
    Base.metadata.drop_all(engine)  # start every test from a clean schema
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_postgres_record_ingestion_dedups_on_reingest(pg_session):
    inserted_first = record_ingestion(
        pg_session, source="google_reviews", kind="review", external_id="rev-1", rating=3
    )
    pg_session.commit()

    inserted_second = record_ingestion(
        pg_session, source="google_reviews", kind="review", external_id="rev-1", rating=5
    )
    pg_session.commit()

    assert inserted_first is True
    assert inserted_second is False

    events = pg_session.execute(
        select(Event).where(Event.event_type == EventType.ITEM_INGESTED)
    ).scalars().all()
    assert len(events) == 1  # the re-ingest must not fire a second ITEM_INGESTED

    mention = pg_session.execute(select(Mention)).scalar_one()
    assert mention.rating == 5  # the row itself still last-write-wins


def test_postgres_assign_mention_first_assignment_wins(pg_session):
    mention = Mention(
        source="google_reviews", kind="review", external_id="rev-1", sentiment=Sentiment.NEGATIVE
    )
    pg_session.add(mention)
    pg_session.commit()

    assign_mention(pg_session, mention.id, "Gian")
    pg_session.commit()
    row = pg_session.get(Mention, mention.id)
    first_assigned_at = row.assigned_at
    assert row.assigned_to == "Gian"
    assert first_assigned_at is not None

    assign_mention(pg_session, mention.id, "Paul")
    pg_session.commit()
    row = pg_session.get(Mention, mention.id)

    assert row.assigned_to == "Paul"
    assert row.assigned_at == first_assigned_at

    events = pg_session.execute(
        select(Event).where(Event.event_type == EventType.ITEM_ASSIGNED)
    ).scalars().all()
    assert len(events) == 2  # every assignment call logs an event regardless
