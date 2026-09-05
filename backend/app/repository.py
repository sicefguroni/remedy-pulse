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
from datetime import datetime, timedelta, timezone
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
    MentionKind,
    ResponseTimeBaseline,
    RunStatus,
    Sentiment,
)
from config import BACKFILL_WINDOW_DAYS


def _upsert_insert(session: Session, values: dict[str, Any]):
    """Build the right dialect's ON CONFLICT upsert statement. Postgres is
    the only target this is meant to run against in production (see
    docs/decisions/05-persistence-choice.md); SQLite support here exists so
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
    if not fields.get("kind"):
        # Mention.kind is nullable=False at the DB level (models.Mention) -
        # without this check, omitting it fails as an opaque DB constraint
        # violation on the upsert below instead of a clear Python error
        # naming the actual problem.
        raise ValueError("upsert_mention() requires kind (e.g. 'review', 'mention', 'article')")
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


# --- Phase 7 API read queries ---
#
# Everything below is new, appended for the app/api/ layer (7.1/7.4) per
# that batch's file-ownership note ("append new READ-ONLY query functions
# ... append only, do not modify any existing function in this file").
# Nothing above this line was changed except the two import lines (adding
# `timedelta` and `MentionKind`, both additive).
#
# These functions do the read-side aggregation docs/api-contract.md's
# routes need; the route handlers themselves (app/api/routes/*.py) stay
# thin wrappers that call these and shape the result into the contract's
# exact JSON, rather than embedding SQLAlchemy queries inline.


# Mention.source values actually written by the registered adapters
# (app.jobs.JOBS) that count as "Google" for the Overview endpoint's
# `source=google` filter. Kept as an explicit set (not "startswith
# google_") because "google_places_competitor" rows are still Google's
# data, just about a competitor, and belong in this bucket too.
GOOGLE_SOURCES = {"google_reviews", "google_places_competitor"}


def _pct(count: int, total: int) -> int:
    """round(100 * count / total), or 0 when total is 0 - a percentage of
    nothing is 0, not a ZeroDivisionError, and not fabricated as some
    other sentinel."""
    return round(100 * count / total) if total else 0


def _sentiment_value(value: Any) -> Any:
    """Mention.sentiment is a plain String(16) column (see models.Mention),
    not a SQLAlchemy Enum type, so a freshly-loaded row's value is
    ordinarily already a plain str - but a still-identity-mapped row from
    earlier in the same session can hold the Sentiment enum member it was
    assigned with. Normalizes either shape to the plain "Positive"/
    "Neutral"/"Negative" string the API contract uses, mirroring
    status_report.py's identical normalization for RunStatus."""
    return getattr(value, "value", value)


def _apply_source_category(conditions: list, source_category: str | None) -> list:
    """Mutates and returns `conditions` in place - a helper, not a public
    query itself. `source_category` matches the Overview endpoint's
    `source` query param: "all" (default, no filter), "google", "news", or
    "social". "social" is deliberately a catch-all (everything that is
    neither Google nor a "news_"-prefixed source) rather than an
    enumerated list of social platforms, so a newly registered adapter
    source counts as "social" automatically - the same "register once in
    app.jobs.JOBS, nothing else needs editing" ethos that module's own
    docstring describes, applied here to source categorization instead of
    job registration."""
    if not source_category or source_category == "all":
        return conditions
    if source_category == "google":
        conditions.append(Mention.source.in_(GOOGLE_SOURCES))
    elif source_category == "news":
        conditions.append(Mention.source.like("news\\_%", escape="\\"))
    elif source_category == "social":
        conditions.append(Mention.source.notin_(GOOGLE_SOURCES))
        conditions.append(~Mention.source.like("news\\_%", escape="\\"))
    return conditions


def _apply_entity(conditions: list, entity: str | None) -> list:
    """Mutates and returns `conditions` in place. `entity` matches the
    Overview endpoint's `entity` query param: "all" (default, no filter)
    or a specific owned listing/venue name, matched against Mention.venue
    exactly (venue names are the same strings config.OWNED_LISTINGS and
    the ingested Mention rows already use, e.g.
    "Remedy — BGC (One Uptown Residence)")."""
    if entity and entity != "all":
        conditions.append(Mention.venue == entity)
    return conditions


