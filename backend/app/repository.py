"""repository.py — idempotent upsert (2.5) and the ingestion ledger (2.4).

Three independent responsibilities live here:

1. upsert_mention() / upsert_mentions() — write a normalized item keyed
   on (source, external_id). Polling adapters (Phase 4) re-fetch the same
   items on every run; this is what stops that from inflating mention
   counts, share of voice, and the Clarity Index with duplicates every
   time a job re-runs.

2. IngestionRunRecorder + get_source_freshness() — the ledger a Phase 4
   job reports into, and the query the eventual "last synced" UI (P0-11)
   reads from. See models.IngestionRun's docstring for why freshness is
   derived from the run log rather than stored as a mutable field.

3. Event logging and the Phase 3 instrumentation built on it —
   log_event() and its convenience wrappers (record_ingestion,
   assign_mention, resolve_mention, log_export, log_login), plus the
   metric queries (get_median_time_to_assignment, get_export_activity)
   and the 3.3 baseline helpers (record_baseline_response_time,
   get_baseline_summary). See models.Event's docstring for why this is a
   separate log rather than more columns on Mention.
"""

from __future__ import annotations

import statistics
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventType,
    IngestionRun,
    Mention,
    ResponseTimeBaseline,
    RunStatus,
    Sentiment,
)


def _upsert_insert(session: Session, values: dict[str, Any]):
    """Build the right dialect's ON CONFLICT upsert statement. Postgres is
    the only target this is meant to run against in production (see
    docs/decisions/persistence-choice.md); SQLite support here exists so
    the exact same repository code is what the test suite exercises,
    instead of tests covering a different code path than production
    does."""
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        insert_fn = pg_insert
    elif dialect == "sqlite":
        insert_fn = sqlite_insert
    else:
        raise NotImplementedError(
            f"upsert_mention() only implements Postgres and SQLite upsert "
            f"syntax; got dialect={dialect!r}. Add that dialect's ON "
            f"CONFLICT/ON DUPLICATE KEY syntax here before using it, "
            f"rather than silently falling through to a plain INSERT "
            f"that would violate the uniqueness constraint on a re-run."
        )

    stmt = insert_fn(Mention).values(**values)
    update_cols = {
        col: getattr(stmt.excluded, col)
        for col in values
        if col not in ("source", "external_id")
    }
    # updated_at always advances to "now" on a re-ingest, regardless of
    # whether the caller passed it, so "this row was touched by the most
    # recent run" is always answerable.
    update_cols["updated_at"] = func.now()
    return stmt.on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_=update_cols,
    )


def upsert_mention(session: Session, **fields: Any) -> bool:
    """Insert a Mention, or update it in place if (source, external_id)
    already exists. Required: source, kind, external_id. Everything else
    is optional and will overwrite the existing row's value on a re-fetch
    (last-write-wins per field — the adapter's latest fetch is treated as
    the current truth, which is correct for polling: a review whose text
    or reply status changed should reflect the new state, not the first
    one ever seen).

    Returns True if this call inserted a new row, False if it updated an
    existing one. This is determined with an explicit SELECT for
    (source, external_id) BEFORE the upsert statement runs, rather than a
    timestamp-equality heuristic or a dialect-specific `RETURNING xmax`
    trick — this project favors clarity over that kind of cleverness (see
    the 4.6 checklist item: "say so in the code, or someone will
    over-build it"), and the extra round trip is negligible at this
    project's stated ingestion volume (same-day/next-day freshness, not
    real-time — see the PRD)."""
    if not fields.get("source") or not fields.get("external_id"):
        raise ValueError("upsert_mention() requires both source and external_id")
    existing_id = session.execute(
        select(Mention.id).where(
            Mention.source == fields["source"],
            Mention.external_id == fields["external_id"],
        )
    ).scalar_one_or_none()
    session.execute(_upsert_insert(session, fields))
    return existing_id is None


def upsert_mentions(session: Session, items: list[dict[str, Any]]) -> int:
    """Upsert a batch of items in one call. Returns the count attempted
    (Postgres/SQLite ON CONFLICT DO UPDATE doesn't distinguish "inserted"
    from "updated" without a RETURNING trip this doesn't need) — an
    adapter that wants an inserted-vs-updated split should compare
    items_seen to items_ingested logged in the ledger via a targeted
    query instead."""
    for item in items:
        upsert_mention(session, **item)
    return len(items)


@dataclass
class IngestionRunRecorder:
    """Context-manager-shaped helper around one IngestionRun row.

    Usage:
        with start_run(session, source="google_reviews") as run:
            for item in fetch_stuff():
                upsert_mention(session, **item)
                run.items_seen += 1
                run.items_ingested += 1
        # run.status is "success" on clean exit, "error" (with the
        # exception message recorded) if the block raised — either way
        # finished_at is set exactly once, here, not scattered across the
        # caller's own try/except.
    """

    run: IngestionRun
    items_seen: int = 0
    items_ingested: int = 0

    def mark(self, status: RunStatus, *, error: str | None = None) -> None:
        self.run.status = status
        self.run.error = error


