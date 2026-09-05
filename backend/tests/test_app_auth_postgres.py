"""Integration test for app/auth.py's create_user()/authenticate() round
trip against a REAL Postgres database — the SQLite-backed tests in
test_app_auth.py cover the same logic through a different SQL dialect.
Follows the exact same skip-cleanly-when-unreachable pattern as
test_app_repository_postgres.py / test_app_events_postgres.py.

Skips automatically (not a failure) if no Postgres is reachable — CI
doesn't run one; a local `docker compose up -d` in backend/ does. Reuses
the same `remedy_pulse_test` database as the other *_postgres.py suites,
dropping/recreating tables around every test so runs never see another
run's leftover rows.
"""

import os

import pytest
import sqlalchemy
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth import DuplicateEmailError, authenticate, create_user, verify_password
from app.db import make_engine
from app.models import Base, Event, EventType, User

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


def test_postgres_create_user_and_authenticate_roundtrip(pg_session):
    created = create_user(
        pg_session, email="gian@remedy.example", password="hunter2", display_name="Gian"
    )
    pg_session.commit()

    row = pg_session.execute(select(User)).scalar_one()
    assert row.id == created.id
    assert row.password_hash != "hunter2"
    assert verify_password("hunter2", row.password_hash) is True

    authenticated = authenticate(pg_session, email="gian@remedy.example", password="hunter2")
    pg_session.commit()

    assert authenticated is not None
    assert authenticated.id == created.id

    row = pg_session.get(User, created.id)
    assert row.last_login_at is not None

    events = pg_session.execute(select(Event).where(Event.event_type == EventType.LOGIN)).scalars().all()
    assert len(events) == 1
    assert events[0].actor == "gian@remedy.example"


def test_postgres_authenticate_fails_with_wrong_password(pg_session):
    create_user(pg_session, email="paul@remedy.example", password="hunter2", display_name="Paul")
    pg_session.commit()

    result = authenticate(pg_session, email="paul@remedy.example", password="wrong-password")
    assert result is None


def test_postgres_create_user_enforces_unique_email(pg_session):
    create_user(pg_session, email="boom@remedy.example", password="pw1", display_name="Boom")
    pg_session.commit()

    with pytest.raises(DuplicateEmailError):
        create_user(pg_session, email="boom@remedy.example", password="pw2", display_name="Boom Again")
