"""db.py — SQLAlchemy engine/session setup.

Kept deliberately thin: one function to build an engine from any
SQLAlchemy URL (so tests can point it at SQLite in-memory without
touching this module), and a session-factory helper for real usage
against the configured DATABASE_URL.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Build an engine for any SQLAlchemy URL. Production code should
    prefer get_engine() (below), which reads DATABASE_URL from Settings;
    this exists so tests can pass "sqlite+pysqlite:///:memory:" directly
    without needing a Settings/env-var dance."""
    connect_args = {}
    if database_url.startswith("sqlite"):
        # Required for a shared in-memory SQLite engine used across
        # multiple connections within the same test (see conftest.py) —
        # SQLite otherwise refuses cross-thread/cross-connection sharing.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, echo=echo, connect_args=connect_args)


_engine: Engine | None = None


def get_engine() -> Engine:
    """The process-wide engine, built once from Settings.database_url."""
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """A transactional session — commits on clean exit, rolls back and
    re-raises on any exception. Prefer this over constructing a Session
    directly so callers don't have to remember the commit/rollback
    boilerplate at every call site."""
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
