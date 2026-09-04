"""Tests for GET /api/overview and the /api/mentions family (Phase 7 API
layer). Checks response SHAPE field-for-field against docs/api-contract.md,
not just status codes - a shape mismatch here is exactly the kind of gap
that wouldn't otherwise surface until a UI consumer hits it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.auth import create_session_token, create_user
from app.models import Base, IngestionRun, Mention, RunStatus


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


# --- GET /api/overview ---


def test_overview_requires_auth(client):
    response = client.get("/api/overview")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_overview_response_shape_matches_contract(client, auth_headers, sqlite_session):
    now = _now()
    sqlite_session.add_all(
        [
            Mention(
                source="google_reviews", kind="review", external_id="rev-1",
                rating=5, has_reply=True, venue="Remedy — BGC (One Uptown Residence)",
                sentiment="Positive", published_at=now - timedelta(days=1),
            ),
            Mention(
                source="reddit", kind="mention", external_id="t3_a",
                sentiment="Negative", alert_category="crisis", published_at=now - timedelta(days=2),
            ),
        ]
    )
    run = IngestionRun(source="google_reviews", status=RunStatus.SUCCESS, finished_at=now)
    sqlite_session.add(run)
    sqlite_session.commit()

    response = client.get("/api/overview", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {
        "clarityIndex", "totalMentions", "netSentiment", "avgGoogleRating",
        "activeAlerts", "aiSummaryText", "lastSyncedAt",
    }
    assert set(body["clarityIndex"].keys()) == {"score", "deltaVsLastWeek"}
    assert isinstance(body["clarityIndex"]["score"], int)
    assert isinstance(body["clarityIndex"]["deltaVsLastWeek"], int)

    assert set(body["totalMentions"].keys()) == {"value", "deltaPct", "priorPeriodValue"}
    assert body["totalMentions"]["value"] == 2

    assert set(body["netSentiment"].keys()) == {"value", "deltaPts"}

    assert set(body["avgGoogleRating"].keys()) == {"value", "reviewCount"}
    assert body["avgGoogleRating"]["value"] == 5.0
    assert body["avgGoogleRating"]["reviewCount"] == 1

    assert set(body["activeAlerts"].keys()) == {"total", "crisis", "digest"}
    assert body["activeAlerts"]["total"] == 1
    assert body["activeAlerts"]["crisis"] == 1
    assert body["activeAlerts"]["digest"] == 0

    assert isinstance(body["aiSummaryText"], str) and body["aiSummaryText"]
    assert body["lastSyncedAt"] is not None
    # ISO-8601 UTC - must round-trip through fromisoformat.
    datetime.fromisoformat(body["lastSyncedAt"])


def test_overview_active_alerts_excludes_resolved(client, auth_headers, sqlite_session):
    now = _now()
    sqlite_session.add_all(
        [
            Mention(
                source="reddit", kind="mention", external_id="t3_open",
                alert_category="crisis", resolved_at=None, published_at=now,
            ),
            Mention(
                source="reddit", kind="mention", external_id="t3_resolved",
                alert_category="crisis", resolved_at=now, published_at=now,
            ),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/overview", headers=auth_headers).json()
    assert body["activeAlerts"]["total"] == 1
    assert body["activeAlerts"]["crisis"] == 1


def test_overview_custom_period_without_from_to_is_400(client, auth_headers):
    response = client.get("/api/overview", params={"period": "custom"}, headers=auth_headers)
    assert response.status_code == 400


@pytest.mark.parametrize("period", ["7d", "30d", "90d"])
def test_overview_accepts_all_documented_periods(client, auth_headers, period):
    response = client.get("/api/overview", params={"period": period}, headers=auth_headers)
    assert response.status_code == 200


def test_overview_no_data_yet_returns_zeroed_shape_not_error(client, auth_headers):
    response = client.get("/api/overview", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["totalMentions"]["value"] == 0
    assert body["lastSyncedAt"] is None


# --- GET /api/mentions ---


def test_mentions_requires_auth(client):
    response = client.get("/api/mentions")
    assert response.status_code == 401


def test_mentions_list_item_shape_matches_contract(client, auth_headers, sqlite_session):
    now = _now()
    mention = Mention(
        source="reddit", kind="mention", external_id="t3_shape",
        author="sk1n_a1b2c3d4", text="hello world", url="https://reddit.com/x",
        published_at=now, sentiment="Positive", topics=["pricing"],
        venue="r/PhilippinesSkincare", alert_category=None,
    )
    sqlite_session.add(mention)
    sqlite_session.commit()

    response = client.get("/api/mentions", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "nextCursor"}
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item.keys()) == {
        "id", "platform", "author", "text", "url", "publishedAt", "sentiment",
        "topics", "venue", "assignedTo", "assignedAt", "resolvedAt", "alertCategory",
    }
    assert item["id"] == mention.id
    assert item["platform"] == "reddit"
    assert item["author"] == "sk1n_a1b2c3d4"
    assert item["text"] == "hello world"
    assert item["url"] == "https://reddit.com/x"
    assert item["sentiment"] == "Positive"
    assert item["topics"] == ["pricing"]
    assert item["venue"] == "r/PhilippinesSkincare"
    assert item["assignedTo"] is None
    assert item["assignedAt"] is None
    assert item["resolvedAt"] is None
    assert item["alertCategory"] is None
    datetime.fromisoformat(item["publishedAt"])


def test_mentions_only_kind_mention_by_default(client, auth_headers, sqlite_session):
    """GET /api/mentions is the Mentions-tab feed - reviews/articles don't
    leak into it."""
    sqlite_session.add_all(
        [
            Mention(source="reddit", kind="mention", external_id="m1", published_at=_now()),
            Mention(source="google_reviews", kind="review", external_id="r1", published_at=_now()),
            Mention(source="news_gnews", kind="article", external_id="a1", published_at=_now()),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/mentions", headers=auth_headers).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["platform"] == "reddit"


def test_mentions_filter_by_keyword(client, auth_headers, sqlite_session):
    sqlite_session.add_all(
        [
            Mention(source="reddit", kind="mention", external_id="m1", text="great facial", published_at=_now()),
            Mention(source="reddit", kind="mention", external_id="m2", text="pricing question", published_at=_now()),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/mentions", params={"keyword": "facial"}, headers=auth_headers).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["text"] == "great facial"


def test_mentions_filter_by_platform_and_sentiment(client, auth_headers, sqlite_session):
    sqlite_session.add_all(
        [
            Mention(source="reddit", kind="mention", external_id="m1", sentiment="Negative", published_at=_now()),
            Mention(source="instagram", kind="mention", external_id="m2", sentiment="Positive", published_at=_now()),
        ]
    )
    sqlite_session.commit()

    body = client.get(
        "/api/mentions", params={"platform": "reddit", "sentiment": "Negative"}, headers=auth_headers
    ).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["platform"] == "reddit"


def test_mentions_pagination_cursor(client, auth_headers, sqlite_session):
    for i in range(3):
        sqlite_session.add(Mention(source="reddit", kind="mention", external_id=f"m{i}", published_at=_now()))
    sqlite_session.commit()

    first_page = client.get("/api/mentions", params={"limit": 2}, headers=auth_headers).json()
    assert len(first_page["items"]) == 2
    assert first_page["nextCursor"] is not None

    second_page = client.get(
        "/api/mentions", params={"limit": 2, "cursor": first_page["nextCursor"]}, headers=auth_headers
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["nextCursor"] is None

    seen_ids = {item["id"] for item in first_page["items"]} | {item["id"] for item in second_page["items"]}
    assert len(seen_ids) == 3


# --- POST /api/mentions/{id}/assign, /resolve ---


def test_assign_mention_success(client, auth_headers, sqlite_session):
    mention = Mention(source="reddit", kind="mention", external_id="m1", published_at=_now())
    sqlite_session.add(mention)
    sqlite_session.commit()

    response = client.post(f"/api/mentions/{mention.id}/assign", json={"assignee": "Gian"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["assignedTo"] == "Gian"
    assert body["assignedAt"] is not None


def test_assign_mention_unknown_id_is_404(client, auth_headers):
    response = client.post("/api/mentions/999999/assign", json={"assignee": "Gian"}, headers=auth_headers)
    assert response.status_code == 404


def test_resolve_mention_success(client, auth_headers, sqlite_session):
    mention = Mention(source="reddit", kind="mention", external_id="m1", published_at=_now())
    sqlite_session.add(mention)
    sqlite_session.commit()

    response = client.post(f"/api/mentions/{mention.id}/resolve", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["resolvedAt"] is not None


def test_resolve_mention_unknown_id_is_404(client, auth_headers):
    response = client.post("/api/mentions/999999/resolve", headers=auth_headers)
    assert response.status_code == 404


def test_assign_and_resolve_require_auth(client, sqlite_session):
    mention = Mention(source="reddit", kind="mention", external_id="m1", published_at=_now())
    sqlite_session.add(mention)
    sqlite_session.commit()

    assert client.post(f"/api/mentions/{mention.id}/assign", json={"assignee": "Gian"}).status_code == 401
    assert client.post(f"/api/mentions/{mention.id}/resolve").status_code == 401
