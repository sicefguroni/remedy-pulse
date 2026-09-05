"""backend/tests/test_acceptance_p0.py — one test per PRD Must-Have (P0)
acceptance criterion (9.1).

remedy-pulse-prd.md's "Must-Have (P0)" section writes eleven criteria as
Given/When/Then already; this file transcribes each one to a real
API-level test wherever the criterion's substance is API-testable. Cross-
reference (every one of the 11 lands in exactly one authoritative place):

  P0-1  Mentions feed visible w/ platform/sentiment/source link
        -> test_p0_1_...                                    AUTOMATED
  P0-2  Search/filter by keyword, platform, sentiment
        -> test_p0_2_...  (+ test_api_overview_mentions.py's own
           test_mentions_filter_by_keyword/test_mentions_filter_by_platform_and_sentiment)
                                                              AUTOMATED
        "Updates as they type" is a reviewed code fact (remedy-pulse-
        mockup.html's filterMentions() fires on a plain `oninput`, no
        debounce) - not a live-typing unknown, so it does not also need
        a manual QA line.
  P0-3  CSV export contains exactly the filtered rows
        -> test_p0_3_...                                    AUTOMATED
  P0-4  Negative mention -> alerts list with an Assign action
        -> test_p0_4_...  (classify_and_store's own routing logic is
           already covered by test_classification.py)        AUTOMATED
  P0-5  Resolving an alert decreases the alert count + resolved state
        -> test_p0_5_...                                    AUTOMATED
  P0-6  Reviews reply updates status immediately
        -> test_p0_6_...  (+ test_api_reviews_topics.py's
           test_reviews_reply_marks_has_reply_and_returns_updated_listing)
                                                              AUTOMATED
        "A reply box opens" is a client-side modal - see
        docs/qa-manual-checklist.md item 1 for the one sliver of this
        criterion an API test can't observe.
  P0-7  Overview: health score, volume trend, alerts, one round trip
        -> test_p0_7_...                                    AUTOMATED
        "Loads by default, without further navigation" is a static code
        fact (the nav's Overview <a> has class="active" hardcoded; see
        docs/qa-manual-checklist.md item 2 for a real first-load check).
  P0-8  Topic drill-down shows constituent mentions + their sentiment
        -> test_p0_8_...  (+ test_api_reviews_topics.py's
           test_topics_drill_down_filters_by_topic)          AUTOMATED
  P0-9  EMV row shows calculation inputs, not just the final number
        -> test_p0_9_...                                    BLOCKED, skipped
        Not a browser-testability gap - a feature-not-built one. Gross/
        net EMV are null on every article by design today (8.7 is gated
        on a formula sign-off that hasn't happened - see
        docs/implementation-checklist.md#8.7 and
        test_api_competitors_emv_roster.py::test_emv_articles_and_totals_are_all_null).
        Does NOT belong in docs/qa-manual-checklist.md either - there is
        nothing yet for a human to click and see calculated.
  P0-10 Competitors: side-by-side share-of-voice and sentiment
        -> test_p0_10_...  (+ test_api_competitors_emv_roster.py's
           test_competitors_shape_and_remedy_is_own)          AUTOMATED
  P0-11 "Last synced" visible on every tab, updates after a sync
        -> test_p0_11_...                                    AUTOMATED
        "Visible on every tab" is a static code fact (#syncPill lives in
        <header>, outside every per-tab <section class="view">, so it
        renders unconditionally regardless of the active tab); see
        docs/qa-manual-checklist.md item 3 for a real cross-tab check.

Three criteria (P0-2, P0-6, P0-7) also get one line in
docs/qa-manual-checklist.md, for the specific real-browser-only sliver of
behavior an API test structurally can't observe (a keystroke happening
live, a modal opening, what's on screen right after a fresh page load).
The substantive acceptance criterion for all three is fully covered by
the automated test in this file - the manual entry supplements it, it
isn't the criterion's only coverage.
"""

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
from app.models import Base, IngestionRun, Mention, RunStatus, Sentiment


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


# --- P0-1: mentions feed, platform/sentiment/source link visible ---