@dataclass
class MentionPage:
    items: list[Mention]
    next_cursor: int | None


# Upper bound on how many candidate rows a topic-filtered query scans in
# Python (see list_mentions_filtered()'s docstring) before giving up on
# finding more matches for the current page. Generous relative to this
# project's stated scale (same-day/next-day ingestion, per the PRD - not
# a high-volume real-time firehose).
_TOPIC_SCAN_LIMIT = 2000


def list_mentions_filtered(
    session: Session,
    *,
    keyword: str | None = None,
    platform: str | None = None,
    sentiment: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    topic: str | None = None,
    kind: MentionKind | None = MentionKind.MENTION,
    limit: int = 50,
    cursor: int | None = None,
) -> tuple[list[Mention], int | None]:
    """The read-only query backing GET /api/mentions, GET
    /api/topics/{key}/mentions, and POST /api/exports/mentions_csv.

    Keyset-paginated on Mention.id, descending (most recently ingested
    first): `cursor` is the smallest id already handed to the caller: the
    next call passes that same value back as `cursor` to get strictly
    smaller ids. Returns (items, next_cursor); next_cursor is None when
    the current page is the last one.

    `kind` defaults to MentionKind.MENTION (the Mentions-tab feed) since
    that's what GET /api/mentions is about; callers that want every kind
    tagged with a topic (GET /api/topics/{key}/mentions - reviews and
    articles carry topic tags too, not just feed mentions) pass
    kind=None.

    `topic` membership is checked in Python over up to _TOPIC_SCAN_LIMIT
    candidate rows fetched via every other filter + the cursor, ordered by
    id descending - not a SQL JSON-containment predicate. Mention.topics
    is a plain JSON column (see models.Mention's docstring on why), and
    SQLAlchemy has no single containment operator that's portable across
    SQLite (tests) and Postgres (production) for that column type; a real
    dialect-branched JSONB containment query would be the next step if
    this project's data volume ever outgrows _TOPIC_SCAN_LIMIT."""
    conditions: list = []
    if kind is not None:
        conditions.append(Mention.kind == kind)
    if keyword:
        conditions.append(Mention.text.ilike(f"%{keyword}%"))
    if platform and platform != "all":
        conditions.append(Mention.source == platform)
    if sentiment and sentiment != "all":
        conditions.append(Mention.sentiment == sentiment)
    if from_dt is not None:
        conditions.append(Mention.published_at >= from_dt)
    if to_dt is not None:
        conditions.append(Mention.published_at <= to_dt)
    if cursor is not None:
        conditions.append(Mention.id < cursor)

    stmt = select(Mention).where(*conditions).order_by(Mention.id.desc())

    if topic:
        candidates = session.execute(stmt.limit(_TOPIC_SCAN_LIMIT)).scalars().all()
        matched = [m for m in candidates if m.topics and topic in m.topics]
        has_more = len(matched) > limit
        page = matched[:limit]
    else:
        rows = session.execute(stmt.limit(limit + 1)).scalars().all()
        has_more = len(rows) > limit
        page = rows[:limit]

    next_cursor = page[-1].id if (has_more and page) else None
    return list(page), next_cursor


@dataclass
class OverviewStats:
    total_mentions_now: int
    total_mentions_prior: int
    positive_count: int
    neutral_count: int
    negative_count: int
    avg_google_rating: float
    google_review_count: int
    avg_response_rate_pct: float
    active_alerts_total: int
    active_alerts_crisis: int
    active_alerts_digest: int


