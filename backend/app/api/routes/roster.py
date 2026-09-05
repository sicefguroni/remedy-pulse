"""app/api/routes/roster.py — GET /api/roster.

Backed by app.models.User rows (5.5) - see
docs/decisions/10-assignment-roster.md for why this replaces the mockup's
hardcoded Gian/Paul/Boom/Mixi list. Only active users are listed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import User

router = APIRouter(tags=["roster"])


@router.get("/roster")
def roster(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).scalars().all()
    return {"assignees": [{"id": u.id, "email": u.email, "displayName": u.display_name} for u in rows]}
