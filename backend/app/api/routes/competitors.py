"""app/api/routes/competitors.py — GET /api/competitors.

See repository.get_competitors_data()'s docstring for the brand-grouping
interpretation this route relies on (the contract doesn't spell it out
further).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.repository import get_competitors_data

router = APIRouter(tags=["competitors"])


@router.get("/competitors")
def competitors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = get_competitors_data(db)
    return {
        "shareOfVoice": data.share_of_voice,
        "sourceBreakdown": data.source_breakdown,
        "competitorSentiment": data.competitor_sentiment,
    }