def get_overview_stats(
    session: Session,
    *,
    period_from: datetime,
    period_to: datetime,
    prior_from: datetime,
    prior_to: datetime,
    source_category: str = "all",
    entity: str = "all",
) -> OverviewStats:
    """The read-only aggregation backing GET /api/overview. `period_from`/
    `period_to` bound the "current" window (published_at range,
    inclusive); `prior_from`/`prior_to` bound the immediately preceding
    window of the same length, used for totalMentions.priorPeriodValue
    and the Clarity Index's volume-trend term. `source_category`/`entity`
    match the endpoint's own `source`/`entity` query params - see
    _apply_source_category()/_apply_entity().

    avg_google_rating/google_review_count come from kind="review",
    source="google_reviews" rows only (Google is the only rated channel),
    scoped to `entity` (a specific venue) but NOT `source_category` - the
    rating figure is inherently Google-only regardless of what the
    `source` filter says, matching the Clarity Index formula's own
    "Avg. Google Rating" input.

    active_alerts_* counts kind=mention Mention rows with alert_category
    set and resolved_at IS NULL (still open) within the current period/
    filters - "active" meaning "routed and not yet resolved," not merely
    "ever routed." Restricted to kind=mention so this count always
    matches what the one alerts-list UI surface (GET /api/mentions, which
    defaults to kind=mention) can actually show and resolve - see the
    kind filter's own comment below for why."""
    base_conditions: list = []
    _apply_source_category(base_conditions, source_category)
    _apply_entity(base_conditions, entity)

    now_conditions = base_conditions + [
        Mention.published_at >= period_from,
        Mention.published_at <= period_to,
    ]
    prior_conditions = base_conditions + [
        Mention.published_at >= prior_from,
        Mention.published_at < period_from,
    ]

    total_now = session.execute(
        select(func.count()).select_from(Mention).where(*now_conditions)
    ).scalar_one()
    total_prior = session.execute(
        select(func.count()).select_from(Mention).where(*prior_conditions)
    ).scalar_one()

    sentiment_rows = session.execute(
        select(Mention.sentiment, func.count())
        .where(*now_conditions, Mention.sentiment.isnot(None))
        .group_by(Mention.sentiment)
    ).all()
    sentiment_counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for sentiment_value, count in sentiment_rows:
        sentiment_counts[_sentiment_value(sentiment_value)] = count

    google_conditions: list = [
        Mention.kind == MentionKind.REVIEW,
        Mention.source == "google_reviews",
        Mention.published_at >= period_from,
        Mention.published_at <= period_to,
        Mention.rating.isnot(None),
    ]
    _apply_entity(google_conditions, entity)
    avg_rating, review_count = session.execute(
        select(func.avg(Mention.rating), func.count(Mention.rating)).where(*google_conditions)
    ).one()

    listings = get_reviews_listings(session)
    response_rates = [listing.response_rate_pct for listing in listings if listing.review_count > 0]
    avg_response_rate = sum(response_rates) / len(response_rates) if response_rates else 0.0

    # Restricted to kind=mention (8.4 fix): classify_and_store() (app/classification.py) sets
    # alert_category on ANY classified row with text, reviews included - but the one and only
    # UI surface that lists/resolves alerts (GET /api/mentions, and therefore the mockup's alerts
    # panel derived from it) defaults to kind=mention and can never show a kind=review row. Left
    # unrestricted, a classified-negative review would silently inflate this KPI forever with no
    # way to see or resolve it - reviews already have their own attention mechanism (the Reviews
    # tab's pending-reply indicator, see docs/decisions/13-review-reply-flow.md), so this count
    # matches exactly what's actually visible/actionable rather than diverging from it.
    alert_conditions = now_conditions + [
        Mention.kind == MentionKind.MENTION,
        Mention.alert_category.isnot(None),
        Mention.resolved_at.is_(None),
    ]
    alert_rows = session.execute(
        select(Mention.alert_category, func.count()).where(*alert_conditions).group_by(Mention.alert_category)
    ).all()
    alert_counts: dict[str, int] = {"crisis": 0, "digest": 0}
    for category, count in alert_rows:
        alert_counts[category] = alert_counts.get(category, 0) + count

    return OverviewStats(
        total_mentions_now=total_now,
        total_mentions_prior=total_prior,
        positive_count=sentiment_counts["Positive"],
        neutral_count=sentiment_counts["Neutral"],
        negative_count=sentiment_counts["Negative"],
        avg_google_rating=round(avg_rating, 1) if avg_rating is not None else 0.0,
        google_review_count=review_count or 0,
        avg_response_rate_pct=avg_response_rate,
        active_alerts_total=sum(alert_counts.values()),
        active_alerts_crisis=alert_counts.get("crisis", 0),
        active_alerts_digest=alert_counts.get("digest", 0),
    )


