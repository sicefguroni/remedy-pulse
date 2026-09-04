"""topic_tagging.py — LLM-based topic TAGGING against a fixed taxonomy (6.5).

Read docs/decisions/topic-tagging-approach.md before touching this file —
it explains the scoping decision this module implements: tagging each
item against a known, fixed five-topic list (facial-results,
staff-service, rejuran, pricing, booking — the mockup's own
`topicMentions` keys/labels, remedy-pulse-mockup.html), NOT true
unsupervised topic *clustering* (discovering the topics themselves from
data). The checklist's 6.5 item is titled "Topic clustering," but real
clustering needs real data volume this project doesn't have live yet —
see that doc for the full reasoning and for what would make real
clustering viable later. `Mention.topics` (a JSON list-of-strings column,
Phase 2) needed no schema change to support this scoped-down version —
only `tag_and_store()`'s LLM call would need to change to migrate to true
clustering later.

Design notes:

- One Claude API call per item (`tag_topics()`), not a batch endpoint —
  matches this project's existing 6.1 lean ("hosted LLM, batched [meaning
  called per item across many items], with the raw text and the label
  both stored so you can re-score later") for the sibling sentiment
  classifier. Topic tagging is a small, cheap classification call
  (`max_tokens=256`, `output_config.effort="low"` — a fixed-list
  membership check doesn't need deep reasoning), same reasoning 6.1
  already accepted for cost at this project's stated per-week item
  volume.
- Model: `claude-opus-5` — this project's default per the claude-api
  skill ("ALWAYS use claude-opus-5 unless the user explicitly names a
  different model"); nothing in this batch's scope named a different one.
- `classification.py` does not exist yet on this branch (checked before
  writing this file) — the API layer / sentiment classifier is being
  built in parallel by another agent. This module's error handling
  (missing API key -> empty list + warning log, malformed response ->
  empty list + warning log, never raise from `tag_topics()` itself) is
  this module's own best-judgment choice, not copied from an existing
  pattern. A later reconciliation pass should align the two once
  classification.py lands, per this batch's own instructions — don't
  block this module on that landing first.
- `tag_topics()` returning `[]` covers two different real cases that
  look identical from its return value alone: "the LLM ran and found no
  matching topic" and "tagging couldn't run at all" (no API key, or the
  API/parse failed). That distinction matters less here than it does for
  `Mention.topics` itself (see below) — `tag_topics()` is a pure
  function with no partial-failure state to preserve, so collapsing both
  cases to `[]` is the simplest contract for its callers. What must stay
  distinguishable is `Mention.topics` being `None` ("never tagged") vs.
  `[]` ("tagged, matched nothing") — that's what `tag_and_store()` and
  `tag_untagged_batch()` preserve: `tag_and_store()` always writes
  whatever `tag_topics()` returns (including `[]`) onto a row that used
  to be `None`, and `tag_untagged_batch()` only ever selects rows still
  sitting at `None`.
"""

from __future__ import annotations

import json
import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classification import ClassifierNotConfiguredError
from app.models import Mention

logger = logging.getLogger(__name__)

# The five topics the mockup already shows (remedy-pulse-mockup.html,
# `topicMentions` — keys and labels copied verbatim from there, not
# guessed). Keep these in sync if the mockup's own taxonomy ever changes;
# per the module docstring, migrating to a *different* set of fixed
# topics is just editing this dict, and migrating away from a fixed list
# entirely (real clustering) is a different tagging function, not a
# schema or taxonomy-format change.
TOPIC_TAXONOMY: dict[str, str] = {
    "facial-results": "Facial Results & Glow",
    "staff-service": "Staff & Service Experience",
    "rejuran": "Rejuran Specifically",
    "pricing": "Pricing & Packages",
    "booking": "Booking & Follow-up Response",
}

_MODEL = "claude-opus-5"

