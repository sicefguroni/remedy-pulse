"""repository.py — idempotent upsert (2.5) and the ingestion ledger (2.4).

Two independent responsibilities live here:

1. upsert_mention() / upsert_mentions() — write a normalized item keyed
   on (source, external_id). Polling adapters (Phase 4) re-fetch the same
   items on every run; this is what stops that from inflating mention
   counts, share of voice, and the Clarity Index with duplicates every
   time a job re-runs.

2. IngestionRunRecorder + get_source_freshness() — the ledger a Phase 4
   job reports into, and the query the eventual "last synced" UI (P0-11)
   reads from. See models.IngestionRun's docstring for why freshness is
   derived from the run log rather than stored as a mutable field.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import IngestionRun, Mention, RunStatus


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


def upsert_mention(session: Session, **fields: Any) -> None:
    """Insert a Mention, or update it in place if (source, external_id)
    already exists. Required: source, kind, external_id. Everything else
    is optional and will overwrite the existing row's value on a re-fetch
    (last-write-wins per field — the adapter's latest fetch is treated as
    the current truth, which is correct for polling: a review whose text
    or reply status changed should reflect the new state, not the first
    one ever seen)."""
    if not fields.get("source") or not fields.get("external_id"):
        raise ValueError("upsert_mention() requires both source and external_id")
    session.execute(_upsert_insert(session, fields))


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