@dataclass
class ReviewListing:
    venue: str
    rating: float | None
    review_count: int
    pending_replies: int
    response_rate_pct: int
    status: str


def get_reviews_listings(session: Session) -> list[ReviewListing]:
    """The read-only aggregation backing GET /api/reviews, POST
    /api/reviews/{id}/reply's response, and POST /api/exports/reviews_csv:
    one row per venue actually present among kind="review",
    source="google_reviews" Mention rows, grouped by venue - per
    docs/api-contract.md's own wording ("aggregated from Mention rows
    where kind=review and source=google_reviews, grouped by venue").

    pendingReplies/responseRatePct are computed from real per-row
    has_reply values (never a single branch-wide flag) - the contract's
    own explicit callout not to reintroduce the mockup's old "one reply
    clears a whole branch" bug.

    A venue with zero ingested reviews never appears here at all (there's
    no row to group), so this function never returns status="no_reviews"
    - the caller (app/api/routes/reviews.py) is what cross-references
    config.OWNED_LISTINGS to add a "no_reviews" placeholder row for a
    configured branch that has no data yet, since that needs the branch
    roster this function deliberately doesn't depend on (repository.py
    has no existing reason to import backend/config.py, and "aggregated
    from Mention rows ... grouped by venue" is exactly what this function
    does - see this phase's final report for the full reasoning)."""
    rows = session.execute(
        select(Mention.venue, Mention.rating, Mention.has_reply).where(
            Mention.kind == MentionKind.REVIEW, Mention.source == "google_reviews"
        )
    ).all()

    freshness = get_source_freshness(session, "google_reviews")
    run_status = _sentiment_value(freshness.last_status)  # same enum-or-str normalization, any column

    by_venue: dict[str, list[tuple[int | None, bool | None]]] = {}
    for venue, rating, has_reply in rows:
        by_venue.setdefault(venue or "(unspecified)", []).append((rating, has_reply))

    listings = []
    for venue, items in sorted(by_venue.items()):
        count = len(items)
        ratings = [r for r, _ in items if r is not None]
        replied = sum(1 for _, hr in items if hr)
        avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None
        status = "ok" if run_status not in ("access_denied", "error") else run_status
        listings.append(
            ReviewListing(
                venue=venue,
                rating=avg_rating,
                review_count=count,
                pending_replies=count - replied,
                response_rate_pct=_pct(replied, count),
                status=status,
            )
        )
    return listings


# The five fixed topic keys/labels this project ships with today (6.5's
# taxonomy), matching remedy-pulse-mockup.html's existing topicMentions
# object exactly - see docs/api-contract.md's Topics section.
FIXED_TOPICS: list[tuple[str, str]] = [
    ("facial-results", "Facial Results & Glow"),
    ("staff-service", "Staff & Service Experience"),
    ("rejuran", "Rejuran Specifically"),
    ("pricing", "Pricing & Packages"),
    ("booking", "Booking & Follow-up Response"),
]


@dataclass
class TopicSummary:
    key: str
    label: str
    mention_count_this_week: int
    positive_pct: int
    neutral_pct: int
    negative_pct: int
    sample_quote: str | None
    tag: str | None


