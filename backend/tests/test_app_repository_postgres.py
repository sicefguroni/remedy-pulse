"""Integration test for app/repository.py's ON CONFLICT upsert path against
a REAL Postgres database — the SQLite-backed tests in
test_app_repository.py cover the same logic through a different SQL
dialect (see app.repository._upsert_insert), and Postgres is the only
dialect this project actually ships against (see
docs/decisions/05-persistence-choice.md). This file is what actually proves
the Postgres ON CONFLICT DO UPDATE statement works, not just that an
analogous SQLite statement does.

Skips automatically (not a failure) if no Postgres is reachable — CI
doesn't run one; a local `docker compose up -d` in backend/ does. Uses a
dedicated `remedy_pulse_test` database on the same server so it never
touches whatever a developer is poking at by hand in the default
`remedy_pulse` database, and drops/recreates its own tables around every
test so runs never see another run's leftover rows.
"""

import os

import pytest
import sqlalchemy
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import make_engine
from app.models import Base, Mention
from app.repository import get_source_freshness, start_run, upsert_mention

# Reuses the same server as DATABASE_URL / docker-compose.yml, pointed at
# a separate database so this suite's DDL never touches dev data.
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
    # CREATE DATABASE can't run inside a transaction block in Postgres -
    # needs a genuinely autocommitting connection, not just a commit()
    # call on a transactional one.
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


def test_postgres_upsert_on_conflict_is_idempotent(pg_session):
    upsert_mention(pg_session, source="google_reviews", kind="review", external_id="rev-1", rating=3)
    pg_session.commit()
    upsert_mention(pg_session, source="google_reviews", kind="review", external_id="rev-1", rating=3)
    pg_session.commit()

    rows = pg_session.execute(select(Mention)).scalars().all()
    assert len(rows) == 1


def test_postgres_upsert_on_conflict_updates_in_place(pg_session):
    upsert_mention(pg_session, source="google_reviews", kind="review", external_id="rev-1",
                   rating=3, text="original")
    pg_session.commit()
    upsert_mention(pg_session, source="google_reviews", kind="review", external_id="rev-1",
                   rating=5, text="revised")
    pg_session.commit()

    row = pg_session.execute(select(Mention)).scalar_one()
    assert row.rating == 5
    assert row.text == "revised"
    # updated_at must advance on a re-upsert (server-side `func.now()`,
    # not something the Python code has to remember to set itself).
    assert row.updated_at >= row.ingested_at


def test_postgres_ledger_roundtrip(pg_session):
    with start_run(pg_session, source="google_reviews") as run:
        run.items_seen = 2
        run.items_ingested = 2
    pg_session.commit()

    fresh = get_source_freshness(pg_session, "google_reviews")
    assert fresh.last_status == "success"
    assert fresh.last_success_at is not None
