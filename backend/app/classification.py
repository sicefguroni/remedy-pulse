"""classification.py — sentiment classification and crisis/digest alert
routing (Phase 6: 6.1 the classifier, 6.2 reconciling the two sentiment
definitions, 6.3 the alert-routing rules).

See `docs/decisions/09-sentiment-classifier-choice.md` for the model choice,
the proposed precision/recall bar, and why this is a *recommendation* for
the team to ratify — same status as every other `docs/decisions/*.md` in
this repo — not a unilateral technical decision that also happens to bind
the team's acceptable-risk threshold for the alert workflow.

Model provider: Groq (`openai/gpt-oss-120b`), switched from Claude Opus
5 on explicit user direction — see the decision doc's "Update
(2026-09-05)" section for why (cost: Anthropic has no real free tier,
unlike everything else this project runs on) and what that trade-off
actually costs in classification quality, honestly, rather than assumed
away. `llama-3.3-70b-versatile` (the model this switch first landed
with) was deprecated/removed from Groq's lineup by the time a real API
key existed to test against — caught by a live smoke test (a 404
`model_not_found`), not assumed working from documentation. See the
same "Update" section for the real Taglish crisis/digest test cases
that verified `openai/gpt-oss-120b` in its place.

--- 6.1: one call does both sentiment AND crisis/digest routing ---

`classify_sentiment()` calls the model exactly once per item and asks for
sentiment, confidence, and the crisis/digest routing decision together.
This is deliberate, not a shortcut: the five crisis / five digest
conditions below (verbatim from the mockup's `openAlertRulesModal()`,
citing spec §9.2 — patient safety, high-velocity negative sentiment,
mainstream media exposure, founder/doctor reputation risk, legal/
regulatory escalation vs. positive coverage, routine reviews, competitive
intel, trend monitoring, low-level negative feedback without traction)
are qualitative judgment calls, not simple keyword rules derivable from
sentiment alone — a "Negative" item is not automatically a crisis (see
"low-level negative feedback without traction," a Digest condition), and
a "Positive" item is never a crisis. One well-crafted call handling both
is more coherent than two separate calls that would have to agree with
each other after the fact.

--- 6.2: reconciling the two conflicting definitions of `sentiment` ---

`fetch_owned_reviews.normalize_reviews()` derives `Mention.sentiment`
purely from star rating (>=4 Positive, <=2 Negative, else Neutral) — a
placeholder, not a real classification, stored in the same column a
text-based classifier also writes to. This module's position, per 6.2:

  - The star-derived value is a TEMPORARY placeholder. It is better than
    nothing (a 1-star review is very likely negative), so it stays on the
    row until something better runs — this module does not blank it out.
  - The moment a row goes through `classify_and_store()` (directly or via
    `classify_unclassified_batch()`), its `sentiment`/`sentiment_confidence`
    are OVERWRITTEN with the real classifier's output, and `classified_at`
    is set. From that point, `sentiment` means "what the LLM classifier
    decided reading the text," identically for a review, a Reddit mention,
    or a press article — never "what the star rating implied."
  - `classified_at IS NULL` is the honest, queryable way to tell the two
    populations apart: still-placeholder rows (reviews only — other kinds
    have `sentiment IS NULL` until classified, since nothing else in this
    codebase derives a placeholder for them) vs. really-classified rows.
    A caller that mixes/sorts/filters on `sentiment` across `kind`s and
    cares about this distinction should filter or segment on
    `classified_at` alongside it — this module doesn't hide the seam,
    it names it.
  - `classify_sentiment()` does NOT special-case `kind="review"` or take
    `rating` as a hint. Every item is classified the same way, from
    `text` alone, regardless of `kind`. Passing `rating` in would just be
    re-deriving the same star-based signal 6.1 exists to replace with
    something that actually handles Taglish/code-switched text — see the
    decision doc's reasoning on why a text-based classifier was chosen
    over the star-rating shortcut in the first place.

--- Schema (read, not modified, by this module) ---

`Mention.sentiment_confidence` / `classified_at` / `alert_category` were
added ahead of this task specifically so this module doesn't need to
touch `app/models.py` — see that module's docstring, "classification
(Phase 6)" section, for the exact reasoning already written there
(sentiment_confidence nullable because a star-derived sentiment has no
classifier confidence to report; classified_at nullable and is the
signal above; alert_category is "crisis" | "digest" | null, where null
means "not yet classified" — the digest rules' own catch-all "routine"/
"low-level" tier means every classified row gets one of the two values,
never a third "neither" state).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Mention

# Same pattern as the existing fetch_*.py connectors and app/auth.py: read
# credentials via os.getenv() after load_dotenv(), rather than threading
# a new field through app.config.Settings — this module is explicitly out
# of scope for touching config.py (see the batch's file-ownership note).
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# See docs/decisions/09-sentiment-classifier-choice.md for why this model.
MODEL_ID = "openai/gpt-oss-120b"
MAX_TOKENS = 1024

VALID_SENTIMENTS = {"Positive", "Neutral", "Negative"}
VALID_ALERT_CATEGORIES = {"crisis", "digest"}


class ClassifierNotConfiguredError(RuntimeError):
    """Raised by classify_sentiment() (via _get_client()) when the
    classifier can't run at all — no GROQ_API_KEY, or the `groq` package
    isn't installed. Matches this project's existing connector
    convention of a clear, specific failure naming the exact problem (see
    fetch_news_articles.py's `raise SystemExit("GNEWS_API_KEY is not
    set...")` and app.auth.DuplicateEmailError) — a RuntimeError, not
    SystemExit, because this is a library function a caller (a future
    scheduled job in app/jobs/) needs to be able to catch, log, and skip
    past, not something that should kill the whole process.
    """


class _ApiCallError(RuntimeError):
    """Internal: raised by _call_model() when the Groq SDK call itself
    fails (rate limit, timeout, a 5xx from the provider — a response
    never came back, as opposed to _parse_response()'s "a response came
    back but wasn't the expected shape"). Deliberately NOT `groq.APIError`
    directly: catching that here would require `groq` to be a safely-bound
    name in classify_sentiment()'s scope even when the package isn't
    installed (the ClassifierNotConfiguredError case), which risks a
    NameError masking that cleaner error instead of propagating it.
    _call_model() is the one place that already knows `groq` imported
    successfully (it's downstream of _get_client()), so it's the only
    place that catches the SDK's own exception type."""


@dataclass
class ClassificationResult:
    """The output of one classify_sentiment() call. `sentiment` is one of
    "Positive"/"Neutral"/"Negative" (matching models.Sentiment's values);
    `alert_category` is "crisis" or "digest" (matching
    Mention.alert_category, minus the null "not yet classified" state,
    which doesn't apply to a result that was actually produced).
    `reasoning` is a short human-readable explanation — useful for the
    audit trail and for a human reviewing why an item got routed where it
    did, e.g. in a future alert-detail UI."""

    sentiment: str
    confidence: float
    alert_category: str
    reasoning: str


# The five crisis / five digest conditions below are copied verbatim from
# remedy-pulse-mockup.html's openAlertRulesModal() (search that function
# name), which itself cites "Per Gian's update to §9.2." This is the
# spec that already exists and is stakeholder-visible in the mockup's own
# classification-rules modal — implemented here, not reinvented.
_SYSTEM_PROMPT = """You are the sentiment and alert-routing classifier for \
Remedy Pulse, a social/media listening dashboard for a medical aesthetics \
clinic group. For each item of text (a review, a social/forum mention, or \
a press article excerpt), you decide:

1. Its sentiment: Positive, Neutral, or Negative.
2. Whether it routes to a Crisis Alert (sent immediately) or the Daily \
Digest (no immediate action) — per spec §9.2's routing rules below, \
copied verbatim from the product's own alert-classification-rules modal.

Crisis Alert — send immediately if ANY of these apply:
- Patient safety or medical risk — injury, malpractice, viral complication, misinformation
- High-velocity negative sentiment — rapid engagement, a 10K+ follower critic, a pile-on thread
- Mainstream media exposure — negative or investigative coverage
- Founder or doctor reputation risk — personal attacks, misquotes
- Legal or regulatory escalation — FDA, DOH, PRC, DTI, legal threats

Daily Digest — no immediate action:
- Positive coverage, features, awards, event mentions
- Routine reviews, organic mentions, pricing questions
- Competitive intelligence — launches, promos, competitor press
- Trend monitoring and emerging conversations
- Low-level negative feedback without traction

Every item gets routed to exactly one of "crisis" or "digest" — there is \
no third option. A negative item is not automatically a crisis (see \
"low-level negative feedback without traction"); a positive item is \
never a crisis. Use your judgment on ambiguous cases — these are \
qualitative calls about patient safety, reputational, and legal risk, \
not keyword matching. The text may be in English, Tagalog, or Taglish \
(code-switched) — read it in whatever language(s) it's actually written \
in, including sarcasm, rather than defaulting to a literal reading.

Respond with ONLY a single JSON object, no markdown code fences, no text \
before or after it, in exactly this shape:
{"sentiment": "Positive" | "Neutral" | "Negative", "confidence": <number \
0.0-1.0>, "alert_category": "crisis" | "digest", "reasoning": "<one or \
two sentences: what you saw in the text, and which specific \
crisis/digest condition it matches>"}"""


def _build_user_prompt(text: str) -> str:
    return f"Classify this item:\n\n{text}"


def _get_client():
    """Builds the Groq client, or raises ClassifierNotConfiguredError with
    a message naming exactly what's missing. Kept separate from
    classify_sentiment() so a caller/test can also fail fast on
    configuration without needing to pass in any text."""
    if not GROQ_API_KEY:
        raise ClassifierNotConfiguredError(
            "GROQ_API_KEY is not set. Sentiment classification calls "
            "the Groq API and cannot run without it. Set "
            "GROQ_API_KEY in the environment (or .env) running this job."
        )
    try:
        import groq
    except ImportError as exc:
        raise ClassifierNotConfiguredError(
            "The `groq` package is not installed. Sentiment "
            "classification needs the Groq Python SDK (`pip install "
            "groq`)."
        ) from exc
    return groq.Groq(api_key=GROQ_API_KEY)


def _call_model(text: str) -> str:
    """Makes the one classification call and returns the raw text of the
    model's reply. Isolated into its own function specifically so tests
    can monkeypatch just this call (see test_classification.py) instead
    of mocking the whole Groq SDK client.

    Raises ClassifierNotConfiguredError (via _get_client(), uncaught here
    - a caller should stop the batch, not retry) or _ApiCallError (the
    SDK call itself failed - a caller should degrade this one item, per
    classify_sentiment()'s handling of it) - never groq.APIError
    directly, so a caller never needs `groq` importable just to
    catch this function's own failures."""
    client = _get_client()
    # groq is guaranteed importable here - _get_client() above either
    # already imported it successfully or raised ClassifierNotConfiguredError,
    # which propagates out of this function uncaught (not the except clause
    # below).
    import groq

    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            max_completion_tokens=MAX_TOKENS,
            # Groq's native JSON mode - a real reliability improvement this
            # switch brings, not scope creep: it directly replaces what
            # _strip_markdown_fence() below was only ever a workaround for.
            # Requires the word "json" to appear somewhere in the prompt,
            # which _SYSTEM_PROMPT's own "Respond with ONLY a single JSON
            # object" already satisfies.
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(text)},
            ],
        )
    except groq.APIError as exc:
        raise _ApiCallError(str(exc)) from exc
    return response.choices[0].message.content or ""


