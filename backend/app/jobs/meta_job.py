"""app/jobs/meta_job.py — Job wrapper for fetch_meta_mentions.py's three
independent capabilities (checklist 4.4, the Meta/Instagram/Facebook
adapter), wired into the Phase 2/3 ledger + repository plumbing every
other adapter uses (start_run() / record_ingestion()).

Three granularity decisions made here, all deliberate — read all three
before changing any of them:

1. Mention.source: "instagram" for BOTH Instagram capabilities' rows
   (comments AND mentions), "facebook" for the Facebook capability's
   rows. This matches models.py's own listed example source values
   ("... 'instagram', 'facebook', 'tiktok' ...") and the mockup's
   per-platform breakdown (its source chart splits Instagram and
   Facebook as two slices, not three) — a downstream "filter Mentions by
   platform" query wants "instagram" vs "facebook", not a third axis for
   which Graph API edge produced the row.

2. IngestionRun.source (the 2.4 ledger): THREE separate values —
   LEDGER_SOURCE_INSTAGRAM_COMMENTS, LEDGER_SOURCE_INSTAGRAM_MENTIONS,
   LEDGER_SOURCE_FACEBOOK_COMMENTS — one start_run() per capability, not
   one run covering all three. This is the OPPOSITE granularity from
   decision 1, and deliberately so: Mention.source and IngestionRun.source
   are two different free-text fields on two different tables, answering
   two different questions, and nothing requires them to share a
   granularity. get_source_freshness() exists to answer "is THIS source
   current," and per 4.4's own framing, "Instagram comments are current
   but Instagram mentions are 3 days stale because that permission
   lapsed" is exactly the kind of fact a single combined ledger entry
   cannot express — a single IngestionRun.status can only ever be one of
   "success"/"partial"/"access_denied"/"error" for the WHOLE call, so
   collapsing three independently-permissioned capabilities into one run
   would throw away exactly the information 2.4 was built to keep (and
   would let one lapsed permission make two healthy capabilities look
   stale, or vice versa). A capability that comes back "not_configured"
   gets NO ledger row at all — not RunStatus.ERROR (misleadingly implies
   an attempted-and-failed run) and not RunStatus.ACCESS_DENIED
   (misleadingly implies Meta itself denied something); models.py's
   RunStatus enum has no "not configured" value, and this module's
   file-ownership boundary doesn't include models.py, so forcing "not
   configured" into either existing value would misrepresent what
   happened. This mirrors an existing precedent in this codebase:
   fetch_competitor_ratings.py / google_places_job.py skip a
   REPLACE_ME-placeholder competitor entirely (not written as a row)
   since there is no ID to query yet — same "no attempt was made, so
   don't manufacture a result" reasoning. A capability with no ledger row
   reads as "never run" via get_source_freshness() (all fields None), an
   honest, distinct state from both "denied" and "errored."

3. app/jobs/__init__.py's job contract asks every job module for exactly
   one `SOURCE_NAME: str`, because scheduler.py's cadence check
   (`is_due(session, job.SOURCE_NAME)`) is written for one source per
   module. This module deliberately does NOT expose that single
   `SOURCE_NAME` — see SOURCE_NAMES below instead — because decision 2
   above means there genuinely isn't one string that represents "is Meta
   current." Picking one anyway (e.g. an arbitrary "meta") would either
   (a) silently gate all three capabilities' fetches behind ONE of
   their cadences even though they can go stale independently, or (b)
   require a fourth, redundant combined ledger entry this module's own
   reasoning in decision 2 argues against. Concretely, that means this
   module is NOT a drop-in for `JOBS.append(meta_job)` as written —
   scheduler.py's single-SOURCE_NAME cadence model needs a real decision
   about whether to (i) gain multi-source support, (ii) be handed three
   thin wrapper modules instead of one (each with its own SOURCE_NAME,
   each calling into this module's three run_*_capability-shaped
   functions), or (iii) accept one coarser combined cadence check anyway.
   That decision touches scheduler.py and app/jobs/__init__.py, both
   outside this module's file-ownership — left to the "separate
   reconciliation pass" app/jobs/__init__.py's own docstring already
   names, rather than papered over here with a name that would quietly
   misrepresent decision 2. `run(session)` itself is fully usable on its
   own in the meantime (`from app.jobs.meta_job import run`), exactly
   like reddit_job.py before its own reconciliation.

Concretely: run() calls fetch_meta_mentions.py's three fetch_*()
functions directly (no subprocess/JSON-file round-trip) and wraps each
configured capability in its OWN start_run(), so each gets its own row in
ingestion_runs and its own independently-queryable
get_source_freshness() answer.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

# fetch_meta_mentions.py (and http_utils.py) live at backend/, two
# directories above this file - see google_reviews_job.py's matching
# comment for why this defensive sys.path insertion exists (this job may
# be imported however it's ultimately invoked, from an arbitrary cwd).
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.models import RunStatus  # noqa: E402
from app.repository import IngestionRunRecorder, record_ingestion, start_run  # noqa: E402
from fetch_meta_mentions import (  # noqa: E402
    fetch_facebook_comments,
    fetch_instagram_comments,
    fetch_instagram_mentions,
)

# Ledger source names (IngestionRun.source) — see module docstring
# decision 2. Deliberately distinct from the Mention.source values below.
LEDGER_SOURCE_INSTAGRAM_COMMENTS = "instagram_comments"
LEDGER_SOURCE_INSTAGRAM_MENTIONS = "instagram_mentions"
LEDGER_SOURCE_FACEBOOK_COMMENTS = "facebook_comments"

# Provided for discoverability/introspection only — see module docstring
# decision 3 for why this is NOT the app/jobs/__init__.py contract's
# singular `SOURCE_NAME: str` and this module is not yet in app.jobs.JOBS.
SOURCE_NAMES = (
    LEDGER_SOURCE_INSTAGRAM_COMMENTS,
    LEDGER_SOURCE_INSTAGRAM_MENTIONS,
    LEDGER_SOURCE_FACEBOOK_COMMENTS,
)

# Mention.source values — see module docstring decision 1. Both Instagram
# capabilities write rows with the same Mention.source; only the ledger
# (above) distinguishes which Graph API edge produced a given row.
MENTION_SOURCE_INSTAGRAM = "instagram"
MENTION_SOURCE_FACEBOOK = "facebook"


def _external_id(item: dict) -> str:
    """Builds the (source, external_id) key upsert_mention()'s uniqueness
    constraint needs. Prefers the Graph API's own comment_id (a permanent,
    unique Meta object ID, present for both comment-based capabilities);
    falls back to the tagged media's media_id for fetch_instagram_mentions(),
    whose raw payload has no comment_id — a tag is a property of the whole
    media object, not a distinct comment. The "media:" prefix keeps that
    fallback from ever colliding with a raw comment_id string from the
    other Instagram capability, since both share Mention.source="instagram"
    and therefore the same (source, external_id) namespace."""
    raw = item.get("raw") or {}
    if raw.get("comment_id"):
        return str(raw["comment_id"])
    if raw.get("media_id"):
        return f"media:{raw['media_id']}"
    # Last-resort fallback — fetch_meta_mentions.py's normalizer always
    # sets one of the above today; this exists so a future change to that
    # shape fails soft (a less-ideal dedup key) rather than with a
    # KeyError deep inside record_ingestion().
    return f"{item.get('sourceUrl')}:{item.get('date')}"


def _parse_published_at(value: str | None) -> datetime | None:
    """Meta's Graph API timestamps are ISO-8601 with a numeric offset and
    no colon (e.g. "2026-06-02T00:00:00+0000"), unlike GNews's "Z"-suffixed
    UTC (see news_job.py's equivalent helper) or Reddit's already-clean
    isoformat() output (see reddit_job.py's). Insert the missing colon —
    and normalize a literal "Z" the same way news_job.py does, in case a
    caller ever hands this one — so datetime.fromisoformat() parses it
    uniformly. Returns None on anything unparseable rather than raising —
    one malformed timestamp must not abort the whole run."""
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    if normalized[-5:-4] in ("+", "-") and normalized[-4:].isdigit():
        normalized = normalized[:-2] + ":" + normalized[-2:]
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _record_items(
    session: Session, recorder: IngestionRunRecorder, items: list[dict], *, mention_source: str
) -> None:
    for item in items:
        # items_ingested counts "successfully upserted", not "newly
        # inserted" - matches news_job.py/google_reviews_job.py/
        # reddit_job.py, which all increment it unconditionally after a
        # successful record_ingestion() call rather than conditioning it
        # on the (inserted: bool) return value. A re-ingest of an
        # already-seen comment on a later run is a normal, successful
        # upsert (last-write-wins per upsert_mention()'s own semantics),
        # not a partial failure - conditioning on `inserted` here would
        # make every capability's second-and-later run look PARTIAL
        # forever, which start_run()'s own items_seen/items_ingested
        # inference would then misreport.
        record_ingestion(
            session,
            source=mention_source,
            kind="mention",
            external_id=_external_id(item),
            published_at=_parse_published_at(item.get("date")),
            author=item.get("author"),
            text=item.get("text"),
            url=item.get("sourceUrl"),
            sentiment=item.get("sentiment"),
            venue=item.get("venue"),
            raw_payload=item.get("raw"),
        )
        recorder.items_seen += 1
        recorder.items_ingested += 1


def _run_capability(
    session: Session,
    *,
    ledger_source: str,
    mention_source: str,
    fetch_fn: Callable[[], dict],
) -> None:
    """Runs one capability's fetch_fn() under its own start_run(). See the
    module docstring's decision 2 for why "not_configured" gets no ledger
    row at all, while "ok"/"access_denied"/"error" each get one."""
    result = fetch_fn()
    if result["status"] == "not_configured":
        return

    with start_run(session, source=ledger_source) as recorder:
        if result["status"] == "access_denied":
            recorder.mark(RunStatus.ACCESS_DENIED, error=result["error"])
            return
        if result["status"] == "error":
            recorder.mark(RunStatus.ERROR, error=result["error"])
            return
        # status == "ok" — record every item; start_run()'s own clean-exit
        # logic decides success vs. partial from items_seen/items_ingested,
        # so there's nothing to mark explicitly here.
        _record_items(session, recorder, result["items"], mention_source=mention_source)


def _access_token() -> str | None:
    # No token at all means none of the three capabilities can be
    # attempted, regardless of their own env vars. fetch_meta_mentions.py's
    # main() raises SystemExit in this situation for a human-run script,
    # but a scheduled job must not crash the whole scheduler over one
    # source's missing credential — so every run_*() below skips the same
    # way an individual capability skips on its own missing env var: no
    # attempt, no ledger row (see module docstring decision 2).
    return os.getenv("META_ACCESS_TOKEN")


def run_instagram_comments(session: Session) -> None:
    """One capability, callable on its own cadence — see
    app/jobs/meta_instagram_comments_job.py, the thin per-capability
    wrapper reconciliation decision (ii) in the module docstring settled
    on."""
    access_token = _access_token()
    if not access_token:
        return
    _run_capability(
        session,
        ledger_source=LEDGER_SOURCE_INSTAGRAM_COMMENTS,
        mention_source=MENTION_SOURCE_INSTAGRAM,
        fetch_fn=lambda: fetch_instagram_comments(access_token, os.getenv("META_IG_BUSINESS_ACCOUNT_ID")),
    )


def run_instagram_mentions(session: Session) -> None:
    """See run_instagram_comments()'s docstring — same reasoning, the
    other Instagram capability."""
    access_token = _access_token()
    if not access_token:
        return
    _run_capability(
        session,
        ledger_source=LEDGER_SOURCE_INSTAGRAM_MENTIONS,
        mention_source=MENTION_SOURCE_INSTAGRAM,
        fetch_fn=lambda: fetch_instagram_mentions(access_token, os.getenv("META_IG_BUSINESS_ACCOUNT_ID")),
    )


def run_facebook_comments(session: Session) -> None:
    """See run_instagram_comments()'s docstring — same reasoning, the
    Facebook capability."""
    access_token = _access_token()
    if not access_token:
        return
    _run_capability(
        session,
        ledger_source=LEDGER_SOURCE_FACEBOOK_COMMENTS,
        mention_source=MENTION_SOURCE_FACEBOOK,
        fetch_fn=lambda: fetch_facebook_comments(access_token, os.getenv("META_PAGE_ID")),
    )


def run(session: Session) -> None:
    """Runs all three Meta capabilities, each independently configured,
    fetched, and ledgered (see module docstring). A capability failing —
    or simply not being configured yet — never blocks either of the
    other two from running. Not registered in app.jobs.JOBS directly
    (that needs one SOURCE_NAME per cadence check — see the three thin
    per-capability wrapper modules instead, each importing one of
    run_instagram_comments/run_instagram_mentions/run_facebook_comments
    above); this combined entry point remains directly callable for
    anything that genuinely wants "run all of Meta right now" regardless
    of per-capability cadence."""
    run_instagram_comments(session)
    run_instagram_mentions(session)
    run_facebook_comments(session)
