"""models.py — the vendor-agnostic mention/review/article schema (2.1).

The roadmap named this the one "Now" item that was never blocked on the
Phase 1 vendor decision, and every adapter (Phase 4) and the eventual
data-driven UI refactor (Phase 7) depend on it existing before they do.

Design notes — read before adding a field:

- ONE table, `mentions`, covers all three of the mockup's shapes: a
  Mentions-tab feed item, a Reviews-tab row, and an EMV-tab press
  article. They're the same underlying fact — "an external source said
  something about Remedy, on some date, with some sentiment" — with a
  handful of fields that only make sense for one `kind`. That sparsity is
  a deliberate trade-off, not an oversight: three separate tables would
  duplicate the source/external_id/published_at/sentiment/ingestion
  columns three times, and Phase 3's metrics query ("median time from
  negative mention appearing to assigned") needs to query across all
  three kinds uniformly. If a `kind` grows enough type-specific fields
  that the sparsity becomes the bigger problem, split it then — this is
  the schema Phase 4/7 will actually be populating, not a museum piece,
  and it's meant to be revised under real usage.
- `(source, external_id)` is UNIQUE. This is what makes 2.5's upsert
  idempotent — polling adapters re-fetch the same items constantly, and
  re-ingesting item X must update row X, never insert a duplicate.
- `raw_payload` keeps the untouched API response. This directly serves
  0.7's Reddit 48-hour deletion-propagation job (not built in this
  phase — that's Phase 5 — but the schema needs to be ready for it now):
  that job re-checks stored Reddit external_ids on a schedule, and needs
  the original payload shape to know what it's re-checking.
- `deleted_at` exists for the same reason: a future purge job marks it
  rather than every future feature needing to know whether hard-delete
  already happened out from under it. No code in this phase sets it —
  it is schema readiness, not a built feature.
- Sentiment is stored as the same three-value string the mockup and the
  Phase 0 connectors already use ("Positive"/"Neutral"/"Negative"), not
  a float score — Phase 6 (sentiment classification) is a separate,
  not-yet-scoped piece of work; this schema doesn't pre-empt whatever
  that phase decides by inventing a scoring scale now.
- `Mention.assigned_at`/`assigned_to`/`resolved_at` (3.2) put an
  ingested-at timestamp and an assigned-at timestamp on the same row, so
  the core PRD metric — median time from a negative mention appearing to
  being assigned, target under 4 business hours — is computable with a
  single-row query (`assigned_at - ingested_at`), not a cross-table join.
- `Event` (3.1) is a separate append-only log, not a set of extra
  timestamp columns bolted onto `Mention`, because most event types
  (`login`, `export_downloaded`) aren't about a mention at all, and even
  the ones that are (`item_assigned`, `item_resolved`) want a full audit
  trail (every reassignment), not just the latest value — which is
  exactly what `Mention`'s own columns above are for instead.
- `ResponseTimeBaseline` (3.3) is schema-only: a place for a one-time
  manual pre-launch measurement to live, not a computed metric. See its
  own docstring.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MentionKind(str, enum.Enum):
    REVIEW = "review"  # Reviews tab — owned or competitor
    MENTION = "mention"  # Mentions tab feed — social/forum/generic
    ARTICLE = "article"  # EMV tab — news/press coverage


class Sentiment(str, enum.Enum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # some items ingested, some failed
    ACCESS_DENIED = "access_denied"  # mirrors the Phase 0 connector status
    ERROR = "error"


class EventType(str, enum.Enum):
    """The five event types the PRD's measurement method names explicitly
    ("from application logs (login events, alert timestamps, resolution
    timestamps)"). Matches the PRD's own wording exactly, not a paraphrase."""

    LOGIN = "login"
    ITEM_INGESTED = "item_ingested"
    ITEM_ASSIGNED = "item_assigned"
    ITEM_RESOLVED = "item_resolved"
    EXPORT_DOWNLOADED = "export_downloaded"


class Mention(Base):
    """One row per external item: a review, a social/forum mention, or a
    press article. See the module docstring for why these three share a
    table."""

    __tablename__ = "mentions"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_mentions_source_external_id"),
        Index("ix_mentions_source_published_at", "source", "published_at"),
        Index("ix_mentions_kind", "kind"),
        # Supports 3.2's core metric query — median time from a negative
        # mention appearing to being assigned — filtering on sentiment and
        # ordering/filtering on assigned_at together.
        Index("ix_mentions_sentiment_assigned_at", "sentiment", "assigned_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- identity: what this is and where it came from ---
    # e.g. "google_reviews", "google_places_competitor", "reddit",
    # "instagram", "facebook", "tiktok", "news_gnews", "x". Free-text
    # rather than a DB enum deliberately: Phase 4 will add adapters one at
    # a time, and a new source should never require a migration just to
    # be nameable.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[MentionKind] = mapped_column(String(16), nullable=False)
    # The source's own ID for this item (a Google review ID, a Reddit
    # fullname like "t3_abc123", a hash of an article URL, ...). Required
    # — this is the other half of the uniqueness constraint that makes
    # 2.5's upsert idempotent.
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)

    # --- shared fields, meaningful across all three kinds ---
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    sentiment: Mapped[Sentiment | None] = mapped_column(String(16), nullable=True)
    # Free-text topic tags (see the mockup's Topics tab / 0.19b's
    # drill-down). A JSON list of strings rather than a join table —
    # revisit if topic taxonomy grows into something with its own
    # lifecycle (Phase 6-adjacent), not before there's a reason to.
    topics: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- kind="review" only ---
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_reply: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- kind="article" only ---
    headline: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(32), nullable=True)  # Rate Card tier, see config.OUTLET_TIER_MAP

    # "Where within the source" — a branch listing name (review), an
    # outlet/publication name (article), or a subreddit/handle (social
    # mention). One field because it plays the same structural role in
    # all three cases: the specific channel within the source, distinct
    # from the source (platform) itself.
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- provenance & lifecycle ---
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Soft-delete marker for the not-yet-built Reddit deletion-propagation
    # job (0.7 / Phase 5) — see module docstring. Nothing in this phase
    # sets it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- assignment & resolution (3.2) ---
    # An ingested-at timestamp (above) and an assigned-at timestamp on the
    # same row are what make the core PRD metric — median time from a
    # negative mention appearing to being assigned, target under 4
    # business hours — computable with a single-row query, not a
    # cross-table join. See repository.assign_mention()/
    # get_median_time_to_assignment() for the semantics (first-assignment-
    # wins on assigned_at; assigned_to always updates on reassignment).
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free text, not an enum: matches the mockup's "Assign to…" dropdown
    # values (Gian/Paul/Boom/Mixi) today, but that roster isn't this
    # schema's business to hardcode — see 6.4 (assignment roster with an
    # owner).
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"Mention(id={self.id!r}, source={self.source!r}, kind={self.kind!r}, external_id={self.external_id!r})"