@contextmanager
def start_run(session: Session, *, source: str) -> Iterator[IngestionRunRecorder]:
    run = IngestionRun(source=source, status=RunStatus.RUNNING)
    session.add(run)
    session.flush()  # populate run.id / started_at before the caller sees it
    recorder = IngestionRunRecorder(run=run)
    try:
        yield recorder
        if recorder.run.status == RunStatus.RUNNING:
            # Caller didn't explicitly call recorder.mark(...) — a clean
            # exit with items_ingested < items_seen is "partial" (some
            # items failed silently upstream), otherwise "success". This
            # default exists so a straightforward adapter never has to
            # remember to mark success itself, only the failure paths
            # that actually need a distinct status.
            if recorder.items_seen and recorder.items_ingested < recorder.items_seen:
                recorder.mark(RunStatus.PARTIAL)
            else:
                recorder.mark(RunStatus.SUCCESS)
    except Exception as exc:
        recorder.mark(RunStatus.ERROR, error=str(exc))
        raise
    finally:
        recorder.run.items_seen = recorder.items_seen
        recorder.run.items_ingested = recorder.items_ingested
        recorder.run.finished_at = datetime.now(timezone.utc)


@dataclass
class SourceFreshness:
    source: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_status: str | None
    last_error: str | None


def get_source_freshness(session: Session, source: str) -> SourceFreshness:
    """Derives last_attempt_at/last_success_at/status from the run log,
    per models.IngestionRun's docstring — never stored as a mutable
    field, always computed from what actually happened."""
    latest = session.execute(
        select(IngestionRun)
        .where(IngestionRun.source == source)
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_success = session.execute(
        select(func.max(IngestionRun.finished_at)).where(
            IngestionRun.source == source,
            IngestionRun.status == RunStatus.SUCCESS,
        )
    ).scalar_one_or_none()

    return SourceFreshness(
        source=source,
        last_attempt_at=latest.started_at if latest else None,
        last_success_at=last_success,
        last_status=latest.status if latest else None,
        last_error=latest.error if latest else None,
    )


# --- Phase 3 instrumentation: event log, assignment/resolution, metrics ---


def log_event(
    session: Session,
    event_type: EventType,
    *,
    mention_id: int | None = None,
    actor: str | None = None,
    metadata: dict | None = None,
) -> Event:
    """The generic event logger every function below builds on. Accepts a
    `metadata` kwarg (the natural name for a caller) and stores it into the
    `metadata_json` column — see models.Event's docstring for why the
    column itself isn't named `metadata`."""
    event = Event(event_type=event_type, mention_id=mention_id, actor=actor, metadata_json=metadata)
    session.add(event)
    session.flush()  # populate event.id / occurred_at before the caller sees it
    return event


def record_ingestion(session: Session, **fields: Any) -> bool:
    """What a Phase 4 adapter should call instead of upsert_mention()
    directly, so ITEM_INGESTED instrumentation isn't optional per-caller
    bookkeeping. Logs the event only when upsert_mention() reports a new
    insert (never on a re-ingest of the same (source, external_id)).
    Returns the same bool upsert_mention() returns."""
    inserted = upsert_mention(session, **fields)
    if inserted:
        mention_id = session.execute(
            select(Mention.id).where(
                Mention.source == fields["source"],
                Mention.external_id == fields["external_id"],
            )
        ).scalar_one()
        log_event(session, EventType.ITEM_INGESTED, mention_id=mention_id)
    return inserted


def assign_mention(session: Session, mention_id: int, assignee: str, *, actor: str | None = None) -> Mention:
    """Sets assigned_to = assignee unconditionally — reassignment is
    allowed and should update who currently owns the item — but sets
    assigned_at = now() ONLY if it isn't already set. This is a real,
    non-obvious semantic choice: 3.2's metric is "time to first take
    ownership," not time-of-most-recent-reassignment, so a second, third,
    or Nth assignment must never move assigned_at once it's set.

    Every call logs an ITEM_ASSIGNED event (metadata={"assignee": ...})
    regardless of whether assigned_at actually changed — every assignment
    action is worth an audit-trail entry even on reassignment.

    Raises ValueError if mention_id doesn't exist."""
    mention = session.get(Mention, mention_id)
    if mention is None:
        raise ValueError(f"No Mention with id={mention_id!r}")
    mention.assigned_to = assignee
    if mention.assigned_at is None:
        mention.assigned_at = datetime.now(timezone.utc)
    log_event(
        session, EventType.ITEM_ASSIGNED, mention_id=mention_id, actor=actor, metadata={"assignee": assignee}
    )
    return mention


def resolve_mention(session: Session, mention_id: int, *, actor: str | None = None) -> Mention:
    """Sets resolved_at = now(). Every call updates it to the latest
    resolution time — there's no "unresolve" concept yet, so this stays
    simple rather than guarding against re-resolution. Logs an
    ITEM_RESOLVED event. Raises ValueError if mention_id doesn't exist."""
    mention = session.get(Mention, mention_id)
    if mention is None:
        raise ValueError(f"No Mention with id={mention_id!r}")
    mention.resolved_at = datetime.now(timezone.utc)
    log_event(session, EventType.ITEM_RESOLVED, mention_id=mention_id, actor=actor)
    return mention


def log_export(session: Session, export_type: str, *, actor: str | None = None, item_count: int | None = None) -> Event:
    """Logs EXPORT_DOWNLOADED (3.4). export_type is a plain string (e.g.
    "mentions_csv", "reviews_csv", "emv_csv", matching the mockup's three
    existing CSV exports) rather than an enum — the UI's export list isn't
    this layer's business to constrain."""
    return log_event(
        session,
        EventType.EXPORT_DOWNLOADED,
        actor=actor,
        metadata={"export_type": export_type, "item_count": item_count},
    )


def log_login(session: Session, *, actor: str) -> Event:
    """Logs a LOGIN event. Nothing calls this yet — there is no
    authentication system to call it from (Phase 5.5 builds one). Same
    schema-readiness pattern as Mention.deleted_at: the capability exists
    so Phase 5.5 doesn't also need a migration."""
    return log_event(session, EventType.LOGIN, actor=actor)


def get_median_time_to_assignment(session: Session, *, since: datetime | None = None) -> float | None:
    """The actual 3.2 metric: median hours from a negative mention's
    ingested_at to its assigned_at, across Mention rows where
    sentiment == NEGATIVE and assigned_at IS NOT NULL (optionally scoped
    to ingested_at >= since).

    The median is computed in Python (statistics.median), not a SQL
    percentile_cont, deliberately: this keeps the exact same query logic
    correct on both SQLite (tests) and Postgres (production) rather than
    a dialect branch for a metric this project's own stated scale doesn't
    need optimized SQL-side.

    Returns None (not 0) when there are no qualifying rows — a metric
    with no data yet is a distinct fact from a metric that computed to
    zero."""
    conditions = [Mention.sentiment == Sentiment.NEGATIVE, Mention.assigned_at.isnot(None)]
    if since is not None:
        conditions.append(Mention.ingested_at >= since)
    rows = session.execute(select(Mention.ingested_at, Mention.assigned_at).where(*conditions)).all()
    if not rows:
        return None
    deltas_hours = [(assigned_at - ingested_at).total_seconds() / 3600 for ingested_at, assigned_at in rows]
    return statistics.median(deltas_hours)


def get_export_activity(session: Session, *, since: datetime) -> int:
    """Count of EXPORT_DOWNLOADED events with occurred_at >= since, to
    support checking the "at least one CSV export per week" target (3.4)
    from a monitoring/reporting script."""
    return session.execute(
        select(func.count()).select_from(Event).where(
            Event.event_type == EventType.EXPORT_DOWNLOADED,
            Event.occurred_at >= since,
        )
    ).scalar_one()


def record_baseline_response_time(
    session: Session,
    *,
    source_description: str,
    response_time_hours: float | None,
    captured_by: str,
    notes: str | None = None,
) -> ResponseTimeBaseline:
    """A straightforward insert for the 3.3 manual pre-launch baseline.
    See models.ResponseTimeBaseline's docstring — this does not, and
    cannot, fabricate the numbers itself.

    response_time_hours=None records a review with no reply as of the
    capture date — a real outcome, not a value to omit or guess at (see
    docs/response-time-baseline-template.md). Put the capture date and
    any detail in `notes` when passing None."""
    baseline = ResponseTimeBaseline(
        source_description=source_description,
        response_time_hours=response_time_hours,
        captured_by=captured_by,
        notes=notes,
    )
    session.add(baseline)
    session.flush()
    return baseline


def get_baseline_summary(session: Session) -> dict:
    """{"count": total rows captured (including no-reply rows),
        "no_reply_count": rows recorded with response_time_hours=None,
        "median_hours": float | None, "mean_hours": float | None}
    over ResponseTimeBaseline rows. median_hours/mean_hours are computed
    only over rows that HAD a reply (response_time_hours is not None) —
    a no-reply row has no time-to-reply to average in, but it must not be
    silently dropped from the summary either (that would hide exactly the
    optimistic bias docs/response-time-baseline-template.md warns about),
    which is why no_reply_count is reported alongside, not omitted.
    median_hours/mean_hours are None (not 0) when there are zero rows WITH
    a reply — same reasoning as get_median_time_to_assignment()."""
    all_values = session.execute(select(ResponseTimeBaseline.response_time_hours)).scalars().all()
    with_reply = [v for v in all_values if v is not None]
    return {
        "count": len(all_values),
        "no_reply_count": len(all_values) - len(with_reply),
        "median_hours": statistics.median(with_reply) if with_reply else None,
        "mean_hours": statistics.mean(with_reply) if with_reply else None,
    }
