"""Tests for app/jobs/google_reviews_job.py (4.1) and
app/jobs/google_places_job.py (4.2). Mocks the fetch layer (the functions
each job imports directly from fetch_owned_reviews.py /
fetch_competitor_ratings.py) exactly like backend/tests/test_http_utils.py
mocks requests.get - monkeypatch the callable in the module under test's
own namespace, never a real network call."""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.jobs import google_places_job, google_reviews_job
from app.models import IngestionRun, Mention, RunStatus
from fetch_owned_reviews import ReviewsAccessDenied
from http_utils import RetryExhaustedError

# --- google_reviews_job ---


def _raw_review(
    review_id="rev1",
    display_name="Juan Dela Cruz",
    star_rating="FIVE",
    comment="Great service!",
    create_time="2026-08-01T10:00:00Z",
    with_reply=True,
):
    review = {
        "name": f"accounts/1/locations/loc1/reviews/{review_id}",
        "reviewId": review_id,
        "reviewer": {"displayName": display_name},
        "starRating": star_rating,
        "comment": comment,
        "createTime": create_time,
    }
    if with_reply:
        review["reviewReply"] = {"comment": "Thank you!"}
    return review


@pytest.fixture(autouse=True)
def _patch_owned_listings(monkeypatch):
    # A real, non-REPLACE_ME OWNED_LISTINGS mapping so run()'s
    # configured_ids lookup actually matches the fake location below.
    monkeypatch.setattr(
        google_reviews_job,
        "OWNED_LISTINGS",
        {"Remedy — BGC": {"location_id": "loc1"}},
    )


def _patch_fetch_layer(monkeypatch, *, reviews=None, get_reviews_side_effect=None):
    monkeypatch.setattr(google_reviews_job, "load_credentials", lambda: object())
    monkeypatch.setattr(google_reviews_job, "get_accounts", lambda creds: [{"name": "accounts/1"}])
    monkeypatch.setattr(
        google_reviews_job,
        "get_locations",
        lambda creds, account_name: [{"name": "locations/loc1", "title": "Remedy — BGC"}],
    )
    if get_reviews_side_effect is not None:
        def fake_get_reviews(creds, account_name, location_name):
            raise get_reviews_side_effect
        monkeypatch.setattr(google_reviews_job, "get_reviews", fake_get_reviews)
    else:
        monkeypatch.setattr(
            google_reviews_job, "get_reviews", lambda creds, account_name, location_name: reviews or []
        )


def test_reviews_job_happy_path_ingests_and_marks_success(sqlite_session, monkeypatch):
    raw = _raw_review()
    _patch_fetch_layer(monkeypatch, reviews=[raw])

    google_reviews_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.source == "google_reviews"
    assert mention.kind == "review"
    assert mention.external_id == "accounts/1/locations/loc1/reviews/rev1"
    assert mention.venue == "Remedy — BGC"
    assert mention.author == "Juan C."
    assert mention.rating == 5
    assert mention.text == "Great service!"
    assert mention.sentiment == "Positive"
    assert mention.has_reply is True
    assert mention.raw_payload == raw
    # SQLite has no native tz-aware storage - DateTime(timezone=True) round-
    # trips as a naive datetime on this backend (Postgres keeps the offset;
    # see docs/decisions/persistence-choice.md), so compare the naive value.
    assert mention.published_at.replace(tzinfo=None) == datetime(2026, 8, 1, 10, 0, 0)

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.source == "google_reviews"
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 1


def test_reviews_job_multiple_reviews_all_ingested(sqlite_session, monkeypatch):
    raws = [_raw_review(review_id="rev1"), _raw_review(review_id="rev2", star_rating="ONE", with_reply=False)]
    _patch_fetch_layer(monkeypatch, reviews=raws)

    google_reviews_job.run(sqlite_session)
    sqlite_session.commit()

    mentions = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(mentions) == 2
    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.items_seen == 2
    assert run_row.items_ingested == 2
    assert run_row.status == RunStatus.SUCCESS


def test_reviews_job_reingest_is_idempotent(sqlite_session, monkeypatch):
    raw = _raw_review()
    _patch_fetch_layer(monkeypatch, reviews=[raw])

    google_reviews_job.run(sqlite_session)
    sqlite_session.commit()
    google_reviews_job.run(sqlite_session)
    sqlite_session.commit()

    mentions = sqlite_session.execute(select(Mention)).scalars().all()
    assert len(mentions) == 1  # (source, external_id) upsert, not a duplicate


def test_reviews_job_403_marks_access_denied_not_a_crash(sqlite_session, monkeypatch):
    _patch_fetch_layer(
        monkeypatch, get_reviews_side_effect=ReviewsAccessDenied("403 from the reviews endpoint")
    )

    google_reviews_job.run(sqlite_session)  # must not raise
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ACCESS_DENIED
    assert "403" in run_row.error
    assert sqlite_session.execute(select(Mention)).scalars().all() == []


