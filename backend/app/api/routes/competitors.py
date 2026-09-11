"""app/api/routes/competitors.py — GET /api/competitors.

See repository.get_competitors_data()'s docstring for the brand-grouping
interpretation this route relies on (the contract doesn't spell it out
further).

`aliases` on each `shareOfVoice` entry (checklist 8.8) is layered on here,
at the route, rather than in repository.get_competitors_data() itself -
same reasoning as app/api/routes/reviews.py's OWNED_LISTINGS layering:
config.BRAND_ALIASES is a read-only reference file, not database state,
so it has no business inside the DB-aggregation function. This is
DISPLAY-ONLY - the "Also matches: ..." tooltip the pre-refactor mockup
had (see backend/config.py's own module comment for where this data
came from) - and is deliberately NOT the same thing as alias-based
TEXT MATCHING during ingestion (that gap is documented in
get_competitors_data()'s own docstring and is out of this item's scope).
"""

from __future__ import annotations

import os
import sys

# backend/config.py lives two levels above backend/app/api/routes/ (routes
# -> api -> app -> backend) - identical defensive sys.path handling to
# app/api/routes/reviews.py, for the same reason (don't assume the
# caller's cwd already has backend/ on sys.path).
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from fastapi import APIRouter, Depends  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import get_current_user, get_db  # noqa: E402
from app.models import User  # noqa: E402
from app.repository import get_competitors_data  # noqa: E402
from config import BRAND_ALIASES  # noqa: E402

router = APIRouter(tags=["competitors"])


def _with_aliases(entry: dict) -> dict:
    """Add an `aliases` list (possibly empty) to one shareOfVoice entry.
    "Remedy" (isOwn) and any competitor whose formal name is a
    BRAND_ALIASES key get their documented variant names; every other
    entry gets an empty list rather than an omitted key, so the field's
    shape is uniform for every row regardless of whether aliases are
    known for it yet."""
    return {**entry, "aliases": BRAND_ALIASES.get(entry["name"], [])}


@router.get("/competitors")
def competitors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = get_competitors_data(db)
    return {
        "shareOfVoice": [_with_aliases(entry) for entry in data.share_of_voice],
        "sourceBreakdown": data.source_breakdown,
        "competitorSentiment": data.competitor_sentiment,
    }