def get_topics_summary(session: Session, *, now: datetime | None = None) -> list[TopicSummary]:
    """The read-only aggregation backing GET /api/topics: one row per
    FIXED_TOPICS entry. mentionCountThisWeek and sentimentSplit are both
    scoped to the trailing 7 days ending at `now` (default: real now) -
    the field is literally named "this week," and scoping sentimentSplit
    to the same window keeps one card internally consistent rather than
    silently mixing a this-week count with an all-time sentiment mix.

    `tag` ("needs-attention" | "watch" | null) is this implementation's
    own threshold on negativePct (>=30 -> needs-attention, >=15 -> watch,
    else null) - docs/api-contract.md names the two values but no
    threshold; docs/decisions/11-topic-tagging-approach.md (6.5, built in
    parallel) may define a real one, in which case this should be
    revisited. See this phase's final report for that caveat."""
    now = now or datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    this_week_rows = session.execute(
        select(Mention.topics, Mention.sentiment, Mention.text)
        .where(Mention.topics.isnot(None), Mention.published_at >= week_start, Mention.published_at <= now)
        .order_by(Mention.id.desc())
    ).all()
    this_week = [(topics, _sentiment_value(sentiment), text) for topics, sentiment, text in this_week_rows]

    # Fallback pool for sampleQuote when a topic had no activity this
    # week: the most recent _TOPIC_SCAN_LIMIT all-time tagged rows (same
    # scan-limit reasoning as list_mentions_filtered()'s topic filter -
    # Mention.topics has no portable SQL containment check). Fetched once,
    # outside the per-topic loop below, rather than once per topic.
    fallback_rows = session.execute(
        select(Mention.topics, Mention.text)
        .where(Mention.topics.isnot(None), Mention.text.isnot(None))
        .order_by(Mention.id.desc())
        .limit(_TOPIC_SCAN_LIMIT)
    ).all()

    summaries = []
    for key, label in FIXED_TOPICS:
        tagged = [(sentiment, text) for topics, sentiment, text in this_week if topics and key in topics]
        count = len(tagged)
        sentiments = [s for s, _ in tagged if s is not None]
        total_with_sentiment = len(sentiments)
        positive_pct = _pct(sentiments.count("Positive"), total_with_sentiment)
        neutral_pct = _pct(sentiments.count("Neutral"), total_with_sentiment)
        negative_pct = _pct(sentiments.count("Negative"), total_with_sentiment)
        sample_quote = next((text for _, text in tagged if text), None)
        if sample_quote is None:
            sample_quote = next(
                (text for topics, text in fallback_rows if topics and key in topics), None
            )
        if negative_pct >= 30:
            tag = "needs-attention"
        elif negative_pct >= 15:
            tag = "watch"
        else:
            tag = None
        summaries.append(
            TopicSummary(
                key=key,
                label=label,
                mention_count_this_week=count,
                positive_pct=positive_pct,
                neutral_pct=neutral_pct,
                negative_pct=negative_pct,
                sample_quote=sample_quote,
                tag=tag,
            )
        )
    return summaries


@dataclass
class CompetitorsData:
    share_of_voice: list[dict]
    source_breakdown: list[dict]
    competitor_sentiment: list[dict]


