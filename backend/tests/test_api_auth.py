"""Tests for POST /api/auth/login (Phase 7 API layer). Uses FastAPI's
TestClient against the real app (app.api.main.app), with the app's DB
dependency overridden to the sqlite_session fixture already established
in conftest.py - see this module's `client` fixture."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.auth import create_user, verify_session_token
from app.models import Base


@pytest.fixture
def sqlite_session():
    """Shadows conftest.py's sqlite_session fixture for this module only -
    same in-memory-SQLite + create_all pattern, plus poolclass=StaticPool.
    FastAPI's TestClient dispatches sync dependencies/route handlers
    through anyio's threadpool, so a request runs on a different OS
    thread than this fixture's own setup. SQLAlchemy's default pool for a
    ":memory:" URL (SingletonThreadPool) hands each distinct thread a
    SEPARATE, independently-empty in-memory database; StaticPool instead
    funnels every checkout - from any thread - through the one underlying
    connection, which is what actually makes a shared in-memory SQLite
    engine usable across threads (see SQLAlchemy's docs, "Using a Memory
    Database in Multiple Threads"). conftest.py's own fixture isn't built
    this way since none of its other consumers go through a threadpool."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(sqlite_session):
    def _override_get_db():
        # Mirrors app.api.deps.get_db()'s own commit/rollback semantics,
        # minus the final close() - the sqlite_session fixture's own
        # teardown owns that.
        try:
            yield sqlite_session
            sqlite_session.commit()
        except Exception:
            sqlite_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _make_user(sqlite_session, email="gian@remedy.example", password="hunter2"):
    user = create_user(sqlite_session, email=email, password=password, display_name="Gian")
    sqlite_session.commit()
    return user


def test_login_success_returns_token_expiry_and_user(client, sqlite_session):
    user = _make_user(sqlite_session)

    response = client.post("/api/auth/login", json={"email": "gian@remedy.example", "password": "hunter2"})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"token", "expires_at", "user"}
    assert isinstance(body["token"], str) and body["token"]
    assert isinstance(body["expires_at"], str)
    assert body["user"] == {"id": user.id, "email": "gian@remedy.example", "display_name": "Gian"}

    # The returned token must actually verify to this user's id.
    assert verify_session_token(body["token"]) == user.id


def test_login_unknown_email_returns_401_invalid_credentials(client):
    response = client.post("/api/auth/login", json={"email": "nobody@remedy.example", "password": "whatever"})
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


def test_login_wrong_password_returns_401_invalid_credentials(client, sqlite_session):
    _make_user(sqlite_session)
    response = client.post("/api/auth/login", json={"email": "gian@remedy.example", "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json() == {"error": "invalid credentials"}


def test_login_unknown_email_and_wrong_password_return_identical_bodies(client, sqlite_session):
    """The contract's own requirement: never let the response distinguish
    unknown-email from wrong-password."""
    _make_user(sqlite_session)
    unknown = client.post("/api/auth/login", json={"email": "nobody@remedy.example", "password": "x"})
    wrong_pw = client.post("/api/auth/login", json={"email": "gian@remedy.example", "password": "wrong"})
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json() == wrong_pw.json() == {"error": "invalid credentials"}


def test_protected_endpoint_without_token_returns_401_unauthorized(client):
    response = client.get("/api/overview")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_protected_endpoint_with_garbage_token_returns_401_unauthorized(client):
    response = client.get("/api/overview", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_protected_endpoint_with_wrong_scheme_returns_401_unauthorized(client, sqlite_session):
    user = _make_user(sqlite_session)
    from app.auth import create_session_token

    token = create_session_token(user.id)
    response = client.get("/api/overview", headers={"Authorization": f"Token {token}"})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_health_needs_no_auth(client):
    # Deploy runbook's liveness probe (docs/runbook-deploy-free-tier.md) - no
    # Authorization header, and not under /api like every real resource.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
