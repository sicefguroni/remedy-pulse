"""Tests for app/classification.py (Phase 6: 6.1, 6.2, 6.3).

The LLM call is always mocked — never make a real Anthropic API call from
the test suite (cost, network dependency, non-determinism). Every test
monkeypatches `classification._call_model`, which is the one seam
classify_sentiment() calls out to the model through."""

import json
from datetime import datetime

import pytest
from sqlalchemy import select

import app.classification as classification
from app.classification import (
    ClassificationResult,
    ClassifierNotConfiguredError,
    classify_and_store,
    classify_sentiment,
    classify_unclassified_batch,
)
from app.models import Mention, Sentiment


def _make_mention(session, **overrides) -> Mention:
    fields = dict(source="google_reviews", kind="review", external_id="rev-1", text="Great service!")
    fields.update(overrides)
    mention = Mention(**fields)
    session.add(mention)
    session.flush()
    return mention


def _well_formed_response(**overrides) -> str:
    data = {
        "sentiment": "Negative",
        "confidence": 0.92,
        "alert_category": "crisis",
        "reasoning": "Patient describes a viral complication after treatment — patient safety risk.",
    }
    data.update(overrides)
    return json.dumps(data)


# --- classify_sentiment() ---


def test_classify_sentiment_parses_well_formed_response(monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())

    result = classify_sentiment("Ang sakit ng ginawa nila, na-infect pa ako!")

    assert isinstance(result, ClassificationResult)
    assert result.sentiment == "Negative"
    assert result.confidence == pytest.approx(0.92)
    assert result.alert_category == "crisis"
    assert "patient safety" in result.reasoning.lower() or "viral complication" in result.reasoning.lower()


def test_classify_sentiment_parses_response_wrapped_in_markdown_fence(monkeypatch):
    fenced = "```json\n" + _well_formed_response(sentiment="Positive", alert_category="digest") + "\n```"
    monkeypatch.setattr(classification, "_call_model", lambda text: fenced)

    result = classify_sentiment("Loved the results, highly recommend!")

    assert result.sentiment == "Positive"
    assert result.alert_category == "digest"


def test_classify_sentiment_malformed_response_returns_low_confidence_result(monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: "not json at all, sorry")

    result = classify_sentiment("some mention text")

    assert isinstance(result, ClassificationResult)
    assert result.confidence == 0.0
    assert result.sentiment == "Neutral"
    assert result.alert_category == "digest"
    assert "could not parse" in result.reasoning.lower()


def test_classify_sentiment_response_with_invalid_enum_values_returns_low_confidence_result(monkeypatch):
    bad = json.dumps({"sentiment": "Very Negative", "confidence": 0.9, "alert_category": "crisis", "reasoning": "x"})
    monkeypatch.setattr(classification, "_call_model", lambda text: bad)

    result = classify_sentiment("some mention text")

    assert result.confidence == 0.0
    assert result.sentiment == "Neutral"
    assert result.alert_category == "digest"


def test_classify_sentiment_clamps_out_of_range_confidence(monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response(confidence=1.5))

    result = classify_sentiment("some mention text")

    assert result.confidence == 1.0


def test_classify_sentiment_blank_text_short_circuits_without_calling_model(monkeypatch):
    called = []
    monkeypatch.setattr(classification, "_call_model", lambda text: called.append(text) or _well_formed_response())

    result = classify_sentiment("   ")

    assert called == []  # never reached the model
    assert result.confidence == 0.0
    assert result.alert_category == "digest"


def test_classify_sentiment_raises_clear_error_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(classification, "ANTHROPIC_API_KEY", None)

    with pytest.raises(ClassifierNotConfiguredError, match="ANTHROPIC_API_KEY"):
        classify_sentiment("some text")


def test_classify_sentiment_degrades_gracefully_on_transient_api_error(monkeypatch):
    # A rate limit / timeout / provider 5xx - the call itself fails, no
    # response ever comes back to parse. Must NOT crash the caller (see
    # classify_unclassified_batch(), which has no per-item try/except of
    # its own precisely because classify_sentiment() is supposed to
    # absorb this) - reconciled in after review found this path used to
    # propagate uncaught, unlike the sibling malformed-response path.
    def _raise_api_error(text):
        raise classification._ApiCallError("rate limited")

    monkeypatch.setattr(classification, "_call_model", _raise_api_error)

    result = classify_sentiment("some text")

    assert result.sentiment == "Neutral"
    assert result.confidence == 0.0
    assert result.alert_category == "digest"
    assert "rate limited" in result.reasoning