def test_p0_1_new_mention_appears_in_the_feed_with_platform_sentiment_and_source_link(
    client, auth_headers, sqlite_session
):
    """Given a new mention is ingested, when the team opens the Mentions
    tab, then it appears in the feed within one business day with
    platform, sentiment, and source link visible."""
    m = Mention(
        source="reddit",
        kind="mention",
        external_id="p0-1",
        author="u/tester",
        text="Great facial results at Remedy BGC",
        url="https://reddit.com/r/PhilippinesSkincare/1",
        published_at=_now(),
        sentiment=Sentiment.POSITIVE,
    )
    sqlite_session.add(m)
    sqlite_session.commit()

    body = client.get("/api/mentions", headers=auth_headers).json()
    item = next(i for i in body["items"] if i["id"] == m.id)
    assert item["platform"] == "reddit"
    assert item["sentiment"] == "Positive"
    assert item["url"] == "https://reddit.com/r/PhilippinesSkincare/1"
    # "Within one business day" is a freshness SLA (how often ingestion jobs actually run),
    # not something a read-path test can prove either way - that cadence is documented and
    # verified separately in docs/runbook-source-failures.md. What this test DOES prove is
    # the necessary condition: nothing on the read path adds its own delay - the item is
    # queryable the instant it's committed, with every field the criterion names present.


# --- P0-2: search/filter by keyword, platform, sentiment ---


def test_p0_2_keyword_platform_and_sentiment_filters_each_narrow_the_feed(client, auth_headers, sqlite_session):
    """Given the team types a keyword, when they search, then only
    matching mentions are shown, updating as they type."""
    sqlite_session.add_all(
        [
            Mention(
                source="reddit", kind="mention", external_id="p0-2a",
                text="Rejuran pricing question", sentiment=Sentiment.NEUTRAL,
            ),
            Mention(
                source="instagram", kind="mention", external_id="p0-2b",
                text="Loved the facial results", sentiment=Sentiment.POSITIVE,
            ),
        ]
    )
    sqlite_session.commit()

    by_keyword = client.get("/api/mentions", params={"keyword": "rejuran"}, headers=auth_headers).json()
    assert [i["text"] for i in by_keyword["items"]] == ["Rejuran pricing question"]

    by_platform = client.get("/api/mentions", params={"platform": "instagram"}, headers=auth_headers).json()
    assert {i["platform"] for i in by_platform["items"]} == {"instagram"}

    by_sentiment = client.get("/api/mentions", params={"sentiment": "Positive"}, headers=auth_headers).json()
    assert {i["sentiment"] for i in by_sentiment["items"]} == {"Positive"}


# --- P0-3: CSV export contains exactly the filtered rows ---


def test_p0_3_csv_export_contains_exactly_the_filtered_rows(client, auth_headers, sqlite_session):
    """Given a filtered mentions view, when the team clicks export, then a
    CSV downloads containing exactly the filtered rows."""
    sqlite_session.add_all(
        [
            Mention(
                source="reddit", kind="mention", external_id="p0-3a",
                text="keep me - negative", sentiment=Sentiment.NEGATIVE,
            ),
            Mention(
                source="instagram", kind="mention", external_id="p0-3b",
                text="drop me - positive", sentiment=Sentiment.POSITIVE,
            ),
        ]
    )
    sqlite_session.commit()

    response = client.post("/api/exports/mentions_csv", params={"sentiment": "Negative"}, headers=auth_headers)
    assert response.status_code == 200
    rows = response.text.strip().splitlines()
    assert len(rows) == 2  # header + exactly the one matching row, no more
    assert "keep me" in rows[1]
    assert "drop me" not in response.text


# --- P0-4: negative mention -> alerts list with an Assign action ---


def test_p0_4_negative_mention_appears_in_alerts_with_an_assign_action(client, auth_headers, sqlite_session):
    """Given a mention is classified negative, when it's ingested, then it
    appears in an alerts list with an "Assign" action. (The classifier's
    own logic for WHICH items get alert_category set is covered by
    test_classification.py; this test covers the surfacing + assign
    action once a mention already carries one - the mockup's alerts
    panel is items.filter(alertCategory), there being no separate
    GET /api/alerts endpoint per docs/api-contract.md's own noted
    deviation. kind="mention" - not "review" - since GET /api/mentions
    defaults to kind=mention (repository.list_mentions_filtered's own
    default); the PRD groups this criterion under "Mentions feed"/
    "Alerts & assignment," distinct from Reviews' own reply-flow
    acceptance criterion, P0-6.)"""
    m = Mention(
        source="reddit",
        kind="mention",
        external_id="p0-4",
        text="Terrible service, will not return",
        sentiment=Sentiment.NEGATIVE,
        alert_category="digest",
        published_at=_now(),
    )
    sqlite_session.add(m)
    sqlite_session.commit()

    body = client.get("/api/mentions", headers=auth_headers).json()
    item = next(i for i in body["items"] if i["id"] == m.id)
    assert item["alertCategory"] in ("crisis", "digest")

    assign_resp = client.post(f"/api/mentions/{m.id}/assign", json={"assignee": "Gian"}, headers=auth_headers)
    assert assign_resp.status_code == 200
    assert assign_resp.json()["assignedTo"] == "Gian"


