"""Tests for GET /api/competitors, GET /api/emv, GET /api/roster (Phase 7
API layer)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.auth import create_session_token, create_user
from app.models import Base, Mention, User


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


# --- GET /api/competitors ---


def test_competitors_requires_auth(client):
    assert client.get("/api/competitors").status_code == 401


def test_competitors_shape_and_remedy_is_own(client, auth_headers, sqlite_session):
    sqlite_session.add_all(
        [
            Mention(source="google_reviews", kind="review", external_id="r1", sentiment="Positive"),
            Mention(
                source="google_places_competitor", kind="review", external_id="c1",
                venue="Belo Medical Group", sentiment="Negative",
            ),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/competitors", headers=auth_headers).json()
    assert set(body.keys()) == {"shareOfVoice", "sourceBreakdown", "competitorSentiment"}

    remedy_sov = next(row for row in body["shareOfVoice"] if row["name"] == "Remedy")
    assert remedy_sov["isOwn"] is True
    assert set(remedy_sov.keys()) == {"name", "pct", "isOwn"}

    belo_sov = next(row for row in body["shareOfVoice"] if row["name"] == "Belo Medical Group")
    assert belo_sov["isOwn"] is False

    remedy_sentiment = next(row for row in body["competitorSentiment"] if row["name"] == "Remedy")
    assert set(remedy_sentiment.keys()) == {"name", "isOwn", "positivePct", "neutralPct", "negativePct"}
    assert remedy_sentiment["positivePct"] == 100

    assert all(set(row.keys()) == {"platform", "pct"} for row in body["sourceBreakdown"])


# --- GET /api/emv ---


def test_emv_requires_auth(client):
    assert client.get("/api/emv").status_code == 401


def test_emv_articles_and_totals_are_all_null(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(
            source="news_gnews", kind="article", external_id="a1",
            venue="Rappler", headline="Remedy BGC clinic review", tier="National News",
            sentiment="Positive", url="https://rappler.com/x", published_at=_now(),
        )
    )
    sqlite_session.commit()

    body = client.get("/api/emv", headers=auth_headers).json()
    assert set(body.keys()) == {"grossTotal", "netTotal", "filtered", "articles"}
    assert body["grossTotal"] is None
    assert body["netTotal"] is None
    assert body["filtered"] is False

    article = body["articles"][0]
    assert set(article.keys()) == {
        "id", "outlet", "headline", "tier", "sentiment", "grossEmv", "netEmv", "url", "publishedAt",
    }
    assert article["outlet"] == "Rappler"
    assert article["grossEmv"] is None
    assert article["netEmv"] is None


def test_emv_filtered_true_when_outlet_given(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(source="news_gnews", kind="article", external_id="a1", venue="Rappler", published_at=_now())
    )
    sqlite_session.commit()

    body = client.get("/api/emv", params={"outlet": "Rappler"}, headers=auth_headers).json()
    assert body["filtered"] is True
    assert all(a["outlet"] == "Rappler" for a in body["articles"])


# --- GET /api/roster ---


def test_roster_requires_auth(client):
    assert client.get("/api/roster").status_code == 401


def test_roster_lists_only_active_users(client, auth_headers, sqlite_session):
    active = User(email="paul@remedy.example", password_hash="x", display_name="Paul", is_active=True)
    inactive = User(email="boom@remedy.example", password_hash="x", display_name="Boom", is_active=False)
    sqlite_session.add_all([active, inactive])
    sqlite_session.commit()

    body = client.get("/api/roster", headers=auth_headers).json()
    assert set(body.keys()) == {"assignees"}
    names = {row["displayName"] for row in body["assignees"]}
    assert "Paul" in names
    assert "Boom" not in names
    row = next(r for r in body["assignees"] if r["displayName"] == "Paul")
    assert set(row.keys()) == {"id", "email", "displayName"}
