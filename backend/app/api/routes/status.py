"""app/api/routes/status.py — GET /api/status.

The REAL fix for 4.7/0.2: app/status_report.py's own module docstring
calls itself an explicit STOPGAP "pending Phase 7's API / data-driven
refactor" existing, because marketing doesn't run CLI scripts and read
their stdout. This endpoint is that dependency landing - it supersedes
status_report.py for anything that can call an HTTP API (i.e. the actual
dashboard UI). The script itself remains valid for a quick manual/CLI
freshness check (someone with shell access and no browser handy) and is
not removed here - both are legitimate ways to read the same
get_source_freshness() data now.

One entry per app.jobs.JOBS registry member, exactly as the contract
specifies.

`isDataSource` (checklist 0.24, docs/decisions/14-last-synced-excludes-
non-data-sources.md): every source still gets a row here (a failed
classification or Reddit-deletion-check run must stay visible, e.g. to
app/jobs/__init__.py's job contract note and the mockup's per-source
failure banner) - this field only tells a consumer which ones represent
external data freshness, for computing something like GET /api/overview's
lastSyncedAt itself does. Not a filter on `sources`, just a label on each
entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.serializers import enum_value, iso
from app.jobs import JOBS
from app.models import User
from app.repository import get_source_freshness

router = APIRouter(tags=["status"])


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sources = []
    for job in JOBS:
        freshness = get_source_freshness(db, job.SOURCE_NAME)
        sources.append(
            {
                "source": freshness.source,
                "lastAttemptAt": iso(freshness.last_attempt_at),
                "lastSuccessAt": iso(freshness.last_success_at),
                "lastStatus": enum_value(freshness.last_status),
                "lastError": freshness.last_error,
                "isDataSource": getattr(job, "IS_DATA_SOURCE", True),
            }
        )
    return {"sources": sources}