_SYSTEM_PROMPT = (
    "You are a topic-tagging assistant for Remedy Skin Clinic's reputation "
    "monitoring dashboard. You will be given one piece of text — a review, "
    "a social/forum mention, or a press excerpt — about the clinic. Decide "
    "which of the following fixed topics it discusses. A single text may "
    "match zero, one, or more than one topic (e.g. a review can mention "
    "both pricing and booking friction at once).\n\n"
    + "\n".join(f"- {key}: {label}" for key, label in TOPIC_TAXONOMY.items())
    + "\n\nRespond with ONLY a JSON object of the exact shape "
    '{"topics": ["<topic-key>", ...]}, using only the topic keys listed '
    'above (never the labels). If none of the topics apply, respond with '
    '{"topics": []}. No other text, no markdown code fences, no '
    "explanation."
)


def tag_topics(text: str) -> list[str]:
    """One Claude API call. Returns the subset of TOPIC_TAXONOMY's keys
    the model judged `text` to match — zero, one, or several.

    Raises ClassifierNotConfiguredError (shared with app.classification —
    same exception, not a duplicate, reconciled in after review found
    this module originally collapsed "not configured" and "ran but
    failed" into the same silent-[] behavior, unlike its sibling
    classify_sentiment(); every remaining item in a batch would fail
    identically if the key/package is missing, so tag_untagged_batch()
    should stop immediately rather than silently tagging N items as "no
    topics" when tagging never actually ran) when ANTHROPIC_API_KEY isn't
    set or the `anthropic` package isn't installed — imported lazily,
    same guarded-import pattern as app.classification._get_client().

    Returns [] (never raises) for the failure modes that ARE per-item and
    transient: an API-level error (rate limit, timeout, bad request, ...)
    or a response that isn't the expected {"topics": [...]} JSON shape.
    Each logs a warning naming which case it was, so a silent-tagging-
    failure is visible in logs without turning into a crash for whatever
    called this. Also returns [] for empty/blank text without making an
    API call at all — there's nothing to tag.
    """
    if not text or not text.strip():
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClassifierNotConfiguredError(
            "ANTHROPIC_API_KEY is not set. Topic tagging calls the "
            "Anthropic API and cannot run without it."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise ClassifierNotConfiguredError(
            "The `anthropic` package is not installed. Topic tagging "
            "needs the Anthropic Python SDK (`pip install anthropic`)."
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.APIError as exc:
        logger.warning("tag_topics(): Claude API call failed (%s); returning []", exc)
        return []

    raw_text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        parsed = json.loads(raw_text)
        topics = parsed["topics"]
        if not isinstance(topics, list):
            raise ValueError(f"`topics` was {type(topics).__name__}, not a list")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("tag_topics(): malformed response (%s); returning []. Raw response: %r", exc, raw_text)
        return []

    valid_topics = [t for t in topics if t in TOPIC_TAXONOMY]
    if len(valid_topics) != len(topics):
        logger.warning(
            "tag_topics(): response included unknown topic key(s), dropped: %r",
            [t for t in topics if t not in TOPIC_TAXONOMY],
        )
    return valid_topics


def tag_and_store(session: Session, mention_id: int) -> Mention:
    """Loads the Mention, tags its text, writes the result onto
    Mention.topics, and returns the row. Always writes — including an
    empty list — so a tagged-and-found-nothing row is stored as [], never
    left as None (see the module docstring on why that distinction has to
    survive this call). Raises ValueError if mention_id doesn't exist,
    matching repository.assign_mention()/resolve_mention()'s existing
    "check first, fail clearly" pattern for an unknown id."""
    mention = session.get(Mention, mention_id)
    if mention is None:
        raise ValueError(f"No Mention with id={mention_id!r}")
    mention.topics = tag_topics(mention.text or "")
    session.flush()
    return mention


def tag_untagged_batch(session: Session, *, limit: int = 50) -> int:
    """Finds Mention rows with topics IS NULL (never tagged — distinct
    from [], see above) and text IS NOT NULL, tags up to `limit` of them,
    and returns the count actually tagged. A row whose tagging call fails
    (see tag_topics()'s [] fallback) still counts as tagged here — it was
    processed and now holds a real (empty) result, it just isn't sitting
    at None anymore, exactly like a row the model genuinely found no
    topics for."""
    mention_ids = (
        session.execute(
            select(Mention.id)
            .where(Mention.topics.is_(None), Mention.text.isnot(None))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    count = 0
    for mention_id in mention_ids:
        tag_and_store(session, mention_id)
        count += 1
    return count
