"""Tests for GET /api/reviews, POST /api/reviews/{id}/reply, GET
/api/topics, GET /api/topics/{key}/mentions (Phase 7 API layer)."""

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
from app.models import Base, Mention


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


# --- GET /api/reviews ---


def test_reviews_requires_auth(client):
    assert client.get("/api/reviews").status_code == 401


def test_reviews_per_row_has_reply_not_whole_branch_bug(client, auth_headers, sqlite_session):
    """The contract's own explicit callout: one replied review must not
    make the whole branch look fully answered."""
    venue = "Remedy — BGC (One Uptown Residence)"
    sqlite_session.add_all(
        [
            Mention(source="google_reviews", kind="review", external_id="r1", venue=venue, rating=5, has_reply=True),
            Mention(source="google_reviews", kind="review", external_id="r2", venue=venue, rating=2, has_reply=False),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/reviews", headers=auth_headers).json()
    listing = next(item for item in body["listings"] if item["venue"] == venue)
    assert listing["reviewCount"] == 2
    assert listing["pendingReplies"] == 1
    assert listing["responseRatePct"] == 50


def test_reviews_listing_shape(client, auth_headers, sqlite_session):
    venue = "Remedy — BGC (One Uptown Residence)"
    sqlite_session.add(
        Mention(source="google_reviews", kind="review", external_id="r1", venue=venue, rating=5, has_reply=True)
    )
    sqlite_session.commit()

    body = client.get("/api/reviews", headers=auth_headers).json()
    assert set(body.keys()) == {"listings"}
    item = next(row for row in body["listings"] if row["venue"] == venue)
    assert set(item.keys()) == {"venue", "rating", "reviewCount", "pendingReplies", "responseRatePct", "status"}
    assert item["status"] == "ok"


def test_reviews_reply_marks_has_reply_and_returns_updated_listing(client, auth_headers, sqlite_session):
    venue = "Remedy — BGC (One Uptown Residence)"
    mention = Mention(source="google_reviews", kind="review", external_id="r1", venue=venue, rating=2, has_reply=False)
    sqlite_session.add(mention)
    sqlite_session.commit()

    response = client.post(f"/api/reviews/{mention.id}/reply", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["venue"] == venue
    assert body["pendingReplies"] == 0
    assert body["responseRatePct"] == 100

    sqlite_session.expire_all()
    refreshed = sqlite_session.get(Mention, mention.id)
    assert refreshed.has_reply is True


def test_reviews_reply_unknown_id_is_404(client, auth_headers):
    assert client.post("/api/reviews/999999/reply", headers=auth_headers).status_code == 404


def test_reviews_reply_on_non_review_mention_is_404(client, auth_headers, sqlite_session):
    mention = Mention(source="reddit", kind="mention", external_id="m1")
    sqlite_session.add(mention)
    sqlite_session.commit()

    assert client.post(f"/api/reviews/{mention.id}/reply", headers=auth_headers).status_code == 404


# --- GET /api/topics ---


def test_topics_requires_auth(client):
    assert client.get("/api/topics").status_code == 401


def test_topics_list_shape_and_fixed_taxonomy(client, auth_headers, sqlite_session):
    sqlite_session.add(
        Mention(
            source="google_reviews", kind="review", external_id="r1",
            text="Left with an actual glow.", sentiment="Positive",
            topics=["facial-results"], published_at=_now(),
        )
    )
    sqlite_session.commit()

    body = client.get("/api/topics", headers=auth_headers).json()
    assert set(body.keys()) == {"topics"}
    keys = {t["key"] for t in body["topics"]}
    assert keys == {"facial-results", "staff-service", "rejuran", "pricing", "booking"}

    facial = next(t for t in body["topics"] if t["key"] == "facial-results")
    assert set(facial.keys()) == {"key", "label", "mentionCountThisWeek", "sentimentSplit", "sampleQuote", "tag"}
    assert set(facial["sentimentSplit"].keys()) == {"positivePct", "neutralPct", "negativePct"}
    assert facial["mentionCountThisWeek"] == 1
    assert facial["sampleQuote"] == "Left with an actual glow."


def test_topics_drill_down_filters_by_topic(client, auth_headers, sqlite_session):
    sqlite_session.add_all(
        [
            Mention(source="reddit", kind="mention", external_id="m1", topics=["pricing"], published_at=_now()),
            Mention(source="reddit", kind="mention", external_id="m2", topics=["booking"], published_at=_now()),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/topics/pricing/mentions", headers=auth_headers).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["topics"] == ["pricing"]


def test_topics_drill_down_unknown_key_is_404(client, auth_headers):
    assert client.get("/api/topics/not-a-real-topic/mentions", headers=auth_headers).status_code == 404