def get_competitors_data(session: Session) -> CompetitorsData:
    """The read-only aggregation backing GET /api/competitors, over the
    full Mention table (this endpoint has no query params per
    docs/api-contract.md).

    Brand grouping (this implementation's own interpretation - the
    contract doesn't spell this out further): "Remedy" is every Mention
    row NOT from source="google_places_competitor" (i.e. every owned-
    source row: google_reviews, reddit, instagram, facebook, news_gnews -
    all channels tracking Remedy itself); each competitor is the
    google_places_competitor rows for one venue (the competitor's name,
    per fetch_competitor_ratings.py's own normalization - see 4.2).
    shareOfVoice/competitorSentiment use this same grouping so the two
    cards are internally consistent with each other.

    8.8's alias matching (config.BRAND_ALIASES, recovered from the
    mockup's pre-refactor tooltips) is NOT applied inside this query, and
    that is a real, deliberate scope boundary, not an oversight: alias
    matching means "does this mention's TEXT contain one of these brand
    variant strings," which only makes sense for a source that was
    keyword-searched for brand mentions in the first place. Today, that
    is exactly two sources - fetch_news_articles.py and
    fetch_reddit_mentions.py, both of which now search
    config.NEWS_SEARCH_TERMS / config.REDDIT_SEARCH_TERMS (already
    extended with the recovered Remedy aliases) - so alias matching is
    already doing its job at ingestion time for those two, by shaping
    which items get ingested as Remedy mentions to begin with. It is NOT
    yet applied the other direction - searching for COMPETITOR aliases so
    a stray "just tried Aivee Skin Spa" mention gets correctly attributed
    to Aivee's share of voice rather than going uningested entirely -
    because no adapter currently keyword-searches for competitor names at
    all (competitor data today is Google Places RATINGS only, matched by
    place_id, never by text). Building that would mean extending the
    news/Reddit adapters to also search config.BRAND_ALIASES's competitor
    entries and tagging the resulting Mention rows with which brand they
    matched - a real adapter-behavior change, not a query-layer fix, and
    out of this pass's scope. Flagged here so the gap is visible at the
    point someone would next touch this function, not just in a checklist
    entry."""
    total = session.execute(select(func.count()).select_from(Mention)).scalar_one()
    remedy_condition = Mention.source != "google_places_competitor"
    remedy_count = session.execute(
        select(func.count()).select_from(Mention).where(remedy_condition)
    ).scalar_one()
    competitor_rows = session.execute(
        select(Mention.venue, func.count())
        .where(Mention.source == "google_places_competitor")
        .group_by(Mention.venue)
    ).all()

    share_of_voice = [{"name": "Remedy", "pct": _pct(remedy_count, total), "isOwn": True}]
    for venue, count in competitor_rows:
        share_of_voice.append({"name": venue or "(unknown)", "pct": _pct(count, total), "isOwn": False})

    source_rows = session.execute(select(Mention.source, func.count()).group_by(Mention.source)).all()
    source_breakdown = [{"platform": source, "pct": _pct(count, total)} for source, count in source_rows]

    def _sentiment_mix(*conditions) -> dict:
        rows = session.execute(
            select(Mention.sentiment, func.count())
            .where(*conditions, Mention.sentiment.isnot(None))
            .group_by(Mention.sentiment)
        ).all()
        counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
        for sentiment_value, count in rows:
            counts[_sentiment_value(sentiment_value)] = count
        total_sentiment = sum(counts.values())
        return {
            "positivePct": _pct(counts["Positive"], total_sentiment),
            "neutralPct": _pct(counts["Neutral"], total_sentiment),
            "negativePct": _pct(counts["Negative"], total_sentiment),
        }

    competitor_sentiment = [{"name": "Remedy", "isOwn": True, **_sentiment_mix(remedy_condition)}]
    for venue, _count in competitor_rows:
        competitor_sentiment.append(
            {
                "name": venue or "(unknown)",
                "isOwn": False,
                **_sentiment_mix(Mention.source == "google_places_competitor", Mention.venue == venue),
            }
        )

    return CompetitorsData(
        share_of_voice=share_of_voice,
        source_breakdown=source_breakdown,
        competitor_sentiment=competitor_sentiment,
    )


@dataclass
class EmvArticle:
    id: int
    outlet: str | None
    headline: str | None
    tier: str | None
    sentiment: str | None
    url: str | None
    published_at: datetime | None


