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
from datetime import datetime, timezone

import pytest
import sqlalchemy
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import make_engine
from app.models import Base, IngestionRun, Mention
from app.repository import get_source_freshness, start_run, upsert_mention
from app.scheduler import run_due_jobs

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


def test_postgres_reply_by_venue_picks_oldest_pending_review(pg_session):
    """Checklist 7.4/8.5's `POST /api/reviews/by-venue/{venue}/reply` uses
    `.order_by(Mention.published_at.asc().nulls_last(), ...)` -
    `NULLS LAST` is standard SQL but SQLite's `ORDER BY` doesn't support it
    natively (SQLAlchemy emulates it there with a CASE expression), so the
    TestClient/SQLite suite in test_api_reviews_topics.py proves the
    *behavior* but not that the real Postgres dialect accepts this exact
    query. Calls the real route function directly (not through FastAPI's
    DI) rather than re-implementing the query here a second time."""
    from app.api.routes.reviews import reply_to_next_pending_review

    venue = "Remedy — BGC (One Uptown Residence)"
    # No published_at at all - exercises the `nulls_last()` half specifically.
    no_date = Mention(source="google_reviews", kind="review", external_id="r-no-date", venue=venue, has_reply=False)
    dated = Mention(
        source="google_reviews", kind="review", external_id="r-dated", venue=venue, has_reply=False,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    pg_session.add_all([no_date, dated])
    pg_session.commit()

    body = reply_to_next_pending_review(venue, db=pg_session, user=None)
    assert body["venue"] == venue
    assert body["pendingReplies"] == 1

    pg_session.expire_all()
    # The dated row sorts before NULL under nulls_last(), so it's the one
    # marked replied first.
    assert pg_session.get(Mention, dated.id).has_reply is True
    assert pg_session.get(Mention, no_date.id).has_reply is False


def test_postgres_scheduler_survives_a_genuinely_aborted_transaction(pg_session, capsys):
    """app.scheduler.run_due_jobs()'s fallback (commit the failed job's
    ERROR-marked IngestionRun row; if THAT commit itself raises, roll back
    instead) only has something real to prove against Postgres - SQLite
    has no equivalent "transaction is unusable until rolled back" failure
    mode, so this can't be exercised on the sqlite_session-backed
    test_scheduler.py suite. Forces a genuine Postgres-level abort (a
    query against a table that doesn't exist) INSIDE a job, then asserts
    a second, unrelated job in the same pass still runs cleanly
    afterward - proving the rollback fallback actually leaves the session
    usable, not just that the code path is reachable."""
    from types import SimpleNamespace

    calls = []

    def _broken_transaction_job(session):
        calls.append("source_a")
        with start_run(session, source="source_a"):
            session.execute(sqlalchemy.text("SELECT * FROM this_table_does_not_exist_at_all"))

    def _healthy_job(session):
        calls.append("source_b")
        with start_run(session, source="source_b") as run:
            run.items_seen = 1
            run.items_ingested = 1

    jobs = [
        SimpleNamespace(SOURCE_NAME="source_a", run=_broken_transaction_job),
        SimpleNamespace(SOURCE_NAME="source_b", run=_healthy_job),
    ]

    ran = run_due_jobs(pg_session, jobs=jobs)

    assert calls == ["source_a", "source_b"]
    assert ran == ["source_b"]
    assert "source_a failed this pass" in capsys.readouterr().out

    # The commit-the-ERROR-row fallback itself failed here (the aborted
    # Postgres transaction rejects even a COMMIT), so source_a's ledger
    # entry was rolled back along with it - genuinely absent, not just
    # unchecked. What matters is that source_b's run committed cleanly on
    # the same session right after.
    rows = pg_session.execute(select(IngestionRun)).scalars().all()
    sources = {row.source for row in rows}
    assert "source_a" not in sources
    assert "source_b" in sources


def test_postgres_classification_job_classifies_oldest_ingested_first(pg_session, monkeypatch):
    """app/jobs/classification_job.py (checklist 0.23) is new code with no
    prior real-Postgres coverage - its query (classify_unclassified_batch(),
    WHERE classified_at IS NULL AND text IS NOT NULL, ORDER BY ingested_at
    ASC) uses no Postgres-specific syntax, unlike the NULLS LAST ordering
    the by-venue reply endpoint needed real-Postgres proof for, but this
    project's own established rigor (see this file's every other test)
    doesn't take "it's simple SQL" as a substitute for actually running it
    against the one dialect this project ships against."""
    import app.classification as classification
    from app.jobs import classification_job

    monkeypatch.setattr(
        classification, "_call_model",
        lambda text: '{"sentiment": "Positive", "confidence": 0.8, "alert_category": "digest", "reasoning": "ok"}',
    )

    older = Mention(source="google_reviews", kind="review", external_id="rev-older", text="First in")
    pg_session.add(older)
    pg_session.commit()
    newer = Mention(source="google_reviews", kind="review", external_id="rev-newer", text="Second in")
    pg_session.add(newer)
    pg_session.commit()

    monkeypatch.setattr(classification_job, "BATCH_SIZE", 1)
    classification_job.run(pg_session)
    pg_session.commit()

    pg_session.expire_all()
    assert pg_session.get(Mention, older.id).classified_at is not None
    assert pg_session.get(Mention, newer.id).classified_at is None

    from app.models import RunStatus

    run_row = pg_session.execute(
        select(IngestionRun).where(IngestionRun.source == classification_job.SOURCE_NAME)
    ).scalar_one()
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 1