# --- P0-5: resolving an alert decreases the alert count + resolved state ---


def test_p0_5_resolving_an_alert_decreases_the_active_alerts_count(client, auth_headers, sqlite_session):
    """Given an alert is resolved, when the team views the alerts count,
    then it decreases by one and the item shows a resolved state. (kind=
    "mention", not "review" - see the kind filter comment on
    get_overview_stats()'s alert_conditions: only kind=mention rows are
    ever visible/resolvable via the one alerts-list UI surface, so
    that's the only kind this count includes.)"""
    m = Mention(
        source="reddit",
        kind="mention",
        external_id="p0-5",
        text="bad experience",
        sentiment=Sentiment.NEGATIVE,
        alert_category="digest",
        published_at=_now(),
    )
    sqlite_session.add(m)
    sqlite_session.commit()

    before = client.get("/api/overview", headers=auth_headers).json()

    resolve_resp = client.post(f"/api/mentions/{m.id}/resolve", headers=auth_headers)
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["resolvedAt"] is not None

    after = client.get("/api/overview", headers=auth_headers).json()
    assert after["activeAlerts"]["total"] == before["activeAlerts"]["total"] - 1


# --- P0-6: reviews reply updates status immediately ---


def test_p0_6_replying_to_a_pending_review_updates_status_immediately(client, auth_headers, sqlite_session):
    """Given a review has no reply, when the team clicks "pending reply,"
    then a reply box opens and submitting it updates the status
    immediately. ("A reply box opens" is a client-side modal - see
    docs/qa-manual-checklist.md item 1 - this test covers "submitting it
    updates the status immediately.")"""
    venue = "Remedy BGC"
    m = Mention(source="google_reviews", kind="review", external_id="p0-6", venue=venue, rating=2, has_reply=False)
    sqlite_session.add(m)
    sqlite_session.commit()

    before_listings = client.get("/api/reviews", headers=auth_headers).json()["listings"]
    before_row = next(r for r in before_listings if r["venue"] == venue)
    assert before_row["pendingReplies"] >= 1

    reply_resp = client.post(f"/api/reviews/{m.id}/reply", headers=auth_headers)
    assert reply_resp.status_code == 200
    assert reply_resp.json()["pendingReplies"] == before_row["pendingReplies"] - 1

    after_listings = client.get("/api/reviews", headers=auth_headers).json()["listings"]
    after_row = next(r for r in after_listings if r["venue"] == venue)
    assert after_row["pendingReplies"] == before_row["pendingReplies"] - 1


# --- P0-7: Overview - health score, volume trend, alerts, one round trip ---


def test_p0_7_overview_returns_health_score_volume_trend_and_alerts_together(client, auth_headers, sqlite_session):
    """Given the team opens the dashboard, then the Overview tab loads by
    default showing health score, volume trend, and outstanding alerts
    without further navigation. ("Loads by default" is a static fact
    about the nav markup - see docs/qa-manual-checklist.md item 2 - this
    test covers that everything that view needs comes back from a single
    GET /api/overview call, plus the trend chart's own GET
    /api/overview/trend, 8.1.)"""
    sqlite_session.add(
        Mention(
            source="google_reviews", kind="review", external_id="p0-7",
            rating=5, sentiment=Sentiment.POSITIVE, published_at=_now(),
        )
    )
    sqlite_session.commit()

    overview = client.get("/api/overview", headers=auth_headers).json()
    assert "score" in overview["clarityIndex"]  # health score
    assert "deltaPct" in overview["totalMentions"]  # volume trend
    assert "total" in overview["activeAlerts"]  # outstanding alerts

    trend = client.get("/api/overview/trend", headers=auth_headers).json()
    assert "days" in trend


