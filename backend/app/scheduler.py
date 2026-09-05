"""scheduler.py — per-source cadence runner (4.6).

The PRD scopes v1 at same-day/next-day freshness, not real-time. So this
is deliberately simple: NOT a distributed task queue, NOT Celery, NOT a
scheduling engine embedded in the app - just "does this source's cadence
say it's due? if so, run it" per source in app.jobs.JOBS, wrapped so it
can be invoked either way this project actually needs it invoked:

  - As a single "run once" pass: `python -m app.scheduler`. Meant to be
    called by cron / Windows Task Scheduler on whatever external cadence
    is convenient (e.g. hourly) - get_source_freshness() is what makes
    over-calling this harmless, since a source whose cadence hasn't
    elapsed yet is silently skipped, not re-run.
  - As a standing process: `python -m app.scheduler --loop`, which checks
    every job's cadence on a plain `time.sleep` loop instead of exiting.

Do not build anything past this for v1 - a per-source interval check plus
one of these two invocation styles is what "same-day/next-day, not
real-time" calls for, and no more.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db import session_scope
from app.jobs import JOBS
from app.repository import get_source_freshness

# Hours a source must wait since its last attempt before it's due again.
# 12h is the default for every registered source - generous enough for
# same-day/next-day freshness without a real reason to poll more often.
# Override a specific source here only once a real constraint shows up
# (e.g. a stricter upstream rate limit), not preemptively.
DEFAULT_CADENCE_HOURS = 12.0
CADENCE_HOURS: dict[str, float] = {}


def _cadence_for(source: str) -> float:
    return CADENCE_HOURS.get(source, DEFAULT_CADENCE_HOURS)


def _as_aware_utc(dt: datetime) -> datetime:
    """Mention/IngestionRun's timestamp columns are declared
    DateTime(timezone=True), but SQLite (the test backend - see
    docs/decisions/05-persistence-choice.md) has no native tz-aware storage
    and silently hands back a naive datetime, while Postgres (production)
    correctly round-trips the UTC offset. Every timestamp this project
    writes is UTC regardless of dialect, so a naive value read back is
    treated as UTC rather than left to fail an aware/naive comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def is_due(session: Session, source: str, *, now: datetime | None = None) -> bool:
    """A source with no prior run is always due. Otherwise it's due once
    `_cadence_for(source)` hours have elapsed since last_attempt_at -
    deliberately keyed on the last ATTEMPT, not the last SUCCESS, so a
    source that's failing repeatedly (e.g. access denied) is retried on
    its normal cadence rather than hammered every time this runs."""
    now = _as_aware_utc(now) if now is not None else datetime.now(timezone.utc)
    freshness = get_source_freshness(session, source)
    if freshness.last_attempt_at is None:
        return True
    last_attempt_at = _as_aware_utc(freshness.last_attempt_at)
    next_due_at = last_attempt_at + timedelta(hours=_cadence_for(source))
    return now >= next_due_at


def run_due_jobs(session: Session, *, now: datetime | None = None, jobs=None) -> list[str]:
    """Runs job.run(session) for every job in `jobs` (defaults to the
    app.jobs.JOBS registry) whose cadence has elapsed. Returns the
    SOURCE_NAMEs actually run this pass - a source that isn't due yet is
    silently skipped, not an error. `jobs` is overridable so tests don't
    have to monkeypatch the module-level registry."""
    now = now or datetime.now(timezone.utc)
    jobs = JOBS if jobs is None else jobs
    ran: list[str] = []
    for job in jobs:
        if is_due(session, job.SOURCE_NAME, now=now):
            job.run(session)
            session.commit()
            ran.append(job.SOURCE_NAME)
    return ran


def run_forever(*, poll_interval_seconds: float = 3600.0) -> None:
    """The standing-process mode - see module docstring. A plain sleep
    loop, not a scheduling library; that's the point."""
    while True:
        with session_scope() as session:
            run_due_jobs(session)
        time.sleep(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run forever, re-checking cadence every --interval seconds "
        "(default: run one pass and exit).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3600.0,
        help="Seconds between cadence checks in --loop mode (default: 3600).",
    )
    args = parser.parse_args()

    if args.loop:
        run_forever(poll_interval_seconds=args.interval)
        return

    with session_scope() as session:
        ran = run_due_jobs(session)
    print(f"Ran: {', '.join(ran) if ran else '(nothing due)'}")


if __name__ == "__main__":
    main()
