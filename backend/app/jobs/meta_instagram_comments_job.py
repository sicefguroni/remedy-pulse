"""app/jobs/meta_instagram_comments_job.py — the app.jobs.JOBS-registrable
wrapper for meta_job.py's Instagram-comments capability.

meta_job.py deliberately does not expose a single SOURCE_NAME (see its
module docstring, decision 3): its three Meta capabilities can go stale
independently, so one combined cadence check would misrepresent that.
This module — and its two siblings, meta_instagram_mentions_job.py and
meta_facebook_comments_job.py — is the reconciliation that docstring
names option (ii) for: one thin SOURCE_NAME-bearing wrapper per
capability, each delegating to meta_job's already-built per-capability
logic, so scheduler.py's/status_report.py's single-SOURCE_NAME-per-job
contract is satisfied without meta_job.py collapsing its three
independently-meaningful ledger entries into one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.jobs.meta_job import LEDGER_SOURCE_INSTAGRAM_COMMENTS, run_instagram_comments

SOURCE_NAME = LEDGER_SOURCE_INSTAGRAM_COMMENTS


def run(session: Session) -> None:
    run_instagram_comments(session)
