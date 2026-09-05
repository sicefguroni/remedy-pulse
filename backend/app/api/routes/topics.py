"""app/api/routes/topics.py — GET /api/topics, GET /api/topics/{key}/mentions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import ApiError, get_current_user, get_db
from app.api.serializers import mention_to_dict
from app.models import User
from app.repository import FIXED_TOPICS, TopicSummary, get_topics_summary, list_mentions_filtered

router = APIRouter(tags=["topics"])

_VALID_TOPIC_KEYS = {key for key, _label in FIXED_TOPICS}


def _topic_to_dict(t: TopicSummary) -> dict:
    return {
        "key": t.key,
        "label": t.label,
        "mentionCountThisWeek": t.mention_count_this_week,
        "sentimentSplit": {
            "positivePct": t.positive_pct,
            "neutralPct": t.neutral_pct,
            "negativePct": t.negative_pct,
        },
        "sampleQuote": t.sample_quote,
        "tag": t.tag,
    }


@router.get("/topics")
def list_topics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"topics": [_topic_to_dict(t) for t in get_topics_summary(db)]}


@router.get("/topics/{key}/mentions")
def topic_mentions(
    key: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if key not in _VALID_TOPIC_KEYS:
        raise ApiError(404, {"error": "not found"})
    # kind=None: reviews and articles carry topic tags too, not just
    # Mentions-tab feed rows - see list_mentions_filtered()'s docstring.
    items, next_cursor = list_mentions_filtered(db, topic=key, kind=None, limit=limit, cursor=cursor)
    return {
        "items": [mention_to_dict(m) for m in items],
        "nextCursor": str(next_cursor) if next_cursor is not None else None,
    }