class IngestionRun(Base):
    """The per-source ledger (2.4): last_attempt_at, last_success_at,
    status, error, items_ingested — the single source of truth for the
    P0-11 "last synced" indicator, and the fix for 0.2/0.3/0.6 applied at
    the pipeline level instead of per-connector.

    `last_attempt_at`/`last_success_at` are deliberately NOT columns on
    this table (see the checklist's own UNDERSTAND FIRST note under 2.4):
    a mutable "last success" field invites the same bug as the mockup's
    page-render timestamp — it's easy to update it in the wrong place and
    have it silently drift from what actually happened. Instead each run
    gets its own row, and `repository.get_source_freshness()` derives
    last_attempt_at/last_success_at by querying MAX(started_at) /
    MAX(finished_at WHERE status='success') per source. The ledger is an
    append-only log of what happened, not a cache of a summary of it.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        Index("ix_ingestion_runs_source_started_at", "source", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RunStatus] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"IngestionRun(id={self.id!r}, source={self.source!r}, status={self.status!r})"


class Event(Base):
    """The application event log (3.1). The PRD's measurement method says
    its success metrics come "from application logs (login events, alert
    timestamps, resolution timestamps)" — those logs did not exist before
    this table, and `repository.log_event()`/its convenience wrappers
    (record_ingestion, assign_mention, resolve_mention, log_export,
    log_login) are what write into it.

    - `mention_id` is a bare nullable Integer with NO foreign-key
      constraint, deliberately: not every event is about one specific
      mention (a `login` or `export_downloaded` event isn't), so this
      column can't be a mandatory FK, and adding an optional FK just to
      get referential integrity on the subset of rows that have a value
      would be over-modeling a column that's read, never joined-through,
      by anything in this phase.
    - `LOGIN` has no caller yet — there is no authentication system to
      call it from (Phase 5.5 builds one). This is schema readiness, the
      same pattern as `Mention.deleted_at`: nothing in this phase sets it,
      but the column exists so the feature that will doesn't also need a
      migration.
    - `metadata_json` is named that, not `metadata`, because `metadata` is
      a reserved attribute name on SQLAlchemy's `DeclarativeBase` (every
      mapped class already has a `.metadata` pointing at its
      `MetaData` object) — naming the mapped attribute `metadata` collides
      with that and errors at import time.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_event_type_occurred_at", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[EventType] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # No FK — see class docstring. Not every event is about one mention.
    mention_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Who did it. Free text, not a foreign key to a users table — there is
    # no auth system yet (Phase 5.5 builds one), so this is a name/handle
    # string for now, same reasoning as Mention.assigned_to.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"Event(id={self.id!r}, event_type={self.event_type!r}, mention_id={self.mention_id!r})"


class ResponseTimeBaseline(Base):
    """Schema-only support for 3.3: a pre-launch, one-time manual sample of
    "how long did the last 20 negative reviews take to get a reply, before
    this tool existed." That data-collection task is a human looking at
    Remedy's real historical Google Business Profile dashboard — nothing
    in this codebase can fabricate it, and this table intentionally ships
    with no sample/seed rows.

    See `docs/response-time-baseline-template.md` (written separately) for
    the manual capture process, and `repository.record_baseline_response_time()`
    for where each looked-up number lands once captured.
    """

    __tablename__ = "response_time_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # e.g. "Google review reply, Remedy BGC, 2-star, June 2026" — a human-
    # readable description of which historical item this row measures,
    # not a foreign key to a Mention row (the reviews being sampled here
    # predate this schema and were never ingested).
    source_description: Mapped[str] = mapped_column(String(512), nullable=False)
    # Nullable: a review with no reply as of the capture date is a real,
    # worth-recording outcome (see docs/response-time-baseline-template.md),
    # not missing data — excluding it would bias the baseline toward only
    # the reviews that happened to get answered. NULL means "no reply yet";
    # the capture date and any detail belongs in `notes`. Never a sentinel
    # number (e.g. 0 or 9999) for this case — see this project's established
    # preference (Phase 0/2) for NULL over a guessed value whenever "no data"
    # and "a real zero/measured value" must stay distinguishable.
    response_time_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_by: Mapped[str] = mapped_column(String(255), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return f"ResponseTimeBaseline(id={self.id!r}, response_time_hours={self.response_time_hours!r})"