def _strip_markdown_fence(raw: str) -> str:
    """Models asked for "JSON only" occasionally still wrap it in a
    ```json ... ``` fence anyway. Strip one if present; leave the string
    alone otherwise (json.loads() below will raise cleanly on genuinely
    malformed input either way)."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _parse_response(raw: str) -> ClassificationResult:
    """Parses the model's raw text into a ClassificationResult. Never
    raises — a malformed/unparseable response returns a low-confidence
    result with a reasoning string explaining the parse failure, so one
    bad response doesn't take down classify_unclassified_batch()."""
    try:
        data = json.loads(_strip_markdown_fence(raw))
        sentiment = data["sentiment"]
        alert_category = data["alert_category"]
        confidence = float(data["confidence"])
        reasoning = str(data.get("reasoning") or "")

        if sentiment not in VALID_SENTIMENTS:
            raise ValueError(f"unexpected sentiment value {sentiment!r}")
        if alert_category not in VALID_ALERT_CATEGORIES:
            raise ValueError(f"unexpected alert_category value {alert_category!r}")

        confidence = max(0.0, min(1.0, confidence))
        return ClassificationResult(
            sentiment=sentiment,
            confidence=confidence,
            alert_category=alert_category,
            reasoning=reasoning,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return ClassificationResult(
            sentiment="Neutral",
            confidence=0.0,
            alert_category="digest",
            reasoning=(
                f"Could not parse the classifier's response ({exc}); "
                f"defaulting to a low-confidence Neutral/digest result "
                f"rather than crashing. Raw response: {raw[:500]!r}"
            ),
        )


def classify_sentiment(text: str) -> ClassificationResult:
    """Classifies `text` for sentiment AND crisis/digest routing in one
    model call. See the module docstring for why these two decisions
    share a single call.

    Raises ClassifierNotConfiguredError if GROQ_API_KEY isn't set (or
    the `groq` package isn't installed) — a caller should catch this
    specifically and decide how to degrade (e.g. skip the item, alert an
    operator), not let it crash a whole batch job silently.

    Returns a low-confidence ClassificationResult (never raises) if the
    model's response can't be parsed — see _parse_response().

    A blank/whitespace-only `text` is handled without an API call: there
    is nothing to classify, so this returns a low-confidence Neutral/
    digest result immediately rather than spending a request on it.

    A transient API-level failure (rate limit, timeout, a 5xx from the
    provider — anything raised by the SDK call itself, as opposed to a
    response that came back but didn't parse) also degrades gracefully
    to a low-confidence result rather than propagating - reconciled in
    after review surfaced that only the parse-failure path degraded
    gracefully here while a sibling module (topic_tagging.py) degraded
    on API failures too. This project's established rule throughout
    every adapter (see e.g. RetryExhaustedError caught per-item in
    fetch_owned_reviews.py, per-search-term in fetch_news_articles.py)
    is that one bad item must not crash a whole batch run -
    classify_unclassified_batch()'s loop has no per-item try/except of
    its own specifically because this function is the one place that
    guarantee is meant to hold. ClassifierNotConfiguredError (missing
    API key) is the one exception that still propagates uncaught: every
    remaining item in a batch would fail identically, so stopping
    immediately with a clear error is more useful than silently
    degrading N times in a row."""
    if not text or not text.strip():
        return ClassificationResult(
            sentiment="Neutral",
            confidence=0.0,
            alert_category="digest",
            reasoning="No text content to classify.",
        )
    try:
        raw = _call_model(text)
    except _ApiCallError as exc:
        return ClassificationResult(
            sentiment="Neutral",
            confidence=0.0,
            alert_category="digest",
            reasoning=(
                f"Classifier API call failed ({exc}); defaulting to a "
                f"low-confidence Neutral/digest result rather than crashing."
            ),
        )
    return _parse_response(raw)


def classify_and_store(session: Session, mention_id: int) -> Mention:
    """Loads the Mention row, classifies its `text`, and writes
    sentiment/sentiment_confidence/classified_at/alert_category onto the
    row. Returns the (still-flushed-but-not-committed) Mention — same
    "caller commits" convention as repository.assign_mention()/
    resolve_mention().

    This is the single place a review's star-derived `sentiment`
    placeholder (6.2, see module docstring) gets overwritten with the
    real classifier's output — after this call, `classified_at` is no
    longer null and `sentiment` came from the LLM, not from stars.

    Raises ValueError if `mention_id` doesn't exist, matching
    repository.assign_mention()'s existing convention."""
    mention = session.get(Mention, mention_id)
    if mention is None:
        raise ValueError(f"No Mention with id={mention_id!r}")

    result = classify_sentiment(mention.text)
    mention.sentiment = result.sentiment
    mention.sentiment_confidence = result.confidence
    mention.classified_at = datetime.now(timezone.utc)
    mention.alert_category = result.alert_category
    return mention


def classify_unclassified_batch(session: Session, *, limit: int = 50) -> int:
    """Finds Mention rows where classified_at IS NULL and text IS NOT
    NULL (nothing to classify without text), classifies up to `limit` of
    them oldest-ingested-first, and returns the count actually
    classified. This is what a future scheduled job (app/jobs/, out of
    this module's scope) would call — this function only does the
    classification work, not the scheduling/wiring."""
    mention_ids = session.execute(
        select(Mention.id)
        .where(Mention.classified_at.is_(None), Mention.text.isnot(None))
        .order_by(Mention.ingested_at.asc())
        .limit(limit)
    ).scalars().all()

    count = 0
    for mention_id in mention_ids:
        classify_and_store(session, mention_id)
        count += 1
    return count
