"""app/api/routes/auth.py — POST /api/auth/login.

The only endpoint exempt from the Authorization: Bearer <token>
requirement (docs/api-contract.md's own wording). Calls
app.auth.authenticate(), which never lets a caller distinguish "unknown
email" from "wrong password" via its return value or a timing side
channel (see that function's docstring) - this route preserves that by
returning the identical 401 body for every failure reason.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import ApiError, get_db
from app.auth import authenticate, create_session_token

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


def _token_expiry(token: str) -> datetime:
    """Decodes the expiry embedded in a token this same request just
    created via app.auth.create_session_token(), rather than
    independently recomputing "now + TTL" (which could drift by up to a
    second or two from what the token itself actually encodes) or
    modifying app.auth to expose the expiry directly (auth.py is outside
    this batch's file ownership). Only decodes the unsigned payload half
    of the token - nothing to verify here that create_session_token()
    didn't already guarantee, since this process just signed it."""
    encoded_payload = token.split(".", 1)[0]
    payload = base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
    _, expires_at_epoch = payload.split(":", 1)
    return datetime.fromtimestamp(int(expires_at_epoch), tz=timezone.utc)


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, email=body.email, password=body.password)
    if user is None:
        raise ApiError(401, {"error": "invalid credentials"})
    token = create_session_token(user.id)
    return {
        "token": token,
        "expires_at": _token_expiry(token).isoformat(),
        "user": {"id": user.id, "email": user.email, "display_name": user.display_name},
    }
