"""app/api/routes/exports.py — POST /api/exports/{type}.

Builds a real CSV (Python's stdlib `csv` module - no new dependency) from
the same filtered query the corresponding GET endpoint would run, and
logs it via repository.log_export() (3.4), per docs/api-contract.md.

DEVIATION (documented): the mentions export accepts the same filter
params GET /api/mentions does (keyword/platform/sentiment/from/to) but
deliberately ignores `limit`/`cursor` - an export's entire point is the
complete filtered set, not one page of it, so pagination params from the
read endpoint don't carry over here. "Complete" is bounded at
_EXPORT_ROW_LIMIT rows as a sanity ceiling, not because pagination is
meaningful for a CSV download.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.routes.reviews import all_listings
from app.models import User
from app.repository import get_emv_articles, list_mentions_filtered, log_export

router = APIRouter(tags=["exports"])

ExportType = Literal["mentions_csv", "reviews_csv", "emv_csv"]

# A sanity ceiling on how many rows a single CSV export fetches - this
# project's stated scale (same-day/next-day ingestion, a few hundred rows
# total per the PRD) never gets close to it; it exists so an export
# request can't accidentally become an unbounded query.
_EXPORT_ROW_LIMIT = 100_000


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _csv_response(fieldnames: list[str], rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _mentions_rows(db: Session, *, keyword, platform, sentiment, from_dt, to_dt) -> list[dict]:
    items, _next_cursor = list_mentions_filtered(
        db,
        keyword=keyword,
        platform=platform,
        sentiment=sentiment,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=_EXPORT_ROW_LIMIT,
    )
    rows = []
    for m in items:
        rows.append(
            {
                "id": m.id,
                "platform": m.source,
                "author": m.author or "",
                "text": m.text or "",
                "url": m.url or "",
                "publishedAt": m.published_at.isoformat() if m.published_at else "",
                "sentiment": getattr(m.sentiment, "value", m.sentiment) or "",
                "topics": ";".join(m.topics or []),
                "venue": m.venue or "",
                "assignedTo": m.assigned_to or "",
                "assignedAt": m.assigned_at.isoformat() if m.assigned_at else "",
                "resolvedAt": m.resolved_at.isoformat() if m.resolved_at else "",
                "alertCategory": m.alert_category or "",
            }
        )
    return rows


def _emv_rows(db: Session, *, outlet, from_dt, to_dt) -> list[dict]:
    articles = get_emv_articles(db, outlet=outlet, from_dt=from_dt, to_dt=to_dt)
    return [
        {
            "id": a.id,
            "outlet": a.outlet or "",
            "headline": a.headline or "",
            "tier": a.tier or "",
            "sentiment": a.sentiment or "",
            "grossEmv": "",  # always null/blank - see module docstring & emv.py
            "netEmv": "",
            "url": a.url or "",
            "publishedAt": a.published_at.isoformat() if a.published_at else "",
        }
        for a in articles
    ]


@router.post("/exports/{export_type}")
def export(
    export_type: ExportType,
    keyword: str | None = None,
    platform: str = "all",
    sentiment: str = "all",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    outlet: str = "all",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from_dt = _parse_dt(from_)
    to_dt = _parse_dt(to)

    if export_type == "mentions_csv":
        rows = _mentions_rows(db, keyword=keyword, platform=platform, sentiment=sentiment, from_dt=from_dt, to_dt=to_dt)
        fieldnames = [
            "id", "platform", "author", "text", "url", "publishedAt", "sentiment",
            "topics", "venue", "assignedTo", "assignedAt", "resolvedAt", "alertCategory",
        ]
        response = _csv_response(fieldnames, rows, "mentions.csv")
    elif export_type == "reviews_csv":
        rows = all_listings(db)
        fieldnames = ["venue", "rating", "reviewCount", "pendingReplies", "responseRatePct", "status"]
        response = _csv_response(fieldnames, rows, "reviews.csv")
    else:  # emv_csv
        rows = _emv_rows(db, outlet=outlet, from_dt=from_dt, to_dt=to_dt)
        fieldnames = ["id", "outlet", "headline", "tier", "sentiment", "grossEmv", "netEmv", "url", "publishedAt"]
        response = _csv_response(fieldnames, rows, "emv.csv")

    log_export(db, export_type, actor=user.email, item_count=len(rows))
    db.flush()
    return response
