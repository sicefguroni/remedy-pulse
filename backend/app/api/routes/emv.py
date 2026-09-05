"""app/api/routes/emv.py — GET /api/emv.

grossEmv/netEmv are null on every article, per docs/api-contract.md's
own explicit, deliberate rule (the pricing formula needs editorial-
judgment inputs - prominence, PubScore, PR_Credibility - no connector or
classifier here can supply; see fetch_news_articles.py's docstring).

DEVIATION from the contract's example JSON (not from its prose rule):
that example also shows concrete grossTotal/netTotal numbers
(2366000/2809000), copied from the mockup's static demo data. Since
those totals are nothing but a sum of grossEmv/netEmv across the
returned articles, and this endpoint returns every article's
grossEmv/netEmv as null, honestly summing null values cannot produce a
real number - grossTotal/netTotal are therefore also null here, not a
guessed or copied-from-the-mockup total. Reported in this phase's final
report as an explicit, intentional deviation, not an oversight.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.repository import EmvArticle, get_emv_articles

router = APIRouter(tags=["emv"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _article_to_dict(a: EmvArticle) -> dict:
    return {
        "id": a.id,
        "outlet": a.outlet,
        "headline": a.headline,
        "tier": a.tier,
        "sentiment": a.sentiment,
        "grossEmv": None,
        "netEmv": None,
        "url": a.url,
        "publishedAt": a.published_at.isoformat() if a.published_at else None,
    }


@router.get("/emv")
def emv(
    outlet: str = "all",
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    articles = get_emv_articles(db, outlet=outlet, from_dt=_parse_dt(from_), to_dt=_parse_dt(to))
    filtered = (outlet != "all") or bool(from_) or bool(to)
    return {
        "grossTotal": None,
        "netTotal": None,
        "filtered": filtered,
        "articles": [_article_to_dict(a) for a in articles],
    }