def get_emv_articles(
    session: Session,
    *,
    outlet: str = "all",
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[EmvArticle]:
    """The read-only query backing GET /api/emv and POST
    /api/exports/emv_csv: kind="article" Mention rows, most recent first.
    `outlet` matches Mention.venue exactly (the "where within the source"
    field holds the publication name for kind="article" rows - see
    models.Mention's docstring)."""
    conditions: list = [Mention.kind == MentionKind.ARTICLE]
    if outlet and outlet != "all":
        conditions.append(Mention.venue == outlet)
    if from_dt is not None:
        conditions.append(Mention.published_at >= from_dt)
    if to_dt is not None:
        conditions.append(Mention.published_at <= to_dt)
    rows = session.execute(
        select(Mention)
        .where(*conditions)
        .order_by(Mention.published_at.desc().nulls_last(), Mention.id.desc())
    ).scalars().all()
    return [
        EmvArticle(
            id=m.id,
            outlet=m.venue,
            headline=m.headline,
            tier=m.tier,
            sentiment=_sentiment_value(m.sentiment),
            url=m.url,
            published_at=m.published_at,
        )
        for m in rows
    ]


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


def is_within_backfill_window(published_at: datetime | None, *, now: datetime | None = None) -> bool:
    """The v1 backfill policy (9.2, config.BACKFILL_WINDOW_DAYS): True if
    `published_at` is recent enough to ingest, False if it's older than
    the window and should be skipped entirely.

    Lives here, not in app.jobs (the more obviously-named home), to avoid
    a circular import: app/jobs/__init__.py imports every job submodule
    (news_job.py, reddit_job.py, ...), and those same submodules need to
    call this - putting it in app.jobs.__init__ would mean a job module
    importing from a package whose own __init__.py hasn't finished
    importing that job module yet.

    A None `published_at` (some ingestion edge case where a source didn't
    give one) is treated as within-window - excluding an item just
    because its date is unknown would be a stranger policy than ingesting
    it, and get_overview_stats()/get_reviews_listings() etc. already fall
    back to ingested_at for rows with no published_at, so an undated item
    still lands somewhere sensible downstream rather than needing a
    second special case here.

    Deliberately NOT applied to owned Google reviews (google_reviews_job.py
    documents why at its own call site: a branch's Reviews-tab rating is
    its TRUE, all-time Google rating, and filtering it would silently
    understate that for no cost/volume benefit) or to competitor ratings
    (google_places_job.py - a current-state snapshot, not a stream, so
    "how far back" doesn't apply). Applied to the keyword-searched
    discovery sources instead (news_job.py, reddit_job.py, meta_job.py),
    where "how far back do we search" is the actual cost/volume question
    the PRD's Non-Goal is about.

    Every job that DOES apply this should call it per-item and skip (not
    count toward items_seen/items_ingested) anything outside the window,
    BEFORE calling record_ingestion() - not inside record_ingestion()
    itself, because start_run()'s items_seen/items_ingested-based status
    inference would otherwise read an intentional, expected exclusion as
    a PARTIAL failure. An item genuinely outside the window was never a
    candidate for this run in the first place."""
    if published_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at >= now - timedelta(days=BACKFILL_WINDOW_DAYS)


def get_overview_trend(session: Session, *, days: int = 30) -> list[dict]:
    """Backs GET /api/overview/trend (8.1 - closes the Overview tab's
    Sentiment Trend chart, which had no backing endpoint at all when
    Phase 7 shipped). See docs/api-contract.md for the exact response
    shape this returns.

    Bucketed by COALESCE(published_at, ingested_at) date, in Python
    rather than SQL date-truncation, deliberately: this project's
    established pattern (get_median_time_to_assignment(),
    get_baseline_summary()) is to do this kind of aggregation in Python
    when it keeps the logic identical across SQLite (tests) and Postgres
    (production) rather than reaching for a dialect-specific date
    function, and this project's stated volume (a few hundred items a
    week) makes pulling the window's rows into Python genuinely cheap,
    not a real performance concern.

    A row with sentiment IS NULL (never classified) counts toward that
    day's mentionCount but none of the three sentiment buckets - it must
    not be silently forced into "Neutral," which would misrepresent an
    unclassified item as a real judgment call the classifier never
    actually made."""
    days = min(days, 90)  # matches the PRD's own 90-day backfill cap (9.2)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = session.execute(
        select(Mention.published_at, Mention.ingested_at, Mention.sentiment).where(
            func.coalesce(Mention.published_at, Mention.ingested_at) >= since
        )
    ).all()

    buckets: dict[Any, dict[str, int]] = {}
    for published_at, ingested_at, sentiment in rows:
        effective = published_at or ingested_at
        if effective is None:
            continue  # shouldn't happen (ingested_at is NOT NULL), but never crash the chart over one bad row
        day = effective.date()
        bucket = buckets.setdefault(
            day, {"mentionCount": 0, "positiveCount": 0, "neutralCount": 0, "negativeCount": 0}
        )
        bucket["mentionCount"] += 1
        sentiment_str = _sentiment_value(sentiment)
        if sentiment_str == Sentiment.POSITIVE:
            bucket["positiveCount"] += 1
        elif sentiment_str == Sentiment.NEUTRAL:
            bucket["neutralCount"] += 1
        elif sentiment_str == Sentiment.NEGATIVE:
            bucket["negativeCount"] += 1
        # else: sentiment is None (never classified) - counted in
        # mentionCount above, deliberately not forced into any bucket.

    return [
        {"date": day.isoformat(), **counts}
        for day, counts in sorted(buckets.items())
    ]
