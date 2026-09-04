"""app/api/deps.py — shared FastAPI dependencies for the Phase 7 API layer.

Two things live here:

1. ApiError - a plain Exception carrying (status_code, JSON payload),
   registered with a FastAPI exception handler in app.api.main. Route
   handlers/dependencies raise this instead of fastapi.HTTPException so
   the response body is exactly the contract's `{"error": "..."}` shape -
   HTTPException's default envelope is `{"detail": ...}`, which
   docs/api-contract.md never uses.

2. get_db() / get_current_user() - the dependencies almost every route
   needs. get_db() yields a SQLAlchemy Session built from
   app.db.get_session_factory() (which itself reads
   app.config.get_settings() for DATABASE_URL - this module does not
   invent a second config/session system). Tests override it via
   app.dependency_overrides[get_db] to inject the sqlite_session fixture
   (see backend/tests/conftest.py) instead of a real engine.

   get_current_user() is the auth dependency every route except
   POST /api/auth/login depends on: it extracts `Authorization: Bearer
   <token>`, calls app.auth.verify_session_token() (returns a bare
   user_id or None, never raises - see that function's own docstring),
   and either resolves + returns the User row or raises
   ApiError(401, {"error": "unauthorized"}) per the contract's own
   wording for a missing/invalid/expired token.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.auth import verify_session_token
from app.db import get_session_factory
from app.models import User


class ApiError(Exception):
    """status_code + a JSON-serializable payload. See module docstring for
    why routes raise this instead of fastapi.HTTPException."""

    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"{status_code}: {payload}")


def get_db() -> Iterator[Session]:
    """Per-request Session. Mirrors app.db.session_scope()'s commit-on-
    clean-exit / rollback-and-reraise-on-exception / always-close
    semantics, but written as a generator FastAPI can manage across one
    request's lifetime (FastAPI throws any handler exception into this
    generator at the `yield` point, exactly like a context manager's
    __exit__ - see FastAPI's "dependencies with yield" docs) instead of a
    context manager wrapping a whole request handler body."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the Authorization: Bearer <token> header to a User row, or
    raises ApiError(401, {"error": "unauthorized"}) for anything else -
    missing header, wrong scheme, malformed/tampered/expired token
    (verify_session_token() returns None for all of those, never raises),
    an unknown user id, or a deactivated account (User.is_active=False)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, {"error": "unauthorized"})
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise ApiError(401, {"error": "unauthorized"})
    user_id = verify_session_token(token)
    if user_id is None:
        raise ApiError(401, {"error": "unauthorized"})
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError(401, {"error": "unauthorized"})
    return user
