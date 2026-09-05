"""Tests for the 6.5 topic-tagging module (app/topic_tagging.py). The
Groq API call is always mocked — see topic_tagging.py's module
docstring and docs/decisions/11-topic-tagging-approach.md for why this is
scoped as "tag against a fixed taxonomy," not real clustering. Every test
monkeypatches `topic_tagging._call_model`, the one seam tag_topics()
calls out to the model through - mirrors test_classification.py's
identical pattern for its sibling module. Runs against in-memory SQLite
(see conftest.sqlite_session), same as test_app_auth.py."""

import json

import pytest
from sqlalchemy import select

import app.topic_tagging as topic_tagging
from app.classification import ClassifierNotConfiguredError
from app.models import Mention
from app.topic_tagging import (
    TOPIC_TAXONOMY,
    tag_and_store,
    tag_topics,
    tag_untagged_batch,
)


def _response(topics: list[str]) -> str:
    return json.dumps({"topics": topics})


# --- tag_topics ---


def test_tag_topics_parses_a_multi_topic_response(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: _response(["pricing", "booking"]))

    result = tag_topics("Anyone know the price for Rejuran, and how do I book?")

    assert result == ["pricing", "booking"]


def test_tag_topics_returns_empty_list_when_no_topic_matches(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: _response([]))

    result = tag_topics("Completely unrelated text about the weather today.")

    assert result == []


def test_tag_topics_returns_a_single_topic_when_exactly_one_matches(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: _response(["staff-service"]))

    result = tag_topics("Front desk remembered my name, such a nice touch.")

    assert result == ["staff-service"]


def test_tag_topics_drops_unknown_topic_keys_from_the_response(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: _response(["pricing", "made-up-topic-key"]))

    result = tag_topics("Some text")

    assert result == ["pricing"]


def test_tag_topics_returns_empty_list_on_malformed_json(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: "not valid json at all")

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_when_topics_key_is_missing(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: json.dumps({"unexpected": "shape"}))

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_when_topics_value_is_not_a_list(monkeypatch):
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: json.dumps({"topics": "pricing"}))

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_on_api_error(monkeypatch):
    def _raise_api_error(text):
        raise topic_tagging._ApiCallError("boom")

    monkeypatch.setattr(topic_tagging, "_call_model", _raise_api_error)

    assert tag_topics("Some text") == []


def test_tag_topics_raises_clear_error_without_calling_the_model_when_key_is_missing(monkeypatch):
    # Reconciled in after review: a missing API key means every remaining
    # item in a batch would fail identically, so this must raise (like
    # app.classification.classify_sentiment()'s equivalent case) rather
    # than silently return [] N times in a row — see
    # ClassifierNotConfiguredError's docstring in app/classification.py.
    # Deliberately does NOT mock _call_model here - lets the real function
    # run, so this exercises the real "no key -> raise before ever
    # importing groq or building a client" path, not a stand-in for it.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(ClassifierNotConfiguredError, match="GROQ_API_KEY"):
        tag_topics("Some text")


def test_tag_topics_returns_empty_list_for_blank_text_without_calling_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(topic_tagging, "_call_model", lambda text: called.append(text) or _response([]))

    assert tag_topics("") == []
    assert tag_topics("   ") == []
    assert called == []  # never reached the model


def test_topic_taxonomy_has_the_five_mockup_topics_and_labels():
    # Verbatim match against remedy-pulse-mockup.html's topicMentions keys
    # and labels — see topic_tagging.py's module docstring.
    assert TOPIC_TAXONOMY == {
        "facial-results": "Facial Results & Glow",
        "staff-service": "Staff & Service Experience",
        "rejuran": "Rejuran Specifically",
        "pricing": "Pricing & Packages",
        "booking": "Booking & Follow-up Response",
    }


# --- tag_and_store ---


def test_tag_and_store_writes_topics_onto_the_mention(sqlite_session, monkeypatch):
    mention = Mention(source="google_reviews", kind="review", external_id="r1", text="Great facial results!")
    sqlite_session.add(mention)
    sqlite_session.commit()

    monkeypatch.setattr("app.topic_tagging.tag_topics", lambda text: ["facial-results"])

    result = tag_and_store(sqlite_session, mention.id)
    sqlite_session.commit()

    assert result.topics == ["facial-results"]
    row = sqlite_session.get(Mention, mention.id)
    assert row.topics == ["facial-results"]


def test_tag_and_store_stores_empty_list_not_none_when_nothing_matches(sqlite_session, monkeypatch):
    """None vs [] has to stay distinguishable — a tagged row that matched
    nothing must not look identical to a never-tagged row."""
    mention = Mention(source="reddit", kind="mention", external_id="m1", text="Totally unrelated content.")
    sqlite_session.add(mention)
    sqlite_session.commit()
    assert mention.topics is None

    monkeypatch.setattr("app.topic_tagging.tag_topics", lambda text: [])

    tag_and_store(sqlite_session, mention.id)
    sqlite_session.commit()

    row = sqlite_session.get(Mention, mention.id)
    assert row.topics == []
    assert row.topics is not None


def test_tag_and_store_raises_value_error_for_unknown_mention_id(sqlite_session):
    with pytest.raises(ValueError, match="99999"):
        tag_and_store(sqlite_session, 99999)


# --- tag_untagged_batch ---


def test_tag_untagged_batch_only_tags_rows_with_null_topics_and_non_null_text(sqlite_session, monkeypatch):
    never_tagged = Mention(source="google_reviews", kind="review", external_id="a", text="Some review text")
    already_tagged = Mention(
        source="google_reviews", kind="review", external_id="b", text="Other text", topics=["pricing"]
    )
    no_text = Mention(source="google_reviews", kind="review", external_id="c", text=None)
    sqlite_session.add_all([never_tagged, already_tagged, no_text])
    sqlite_session.commit()

    monkeypatch.setattr("app.topic_tagging.tag_topics", lambda text: ["booking"])

    count = tag_untagged_batch(sqlite_session)
    sqlite_session.commit()

    assert count == 1
    assert sqlite_session.get(Mention, never_tagged.id).topics == ["booking"]
    # Untouched rows stay exactly as they were.
    assert sqlite_session.get(Mention, already_tagged.id).topics == ["pricing"]
    assert sqlite_session.get(Mention, no_text.id).topics is None


def test_tag_untagged_batch_respects_limit(sqlite_session, monkeypatch):
    for i in range(5):
        sqlite_session.add(
            Mention(source="google_reviews", kind="review", external_id=f"ext-{i}", text=f"Text {i}")
        )
    sqlite_session.commit()

    monkeypatch.setattr("app.topic_tagging.tag_topics", lambda text: [])

    count = tag_untagged_batch(sqlite_session, limit=3)
    sqlite_session.commit()

    assert count == 3
    tagged_count = len(
        sqlite_session.execute(select(Mention).where(Mention.topics.isnot(None))).scalars().all()
    )
    assert tagged_count == 3


def test_tag_untagged_batch_returns_zero_when_nothing_to_tag(sqlite_session):
    assert tag_untagged_batch(sqlite_session) == 0
