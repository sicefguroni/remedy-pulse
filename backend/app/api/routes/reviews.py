"""app/api/routes/reviews.py — GET /api/reviews, POST /api/reviews/{id}/reply,
POST /api/reviews/by-venue/{venue}/reply.

repository.get_reviews_listings() aggregates purely from Mention rows
that exist (kind="review", source="google_reviews", grouped by venue) -
see that function's docstring for why. This route is what layers
config.OWNED_LISTINGS (the four configured branch names, backend/config.py
- read-only reference, not modified) on top, so a branch with zero
ingested reviews yet still gets a status="no_reviews" row instead of
silently not appearing, matching the contract's "one row per owned
branch."

Checklist 7.4/8.5 — closing the mockup wiring gap `sendReply()`'s own
comment names: the UI's reply flow operates on a whole branch listing
(`pendingReplies`, a count), not one specific review id, and there was no
endpoint shaped for that until now. `POST /reviews/by-venue/{venue}/reply`
is that endpoint — see its own docstring for why it resolves "the next
pending review" server-side rather than requiring the UI to first list
individual reviews to pick one, and docs/decisions/13-review-reply-flow.md
for why NEITHER endpoint posts real reply text to Google (both only mark
`has_reply` in this system's own copy - a human posts the actual reply via
the deep-link Business Profile takes them to, exactly as before).
"""

from __future__ import annotations

import os
import sys

# backend/config.py lives two levels above backend/app/api/routes/ (routes
# -> api -> app -> backend). Mirrors the identical defensive sys.path
# handling app/jobs/google_reviews_job.py already does for the same
# reason: don't assume the caller's cwd already has backend/ on
# sys.path (e.g. `uvicorn app.api.main:app` launched from an arbitrary
# directory).
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from fastapi import APIRouter, Depends  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import ApiError, get_current_user, get_db  # noqa: E402
from app.models import Mention, MentionKind, User  # noqa: E402
from app.repository import ReviewListing, get_reviews_listings  # noqa: E402
from config import OWNED_LISTING_ALIASES, OWNED_LISTINGS  # noqa: E402

router = APIRouter(tags=["reviews"])


def _business_profile_url(venue: str) -> str | None:
    """The per-venue deep link the reply modal sends a human to (8.5's
    ratified decision), or None if config.py's placeholder hasn't been
    replaced with a real one yet - see OWNED_LISTINGS's own comment for
    why this isn't guessed. `all_listings()` also calls this for a venue
    with zero ingested reviews yet, so it must tolerate a venue not in
    OWNED_LISTINGS at all (a competitor-only or not-yet-configured name)
    rather than raising."""
    return OWNED_LISTINGS.get(venue, {}).get("business_profile_url")


def _listing_to_dict(listing: ReviewListing) -> dict:
    return {
        "venue": listing.venue,
        "rating": listing.rating,
        "reviewCount": listing.review_count,
        "pendingReplies": listing.pending_replies,
        "responseRatePct": listing.response_rate_pct,
        "status": listing.status,
        # Checklist 8.8 — the pre-refactor mockup's per-branch "Also
        # matches: ..." tooltip (config.OWNED_LISTING_ALIASES, recovered
        # from git history; see backend/config.py's own comment). Display
        # data only, uniform shape (empty list, not an omitted key, for
        # the two branches with none documented).
        "aliases": OWNED_LISTING_ALIASES.get(listing.venue, []),
        # Checklist 8.5 — see _business_profile_url()'s docstring.
        "businessProfileUrl": _business_profile_url(listing.venue),
    }


def all_listings(db: Session) -> list[dict]:
    """One dict per docs/api-contract.md's Reviews `listings` item shape:
    every venue with real Mention data, plus a status="no_reviews"
    placeholder for any configured branch (config.OWNED_LISTINGS) that
    doesn't have any yet. Sorted by venue name for a stable order."""
    by_venue = {listing.venue: _listing_to_dict(listing) for listing in get_reviews_listings(db)}
    for venue in OWNED_LISTINGS:
        by_venue.setdefault(
            venue,
            {
                "venue": venue,
                "rating": None,
                "reviewCount": 0,
                "pendingReplies": 0,
                "responseRatePct": 0,
                "status": "no_reviews",
                "aliases": OWNED_LISTING_ALIASES.get(venue, []),
                "businessProfileUrl": _business_profile_url(venue),
            },
        )
    return [by_venue[venue] for venue in sorted(by_venue)]


@router.get("/reviews")
def list_reviews(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"listings": all_listings(db)}


@router.post("/reviews/{mention_id}/reply")
def reply_to_review(
    mention_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    mention = db.get(Mention, mention_id)
    if mention is None or mention.kind != MentionKind.REVIEW:
        raise ApiError(404, {"error": "not found"})
    mention.has_reply = True
    db.flush()
    listings = all_listings(db)
    updated = next((listing for listing in listings if listing["venue"] == mention.venue), None)
    if updated is None:
        # Defensive only: the review that was just marked has_reply=True
        # belongs to mention.venue, so get_reviews_listings() will always
        # produce an aggregate row for that venue now - this branch exists
        # so a future taxonomy change fails loudly (404) instead of a
        # silent 500/KeyError.
        raise ApiError(404, {"error": "not found"})
    return updated


@router.post("/reviews/by-venue/{venue}/reply")
def reply_to_next_pending_review(
    venue: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Closes checklist 7.4/8.5's documented wiring gap: the mockup's
    reply flow is branch-level (`pendingReplies`, a count), not a specific
    review id, and `POST /reviews/{mention_id}/reply` needs exactly that
    id. This resolves "the oldest not-yet-replied review at this venue"
    server-side instead of requiring the UI to first list individual
    reviews to pick one from - the smaller of the two designs the
    checklist's own gap note named as the alternatives, and consistent
    with the mockup's existing "N pending replies" framing (it already
    doesn't distinguish *which* review is pending, only how many).

    Same semantics as the by-id endpoint otherwise: only marks
    `has_reply = true` in this system's own copy, never posts to Google
    (docs/decisions/13-review-reply-flow.md). 404 if `venue` has no
    pending review right now - a caller should only invoke this when
    `pendingReplies > 0` for that venue, per the current `GET /api/reviews`
    response it is already holding."""
    mention = db.execute(
        select(Mention)
        .where(
            Mention.venue == venue,
            Mention.kind == MentionKind.REVIEW,
            Mention.source == "google_reviews",
            Mention.has_reply.is_not(True),
        )
        # Oldest first: whichever review has been waiting longest gets
        # marked replied first, same "first come, first served" ordering
        # a human working the list top-to-bottom would produce.
        .order_by(Mention.published_at.asc().nulls_last(), Mention.ingested_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if mention is None:
        raise ApiError(404, {"error": "no pending review for this venue"})
    mention.has_reply = True
    db.flush()
    listings = all_listings(db)
    updated = next((listing for listing in listings if listing["venue"] == venue), None)
    if updated is None:
        # Defensive only, mirrors reply_to_review()'s identical branch:
        # the review just marked belongs to `venue`, so an aggregate row
        # for it must exist now.
        raise ApiError(404, {"error": "not found"})
    return updated