def test_classify_unclassified_batch_survives_a_transient_api_error_on_one_item(sqlite_session, monkeypatch):
    # The batch must not abort just because one item's API call failed -
    # only ClassifierNotConfiguredError (every remaining item would fail
    # identically) should stop it early.
    _make_mention(sqlite_session, external_id="err-1", text="a review")
    sqlite_session.commit()

    monkeypatch.setattr(
        classification, "_call_model",
        lambda text: (_ for _ in ()).throw(classification._ApiCallError("boom")),
    )

    count = classify_unclassified_batch(sqlite_session)
    sqlite_session.commit()

    assert count == 1
    row = sqlite_session.execute(select(Mention)).scalar_one()
    assert row.classified_at is not None
    assert row.sentiment == "Neutral"
    assert row.alert_category == "digest"


# --- classify_and_store() ---


def test_classify_and_store_writes_all_four_fields(sqlite_session, monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())
    mention = _make_mention(sqlite_session, text="na-infect ako after ng treatment")
    sqlite_session.commit()

    result_mention = classify_and_store(sqlite_session, mention.id)
    sqlite_session.commit()

    assert result_mention.id == mention.id
    assert result_mention.sentiment == "Negative"
    assert result_mention.sentiment_confidence == pytest.approx(0.92)
    assert result_mention.classified_at is not None
    assert result_mention.alert_category == "crisis"

    row = sqlite_session.execute(select(Mention).where(Mention.id == mention.id)).scalar_one()
    assert row.sentiment == "Negative"
    assert row.sentiment_confidence == pytest.approx(0.92)
    assert row.classified_at is not None
    assert row.alert_category == "crisis"


def test_classify_and_store_overwrites_star_derived_sentiment(sqlite_session, monkeypatch):
    """6.2: a review's star-derived sentiment is a placeholder that
    classify_and_store() must overwrite with the real classifier output."""
    monkeypatch.setattr(
        classification, "_call_model",
        lambda text: _well_formed_response(sentiment="Positive", alert_category="digest"),
    )
    mention = _make_mention(
        sqlite_session, kind="review", rating=1, sentiment=Sentiment.NEGATIVE, text="grabe ang ganda naman pala",
    )
    sqlite_session.commit()
    assert mention.classified_at is None  # still the star-derived placeholder

    classify_and_store(sqlite_session, mention.id)
    sqlite_session.commit()

    row = sqlite_session.execute(select(Mention).where(Mention.id == mention.id)).scalar_one()
    assert row.sentiment == "Positive"  # overwritten by the LLM, not "Negative" from the 1-star rating
    assert row.classified_at is not None


def test_classify_and_store_raises_value_error_for_unknown_id(sqlite_session):
    with pytest.raises(ValueError, match="No Mention with id"):
        classify_and_store(sqlite_session, 999999)


# --- classify_unclassified_batch() ---


def test_classify_unclassified_batch_only_touches_unclassified_rows_with_text(sqlite_session, monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())

    _make_mention(sqlite_session, external_id="a", text="some text")
    _make_mention(sqlite_session, external_id="b", text=None)  # no text — skipped
    already_classified = _make_mention(
        sqlite_session, external_id="c", text="already done", classified_at=datetime(2026, 1, 1),
        sentiment="Positive", alert_category="digest",
    )
    sqlite_session.commit()

    count = classify_unclassified_batch(sqlite_session)
    sqlite_session.commit()

    assert count == 1
    row_a = sqlite_session.execute(select(Mention).where(Mention.external_id == "a")).scalar_one()
    assert row_a.classified_at is not None
    row_b = sqlite_session.execute(select(Mention).where(Mention.external_id == "b")).scalar_one()
    assert row_b.classified_at is None  # untouched — no text

    row_c = sqlite_session.execute(select(Mention).where(Mention.external_id == "c")).scalar_one()
    assert row_c.sentiment == "Positive"  # untouched — was already classified
    assert row_c.classified_at == already_classified.classified_at


def test_classify_unclassified_batch_respects_limit(sqlite_session, monkeypatch):
    call_count = {"n": 0}

    def _fake_call(text):
        call_count["n"] += 1
        return _well_formed_response()

    monkeypatch.setattr(classification, "_call_model", _fake_call)

    for i in range(5):
        _make_mention(sqlite_session, external_id=f"item-{i}", text=f"text {i}")
    sqlite_session.commit()

    count = classify_unclassified_batch(sqlite_session, limit=3)
    sqlite_session.commit()

    assert count == 3
    assert call_count["n"] == 3
    classified_count = sqlite_session.execute(
        select(Mention).where(Mention.classified_at.isnot(None))
    ).scalars().all()
    assert len(classified_count) == 3


def test_classify_unclassified_batch_returns_zero_when_nothing_to_classify(sqlite_session, monkeypatch):
    monkeypatch.setattr(classification, "_call_model", lambda text: _well_formed_response())

    count = classify_unclassified_batch(sqlite_session)

    assert count == 0