def test_reviews_job_retry_exhausted_marks_partial_not_a_crash(sqlite_session, monkeypatch):
    _patch_fetch_layer(
        monkeypatch, get_reviews_side_effect=RetryExhaustedError("gave up after retries")
    )

    google_reviews_job.run(sqlite_session)  # must not raise
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.PARTIAL
    assert "gave up after retries" in run_row.error


def test_reviews_job_unmatched_location_is_skipped_not_fatal(sqlite_session, monkeypatch):
    get_reviews_calls = []
    monkeypatch.setattr(google_reviews_job, "load_credentials", lambda: object())
    monkeypatch.setattr(google_reviews_job, "get_accounts", lambda creds: [{"name": "accounts/1"}])
    monkeypatch.setattr(
        google_reviews_job,
        "get_locations",
        lambda creds, account_name: [{"name": "locations/unknown_loc", "title": "Some Other Business"}],
    )

    def fake_get_reviews(creds, account_name, location_name):
        get_reviews_calls.append(location_name)
        return []

    monkeypatch.setattr(google_reviews_job, "get_reviews", fake_get_reviews)

    google_reviews_job.run(sqlite_session)  # must not raise
    sqlite_session.commit()

    assert get_reviews_calls == []  # an unmatched location must never reach get_reviews()
    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.SUCCESS
    assert run_row.items_seen == 0


def test_reviews_job_no_accounts_marks_error(sqlite_session, monkeypatch):
    monkeypatch.setattr(google_reviews_job, "load_credentials", lambda: object())
    monkeypatch.setattr(google_reviews_job, "get_accounts", lambda creds: [])

    with pytest.raises(RuntimeError, match="No Business Profile accounts"):
        google_reviews_job.run(sqlite_session)
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.ERROR
    assert "No Business Profile accounts" in run_row.error


# --- google_places_job ---


@pytest.fixture(autouse=True)
def _patch_competitor_place_ids(monkeypatch):
    monkeypatch.setattr(
        google_places_job,
        "COMPETITOR_PLACE_IDS",
        {"Belo Medical Group": "place-belo-1", "Not Yet Mapped": "REPLACE_ME_SOMETHING"},
    )


def _place_details(rating=4.5, total=120):
    return {
        "name": "Belo Medical Group",
        "rating": rating,
        "user_ratings_total": total,
        "reviews": [
            {"author_name": "X", "rating": 5, "text": "Great", "relative_time_description": "a week ago"}
        ],
    }


def test_places_job_happy_path_ingests_and_marks_success(sqlite_session, monkeypatch):
    monkeypatch.setattr(google_places_job, "fetch_place_details", lambda place_id: _place_details())

    google_places_job.run(sqlite_session)
    sqlite_session.commit()

    mention = sqlite_session.execute(select(Mention)).scalar_one()
    assert mention.source == "google_places_competitor"
    assert mention.kind == "review"
    assert mention.external_id == "place-belo-1"
    assert mention.venue == "Belo Medical Group"
    assert mention.rating == 4.5
    assert mention.raw_payload == _place_details()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.SUCCESS
    # Only the one non-REPLACE_ME competitor counts as attempted.
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 1


def test_places_job_replace_me_placeholder_is_skipped_not_attempted(sqlite_session, monkeypatch):
    calls = []

    def fake_fetch(place_id):
        calls.append(place_id)
        return _place_details()

    monkeypatch.setattr(google_places_job, "fetch_place_details", fake_fetch)

    google_places_job.run(sqlite_session)
    sqlite_session.commit()

    assert calls == ["place-belo-1"]  # the REPLACE_ME_SOMETHING competitor was never fetched


def test_places_job_not_found_result_marks_partial_and_writes_no_row(sqlite_session, monkeypatch):
    monkeypatch.setattr(google_places_job, "fetch_place_details", lambda place_id: None)

    google_places_job.run(sqlite_session)
    sqlite_session.commit()

    assert sqlite_session.execute(select(Mention)).scalars().all() == []
    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.PARTIAL
    assert run_row.items_seen == 1
    assert run_row.items_ingested == 0
    assert "no result" in run_row.error


def test_places_job_retry_exhausted_marks_partial_not_a_crash(sqlite_session, monkeypatch):
    def fake_fetch(place_id):
        raise RetryExhaustedError("gave up after retries")

    monkeypatch.setattr(google_places_job, "fetch_place_details", fake_fetch)

    google_places_job.run(sqlite_session)  # must not raise
    sqlite_session.commit()

    run_row = sqlite_session.execute(select(IngestionRun)).scalar_one()
    assert run_row.status == RunStatus.PARTIAL
    assert "gave up after retries" in run_row.error
