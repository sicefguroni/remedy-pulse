"""app/jobs/meta_instagram_mentions_job.py — the app.jobs.JOBS-registrable
wrapper for meta_job.py's Instagram-mentions capability. See
meta_instagram_comments_job.py's module docstring for why this thin
per-capability wrapper exists instead of registering meta_job directly.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.jobs.meta_job import LEDGER_SOURCE_INSTAGRAM_MENTIONS, run_instagram_mentions

SOURCE_NAME = LEDGER_SOURCE_INSTAGRAM_MENTIONS


def run(session: Session) -> None:
    run_instagram_mentions(session)
