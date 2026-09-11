# Decision record: does `lastSyncedAt` count every registered source, or only external-data ones?

**Status:** DECIDED AND IMPLEMENTED (2026-09-11). This covers checklist item 0.24, raised directly
by building 0.23 (`app/jobs/classification_job.py`).

## The decision

**`lastSyncedAt` (and the mockup's sync pill) now count only external-data-fetching sources.**
`app.jobs.classification_job` and `app.jobs.reddit_deletion_job` — both re-processing rows some
*other* job already ingested, never fetching from an external source themselves — are excluded
from the "most recent successful sync" computation, on both the backend (`GET /api/overview`'s
`lastSyncedAt`, `docs/api-contract.md`'s own definition) and the frontend (the mockup's
`mostRecentSuccessAt()`). They still appear in `GET /api/status`'s `sources` list — a failed
classification or deletion-check run must stay visible — just tagged `isDataSource: false` so a
consumer computing an aggregate freshness value knows to skip them.

## Context

`docs/api-contract.md` defined `lastSyncedAt` as "the most recent successful run across every
registered source, per `repository.get_source_freshness()`" — unqualified. That was already
slightly wrong the moment `reddit_deletion_job.py` was built (checklist 5.1): it's a maintenance
sweep, not a data-fetching job, so its own success/failure was never actually informative about
"is the DATA stale." Nobody flagged it, because nothing exercised the problem visibly — every
ingestion job (Google, Reddit, GNews, Meta) and `reddit_deletion_check` all shared roughly the same
12-hour-scale cadence, so a maintenance job happening to succeed slightly more recently than a data
job rarely produced a noticeably misleading pill.

Building `classification_job.py` (checklist 0.23) made this concrete and much more visible:
that job's cadence is deliberately ~15 minutes (`app.scheduler.CADENCE_HOURS["classification"]`),
tied to the PRD's 4-business-hour alert-assignment metric — a mention that sits unclassified for
12 hours can't be routed to the alert workflow in time regardless of how fast a human responds
once it is. At that cadence, classification succeeds almost every time the scheduler ticks, which
means the old, unqualified `lastSyncedAt` definition would show a "synced minutes ago" pill nearly
continuously — including while every real external data source (Google, Reddit, GNews, Meta) had
gone stale for hours. That directly undermines the PRD's own stated reason `lastSyncedAt`/the sync
pill exists: *"so I don't mistake stale data for 'no news.'"*

## Options considered

**A. Leave it as every registered source (status quo).** Simplest, already shipped and documented.
Defensible in a narrow reading — "the system as a whole is functioning" is a real thing worth
knowing too. Cost: the pill can actively mislead about *data* freshness specifically, which is the
one thing P0-11 asks it to answer, and that cost got much larger the moment a fast-cadence
non-data job existed.

**B. Redefine it to only external-data-fetching sources (chosen).** Restores the pill's original
intent precisely — "when did real content last actually come in." Cost: a real, if small, change
to a documented contract value, and needed a way to mark which registered sources count as "data"
vs. "maintenance" (nothing in the job contract distinguished them before this).

## Reasoning

P0-11's own PRD wording is specifically about data staleness, not "is the pipeline alive" in
general. A job that touches zero external sources has no bearing on that question by construction
— it can succeed indefinitely while every actual data feed is broken, or vice versa fail while data
is perfectly current. Collapsing both into one number throws away exactly the distinction the
2.4/4.7 ledger design was built to preserve elsewhere (see `meta_job.py`'s own three-separate-
ledger-sources reasoning, and `app/jobs/__init__.py`'s general "one SOURCE_NAME, one independently
diagnosable fact" philosophy) — there's no reason this one aggregate value should be the exception.

## Implementation

- `app/jobs/__init__.py`'s job contract gains an optional `IS_DATA_SOURCE: bool` attribute
  (default `True` via `getattr(job, "IS_DATA_SOURCE", True)` at every read site, so every ordinary
  ingestion job needs zero changes). Set to `False` on `classification_job.py` and
  `reddit_deletion_job.py` only.
- `app/api/routes/overview.py`'s `lastSyncedAt` computation skips any job with
  `IS_DATA_SOURCE = False`.
- `app/api/routes/status.py`'s `GET /api/status` gains an `isDataSource` field per source (a label,
  not a filter — every source still gets a row, including a failed one, so the per-source failure
  banner still works for classification/reddit_deletion_check).
- `index.html`'s `mostRecentSuccessAt()` skips any `status.sources` entry with
  `isDataSource === false`.
- `docs/api-contract.md` updated in both places (`lastSyncedAt`'s definition, `GET /api/status`'s
  shape) to describe the new behavior, not just the code.

Verified: new backend tests proving `lastSyncedAt` picks the older real-data-source timestamp over
a newer classification-run timestamp (SQLite) plus the existing `isDataSource` shape assertions;
a real-browser check confirming `mostRecentSuccessAt()` applies the same exclusion client-side.

## What would change this

If the team decides the pill is meant more broadly as "is the whole ingestion+processing pipeline
alive" rather than "is the data fresh" — a legitimate, different product intent — this should be
reverted to counting every registered source again, with that broader framing written down here in
its place.
