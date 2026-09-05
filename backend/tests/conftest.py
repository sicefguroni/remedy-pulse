import os
import sys

# Allow `import fetch_owned_reviews`, `import fetch_competitor_ratings`,
# `import http_utils`, etc. regardless of the directory pytest is invoked
# from (CI runs `pytest backend/tests/` from the repo root).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture
def sqlite_session():
    """A throwaway in-memory SQLite database with the full app schema, for
    fast repository/model tests. Real usage targets Postgres exclusively
    (see docs/decisions/05-persistence-choice.md); this exists purely so CI
    doesn't need a live Postgres to exercise app/repository.py's logic —
    the ON CONFLICT upsert path is also tested directly against a real
    Postgres container in test_app_repository_postgres.py, skipped
    automatically when one isn't reachable."""
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
