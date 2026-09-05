"""app/api/routes/reviews.py — GET /api/reviews, POST /api/reviews/{id}/reply.

repository.get_reviews_listings() aggregates purely from Mention rows
that exist (kind="review", source="google_reviews", grouped by venue) -
see that function's docstring for why. This route is what layers
config.OWNED_LISTINGS (the four configured branch names, backend/config.py
- read-only reference, not modified) on top, so a branch with zero
ingested reviews yet still gets a status="no_reviews" row instead of
silently not appearing, matching the contract's "one row per owned
branch."
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
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import ApiError, get_current_user, get_db  # noqa: E402
from app.models import Mention, MentionKind, User  # noqa: E402
from app.repository import ReviewListing, get_reviews_listings  # noqa: E402
from config import OWNED_LISTINGS  # noqa: E402

router = APIRouter(tags=["reviews"])


def _listing_to_dict(listing: ReviewListing) -> dict:
    return {
        "venue": listing.venue,
        "rating": listing.rating,
        "reviewCount": listing.review_count,
        "pendingReplies": listing.pending_replies,
        "responseRatePct": listing.response_rate_pct,
        "status": listing.status,
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
