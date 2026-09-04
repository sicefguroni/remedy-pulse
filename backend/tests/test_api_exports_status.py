"""Tests for GET /api/status and POST /api/exports/{type} (Phase 7 API
layer)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.auth import create_session_token, create_user
from app.jobs import JOBS
from app.models import Base, Event, EventType, IngestionRun, Mention, RunStatus


@pytest.fixture
def sqlite_session():
    """Shadows conftest.py's sqlite_session fixture for this module only -
    see test_api_auth.py's identical fixture for why (StaticPool is
    needed so the in-memory SQLite DB survives FastAPI TestClient's
    threadpool dispatch)."""
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


@pytest.fixture
def auth_headers(sqlite_session):
    user = create_user(sqlite_session, email="gian@remedy.example", password="hunter2", display_name="Gian")
    sqlite_session.commit()
    token = create_session_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def _now():
    return datetime.now(timezone.utc)


# --- GET /api/status ---


def test_status_requires_auth(client):
    assert client.get("/api/status").status_code == 401


def test_status_one_entry_per_registered_job(client, auth_headers, sqlite_session):
    sqlite_session.add(
        IngestionRun(source="google_reviews", status=RunStatus.SUCCESS, finished_at=_now())
    )
    sqlite_session.add(
        IngestionRun(source="google_places_competitor", status=RunStatus.ACCESS_DENIED, error="403 Forbidden")
    )
    sqlite_session.commit()

    body = client.get("/api/status", headers=auth_headers).json()
    assert set(body.keys()) == {"sources"}
    assert len(body["sources"]) == len(JOBS)
    assert {row["source"] for row in body["sources"]} == {job.SOURCE_NAME for job in JOBS}

    for row in body["sources"]:
        assert set(row.keys()) == {"source", "lastAttemptAt", "lastSuccessAt", "lastStatus", "lastError"}

    google = next(row for row in body["sources"] if row["source"] == "google_reviews")
    assert google["lastStatus"] == "success"
    assert google["lastSuccessAt"] is not None

    places = next(row for row in body["sources"] if row["source"] == "google_places_competitor")
    assert places["lastStatus"] == "access_denied"
    assert places["lastError"] == "403 Forbidden"

    never_run = next(row for row in body["sources"] if row["source"] == "reddit")
    assert never_run["lastAttemptAt"] is None
    assert never_run["lastStatus"] is None


# --- POST /api/exports/{type} ---


def test_exports_require_auth(client):
    assert client.post("/api/exports/mentions_csv").status_code == 401


def test_exports_unknown_type_is_422(client, auth_headers):
    response = client.post("/api/exports/not_a_real_export", headers=auth_headers)
    assert response.status_code == 422


def test_exports_mentions_csv_returns_real_csv_and_logs_export(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(
            source="reddit", kind="mention", external_id="m1", text="hello",
            author="u/test", sentiment="Positive", published_at=_now(),
        )
    )
    sqlite_session.commit()

    response = client.post("/api/exports/mentions_csv", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["text"] == "hello"
    assert rows[0]["sentiment"] == "Positive"

    export_events = sqlite_session.execute(
        select(Event).where(Event.event_type == EventType.EXPORT_DOWNLOADED)
    ).scalars().all()
    assert len(export_events) == 1
    assert export_events[0].metadata_json["export_type"] == "mentions_csv"
    assert export_events[0].metadata_json["item_count"] == 1


def test_exports_reviews_csv(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(
            source="google_reviews", kind="review", external_id="r1",
            venue="Remedy — BGC (One Uptown Residence)", rating=5, has_reply=True,
        )
    )
    sqlite_session.commit()

    response = client.post("/api/exports/reviews_csv", headers=auth_headers)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert any(row["venue"] == "Remedy — BGC (One Uptown Residence)" for row in rows)


def test_exports_emv_csv_has_blank_emv_columns(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(source="news_gnews", kind="article", external_id="a1", venue="Rappler", published_at=_now())
    )
    sqlite_session.commit()

    response = client.post("/api/exports/emv_csv", headers=auth_headers)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["outlet"] == "Rappler"
    assert rows[0]["grossEmv"] == ""
    assert rows[0]["netEmv"] == ""
