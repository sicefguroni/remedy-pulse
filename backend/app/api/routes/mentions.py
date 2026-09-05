"""app/api/routes/mentions.py — GET /api/mentions, POST .../assign,
POST .../resolve.

Pagination: keyset on Mention.id, descending (most recently ingested
first) - see repository.list_mentions_filtered()'s docstring. `cursor` is
the smallest id already returned to the caller; passing it back as the
next request's `cursor` asks for strictly smaller ids. `nextCursor` in
the response is a string (per the contract's "string or null") even
though the underlying value is an id.

assign/resolve call repository.assign_mention()/resolve_mention()
directly (Phase 3) rather than reimplementing that logic here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import ApiError, get_current_user, get_db
from app.api.serializers import mention_to_dict
from app.models import User
from app.repository import assign_mention, list_mentions_filtered, resolve_mention

router = APIRouter(tags=["mentions"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@router.get("/mentions")
def list_mentions(
    keyword: str | None = None,
    platform: str = "all",
    sentiment: str = "all",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items, next_cursor = list_mentions_filtered(
        db,
        keyword=keyword,
        platform=platform,
        sentiment=sentiment,
        from_dt=_parse_dt(from_),
        to_dt=_parse_dt(to),
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [mention_to_dict(m) for m in items],
        "nextCursor": str(next_cursor) if next_cursor is not None else None,
    }


class AssignRequest(BaseModel):
    assignee: str


@router.post("/mentions/{mention_id}/assign")
def assign(
    mention_id: int,
    body: AssignRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        mention = assign_mention(db, mention_id, body.assignee, actor=user.email)
    except ValueError:
        raise ApiError(404, {"error": "not found"})
    db.flush()
    return mention_to_dict(mention)


@router.post("/mentions/{mention_id}/resolve")
def resolve(
    mention_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        mention = resolve_mention(db, mention_id, actor=user.email)
    except ValueError:
        raise ApiError(404, {"error": "not found"})
    db.flush()
    return mention_to_dict(mention)
