"""Tests for the 6.5 topic-tagging module (app/topic_tagging.py). The
Claude API call is always mocked — see topic_tagging.py's module
docstring and docs/decisions/11-topic-tagging-approach.md for why this is
scoped as "tag against a fixed taxonomy," not real clustering. Runs
against in-memory SQLite (see conftest.sqlite_session), same as
test_app_auth.py."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic

# anthropic 1.x is built on httpx2, not the separate `httpx` PyPI package
# (see the claude-api skill's API-drift warning) - anthropic.APIError's
# `request` argument is typed as httpx2.Request, and httpx2 is already an
# anthropic dependency, so no new test dependency is needed here.
import httpx2 as httpx
import pytest
from sqlalchemy import select

from app.classification import ClassifierNotConfiguredError
from app.models import Mention
from app.topic_tagging import (
    TOPIC_TAXONOMY,
    tag_and_store,
    tag_topics,
    tag_untagged_batch,
)


def _fake_response(payload: dict) -> SimpleNamespace:
    """Builds a minimal stand-in for the Anthropic SDK's Message object —
    just enough shape for tag_topics()'s `next(b.text for b in
    response.content if b.type == "text")` to work."""
    text_block = SimpleNamespace(type="text", text=json.dumps(payload))
    return SimpleNamespace(content=[text_block])


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    # Every test gets a fake key set by default; tests that care about the
    # missing-key case unset it explicitly.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")


# --- tag_topics ---


def test_tag_topics_parses_a_multi_topic_response(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response({"topics": ["pricing", "booking"]})
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    result = tag_topics("Anyone know the price for Rejuran, and how do I book?")

    assert result == ["pricing", "booking"]


def test_tag_topics_returns_empty_list_when_no_topic_matches(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response({"topics": []})
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    result = tag_topics("Completely unrelated text about the weather today.")

    assert result == []


def test_tag_topics_returns_a_single_topic_when_exactly_one_matches(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response({"topics": ["staff-service"]})
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    result = tag_topics("Front desk remembered my name, such a nice touch.")

    assert result == ["staff-service"]


def test_tag_topics_drops_unknown_topic_keys_from_the_response(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        {"topics": ["pricing", "made-up-topic-key"]}
    )
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    result = tag_topics("Some text")

    assert result == ["pricing"]


def test_tag_topics_returns_empty_list_on_malformed_json(monkeypatch):
    mock_client = MagicMock()
    text_block = SimpleNamespace(type="text", text="not valid json at all")
    mock_client.messages.create.return_value = SimpleNamespace(content=[text_block])
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_when_topics_key_is_missing(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response({"unexpected": "shape"})
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_when_topics_value_is_not_a_list(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response({"topics": "pricing"})
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    assert tag_topics("Some text") == []


def test_tag_topics_returns_empty_list_on_api_error(monkeypatch):
    mock_client = MagicMock()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mock_client.messages.create.side_effect = anthropic.APIError("boom", request, body=None)
    monkeypatch.setattr(anthropic, "Anthropic", MagicMock(return_value=mock_client))

    assert tag_topics("Some text") == []


def test_tag_topics_raises_clear_error_without_calling_the_api_when_key_is_missing(monkeypatch):
    # Reconciled in after review: a missing API key means every remaining
    # item in a batch would fail identically, so this must raise (like
    # app.classification.classify_sentiment()'s equivalent case) rather
    # than silently return [] N times in a row — see
    # ClassifierNotConfiguredError's docstring in app/classification.py.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mock_anthropic_cls = MagicMock()
    monkeypatch.setattr(anthropic, "Anthropic", mock_anthropic_cls)

    with pytest.raises(ClassifierNotConfiguredError, match="ANTHROPIC_API_KEY"):
        tag_topics("Some text")

    mock_anthropic_cls.assert_not_called()


def test_tag_topics_returns_empty_list_for_blank_text_without_calling_the_api(monkeypatch):
    mock_anthropic_cls = MagicMock()
    monkeypatch.setattr(anthropic, "Anthropic", mock_anthropic_cls)

    assert tag_topics("") == []
    assert tag_topics("   ") == []
    mock_anthropic_cls.assert_not_called()


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