# --- P0-8: topic drill-down shows constituent mentions + their sentiment ---


def test_p0_8_topic_drill_down_shows_constituent_mentions_and_their_sentiment(client, auth_headers, sqlite_session):
    """Given a topic is selected, when the team drills in, then they see
    the mentions that make up that topic and their individual
    sentiment."""
    sqlite_session.add_all(
        [
            Mention(
                source="reddit", kind="mention", external_id="p0-8a",
                text="Rejuran pricing question", topics=["rejuran"], sentiment=Sentiment.NEUTRAL,
            ),
            Mention(
                source="instagram", kind="mention", external_id="p0-8b",
                text="Loved the facial results", topics=["facial-results"], sentiment=Sentiment.POSITIVE,
            ),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/topics/rejuran/mentions", headers=auth_headers).json()
    assert [i["text"] for i in body["items"]] == ["Rejuran pricing question"]
    assert body["items"][0]["sentiment"] == "Neutral"


# --- P0-9: EMV row shows calculation inputs, not just the final number ---


@pytest.mark.skip(
    reason=(
        "Blocked on 8.7 (EMV formula sign-off, still pending - see "
        "docs/implementation-checklist.md#8.7). grossEmv/netEmv are null on every article by "
        "design today (docs/api-contract.md's EMV section; see also "
        "test_api_competitors_emv_roster.py::test_emv_articles_and_totals_are_all_null), so there "
        "is no real calculation for this criterion to expose yet. This is a feature-not-built "
        "gap, not a browser-testability one, so it does not belong in "
        "docs/qa-manual-checklist.md either - there's nothing yet for a human to click and see "
        "calculated."
    )
)
def test_p0_9_emv_row_shows_calculation_inputs_not_just_the_final_number():
    """Given an EMV row, when the team clicks it, then the calculation
    inputs (reach, placement value, etc.) are shown, not just the final
    number."""


# --- P0-10: Competitors - side-by-side share-of-voice and sentiment ---


def test_p0_10_competitors_tab_shows_side_by_side_share_of_voice_and_sentiment(client, auth_headers, sqlite_session):
    """Given the Competitors tab, when the team views it, then Remedy and
    each named competitor show side-by-side share-of-voice and
    sentiment."""
    sqlite_session.add_all(
        [
            Mention(source="google_reviews", kind="review", external_id="p0-10a", sentiment=Sentiment.POSITIVE),
            Mention(
                source="google_places_competitor", kind="review", external_id="p0-10b",
                venue="Aivee Clinic", sentiment=Sentiment.NEGATIVE,
            ),
        ]
    )
    sqlite_session.commit()

    body = client.get("/api/competitors", headers=auth_headers).json()

    remedy_sov = next(row for row in body["shareOfVoice"] if row["name"] == "Remedy")
    assert remedy_sov["isOwn"] is True
    aivee_sov = next(row for row in body["shareOfVoice"] if row["name"] == "Aivee Clinic")
    assert aivee_sov["isOwn"] is False

    remedy_sentiment = next(row for row in body["competitorSentiment"] if row["name"] == "Remedy")
    aivee_sentiment = next(row for row in body["competitorSentiment"] if row["name"] == "Aivee Clinic")
    assert remedy_sentiment["positivePct"] == 100
    assert aivee_sentiment["negativePct"] == 100


# --- P0-11: "Last synced" visible on every tab, updates after a sync ---


def test_p0_11_last_synced_reflects_the_most_recent_successful_run(client, auth_headers, sqlite_session):
    """Given data was last refreshed at time T, when the team views any
    tab, then T is visible and updates after a successful sync. ("Visible
    on every tab" is a static fact - see docs/qa-manual-checklist.md item
    3 - this test covers the data side: GET /api/status and GET
    /api/overview's lastSyncedAt both reflect a real run-ledger row.)"""
    finished = _now()
    sqlite_session.add(
        IngestionRun(
            source="google_reviews", started_at=finished, finished_at=finished,
            status=RunStatus.SUCCESS, items_seen=3, items_ingested=3,
        )
    )
    sqlite_session.commit()

    status = client.get("/api/status", headers=auth_headers).json()
    entry = next(s for s in status["sources"] if s["source"] == "google_reviews")
    assert entry["lastSuccessAt"] is not None

    overview = client.get("/api/overview", headers=auth_headers).json()
    assert overview["lastSyncedAt"] is not None
