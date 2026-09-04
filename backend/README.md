# Backend Connectors — Setup Guide

Pulls real review data for Remedy's own listings, public rating
benchmarks for competitors, and news/press coverage, and writes each out
as JSON that is the *input* for the corresponding tab in
`remedy-pulse-mockup.html` — not a byte-for-byte match of any of them.
See "What you get" below for exactly what is and isn't covered.

## Before you start: the one real blocker

The endpoint that reads individual reviews on owned listings
(`mybusiness.googleapis.com/v4`) is **not self-serve on a new Google Cloud
project**. Google gates it behind an access request form:
https://developers.google.com/my-business/content/prereqs

Submit that request as early as possible — it's the long pole here, not
the code. Everything else in this folder (OAuth setup, competitor
ratings via Places API) works without waiting on Google's approval.

## Setup steps

1. **Create a Google Cloud project** (console.cloud.google.com), name it
   something like "Remedy Pulse".
2. **Enable APIs**: "My Business Account Management API",
   "My Business Business Information API", and "Places API".
3. **Request Business Profile API access** using the link above — do this
   now, it can take time to get approved.
4. **Create OAuth credentials**: APIs & Services → Credentials → Create
   Credentials → OAuth client ID → type "Desktop app". Download the JSON.
5. **Create a Places API key**: APIs & Services → Credentials → Create
   Credentials → API key. Set a budget alert — this one's billed per
   request past a free quota.
6. **Copy `.env.example` to `.env`** and fill in the path to your
   downloaded OAuth JSON and your Places API key.
7. **Install dependencies**:
   ```
   pip install -r requirements.txt
   ```
8. **Authorize once**:
   ```
   python oauth_setup.py
   ```
   This opens a browser window — log in as whichever Google account
   manages Remedy's Business Profile listings (must already have
   verified ownership).
9. **Find your location IDs**: run `python fetch_owned_reviews.py` once
   with `config.py` unfilled — it'll fetch every location the logged-in
   account can see and print their IDs so you can match them to Remedy's
   actual branches. Paste those IDs into `config.py`, then run it again.
10. **Find competitor place IDs**: use Google's Place ID Finder
    (linked in `config.py`) to look up Belo, Aivee, Kamiseta, etc., then
    run `python fetch_competitor_ratings.py`.
11. **News/press coverage (separate from Google entirely)**: sign up for
    a free GNews API key at https://gnews.io (self-serve, no approval
    wait), paste it into `.env` as `GNEWS_API_KEY`, then run
    `python fetch_news_articles.py`. See
    `docs/decisions/news-press-ingestion-path.md` for why GNews was
    picked as the first option to evaluate.

## What you get

Every file below now starts with a top-level `"fetchedAt"` (ISO-8601 UTC
timestamp) — this is the data the P0-11 "last synced" indicator needs;
there was nothing for the UI to display before.

- **`reviews_aggregate.json`** — `{"fetchedAt": ..., "listings": [...]}`,
  one row per branch (rating, review count, response rate, pending
  replies). **Not** an exact match of the Reviews tab table — three
  differences to know before wiring a consumer to it:
  - `responseRate` is a **fraction** (`0.88`), not a percentage — the
    table renders `88%`, so the consumer must format it.
  - There is **no `Trend (30d)` field**. Computing a real 30-day trend
    needs historical snapshots this script doesn't keep (it only ever
    sees the current live state); faking one from a single snapshot
    would be worse than omitting it.
  - Each row carries a `status`: `"ok"`, `"no_reviews"` (fetched fine,
    genuinely zero reviews), `"access_denied"` (the reviews endpoint
    returned 403 for this listing — rating/counts are all `null`, never
    `0`, so it can't be mistaken for a real zero-review branch), or
    `"error"` (request failed even after retries). Map `"ok"` with
    `pendingReplies == 0` to the "All clear" tag, `"ok"` with
    `pendingReplies > 0` to "N pending replies", and both
    `"access_denied"` and `"error"` to a distinct "sync failed" state —
    never to "All clear".
- **`reviews_raw.json`** — `{"fetchedAt": ..., "reviews": [...]}`, one row
  per individual review, with reviewer names already masked to
  first-name-plus-initial per the PH Data Privacy Act note in §11 of the
  spec. This is the input for the Mentions tab once the mockup is
  refactored to render from data instead of hardcoded markup — that
  refactor is separate work, not part of this script.
- **`competitor_ratings.json`** — `{"fetchedAt": ..., "competitors": [...]}`,
  rating + review count per competitor, plus a small capped sample of
  their reviews (Google doesn't give more than that for listings you
  don't own). Each row carries a `status`: `"ok"`, `"not_found"` (API
  responded but not with a usable result for that place_id), or
  `"error"` (request failed even after retries).
- **`news_articles.json`** — `{"fetchedAt": ..., "articles": [...]}`, raw
  article metadata (outlet, headline, url, publishedAt, description) from
  GNews. **This is normalized wire data, not EMV.** The EMV tab's dollar
  figures come from a formula (Base AVE × Prominence × PubScore ×
  PR_Credibility × Sentiment) that needs editorial judgment inputs no news
  API provides — this script doesn't invent them. Each article carries:
  - `tier`: the Rate Card tier (`"National News"` / `"Lifestyle Mag"` /
    `"Broadcast TV"`), but **only** for the six outlets already in
    `config.OUTLET_TIER_MAP` (seeded from the mockup's sample EMV rows).
    Any other outlet gets `tier: null` and `status: "unmapped_outlet"` —
    add it to the map once Marketing confirms which Rate Card row it
    belongs in, rather than guessing.
  - `sentiment`: always `null`. Sentiment classification is Phase 6's
    job, applied consistently across every source — not invented ad hoc
    inside this one connector.

## Retry behavior

Every outbound `GET` in `fetch_owned_reviews.py`,
`fetch_competitor_ratings.py`, and `fetch_news_articles.py` goes through
`http_utils.get_with_retry`,
which retries transient failures (429, 500, 502, 503, 504) with
exponential backoff and jitter — honoring a `Retry-After` header on 429
when the server sends one. A run that exhausts its retries raises clearly
(`RetryExhaustedError`) instead of silently writing partial or stale-look
data; the per-listing/per-competitor `status` field records it as
`"error"` rather than pretending the fetch succeeded.

## Persistence foundations (Phase 2 — `backend/app/`)

The `fetch_*.py` scripts above still write standalone JSON files exactly
as Phase 0 left them, and still work run by hand with no database at
all. Separately, `backend/app/` is the vendor-agnostic schema +
persistence layer Phase 4 will wire those connectors into when they
become scheduled jobs instead of one-shot scripts — see
`backend/app/__init__.py` for that boundary. To stand it up locally:

```
docker compose up -d          # starts Postgres on localhost:5434
cp .env.example .env          # DATABASE_URL default already matches
pip install -r requirements.txt
alembic upgrade head          # creates the mentions + ingestion_runs tables
```

- **`app/models.py`** — the `Mention` table (2.1): one row per review,
  social/forum mention, or press article, keyed uniquely on
  `(source, external_id)`. See its module docstring for why one table
  covers all three of the mockup's shapes, and what `raw_payload` /
  `deleted_at` are pre-built for (0.7's not-yet-built Reddit
  deletion-propagation job).
- **`app/repository.py`** — `upsert_mention()` (2.5): idempotent on
  `(source, external_id)`, so a polling adapter re-fetching the same item
  updates it in place instead of duplicating it. `start_run()` /
  `get_source_freshness()` (2.4): the ingestion ledger — freshness is
  derived from the run log, never stored as a mutable field, so "Reddit
  is 3 days stale but Google is current" is answerable per-source.
- **`app/config.py`** — `Settings` (2.3): validated at startup, fails
  once with every problem named, rather than the existing scripts'
  per-line `os.getenv(...)` + `raise SystemExit`. `DATABASE_URL` is
  required; the existing Google/GNews credentials are optional here (a
  ledger read doesn't need a Places API key).
- **`migrations/`** — Alembic (2.6), autogenerated from `app/models.py`
  and verified against a real local Postgres (`alembic upgrade head` /
  `alembic check` / a downgrade-then-upgrade roundtrip, not just that the
  migration file parses).
- See `docs/decisions/persistence-choice.md` for why Postgres specifically
  (2.2's "YOUR CALL").

Tests: `pytest backend/tests/` covers the repository/ledger logic against
in-memory SQLite (fast, no external dependency — this is what CI runs)
*and*, in `test_app_repository_postgres.py`, against a real Postgres
container when one is reachable (`docker compose up -d` first) — that
file exists because the SQLite tests exercise a different `ON CONFLICT`
code path than production actually uses, and only the Postgres run
proves the real one works. It skips cleanly (not a failure) with no
Postgres reachable.

## Instrumentation (Phase 3 — event log, the core metric, response-time baseline)

Phase 2 gave every mention an `ingested_at`. Phase 3 adds the rest of what
the PRD's success metrics need: an event log, an `assigned_at`/`resolved_at`
on the same row as `ingested_at`, and somewhere to put a pre-launch
response-time baseline.

- **`app/models.py` — `Event`** (3.1): one row per `login`,
  `item_ingested`, `item_assigned`, `item_resolved`, or
  `export_downloaded` occurrence — the application log the PRD's
  measurement method assumes exists ("from application logs (login
  events, alert timestamps, resolution timestamps)"). `mention_id` has no
  FK constraint (not every event is about one mention); `metadata_json`
  is named that, not `metadata`, because `metadata` collides with
  SQLAlchemy's own reserved attribute on `Base`. `LOGIN` has no caller
  yet — there's no auth system (Phase 5.5 builds one) — same
  schema-readiness pattern as `Mention.deleted_at`.
- **`app/models.py` — `Mention.assigned_at` / `assigned_to` / `resolved_at`**
  (3.2): an ingested-at timestamp and an assigned-at timestamp on the
  same row, so the core PRD metric — median time from a negative mention
  appearing to being assigned, target under 4 business hours — is a
  single-row computation, not a join.
- **`app/models.py` — `ResponseTimeBaseline`** (3.3): schema only, no
  seed data. See "Response-time baseline" below.
- **`app/repository.py`** functions:
  - `log_event()` — the generic logger everything else below calls.
  - `record_ingestion()` — what a Phase 4 adapter should call instead of
    `upsert_mention()` directly: it upserts *and* logs `ITEM_INGESTED`,
    but only on a genuine first insert, never on a re-ingest of the same
    `(source, external_id)`. This is also why `upsert_mention()` now
    returns `bool` (`True` = inserted, `False` = updated) — determined by
    an explicit existence check before the upsert, not a
    dialect-specific trick, matching this project's general preference
    for clarity over cleverness at its stated ingestion volume.
  - `assign_mention()` — sets `assigned_to` unconditionally (reassignment
    always updates who owns it) but sets `assigned_at` only the *first*
    time (first-assignment-wins), because 3.2's metric is time to first
    ownership, not time of most recent reassignment. Every call still
    logs an `ITEM_ASSIGNED` event, reassignment included.
  - `resolve_mention()` — sets `resolved_at` to now on every call (no
    "unresolve" concept yet) and logs `ITEM_RESOLVED`.
  - `log_export()` / `get_export_activity()` — the 3.4 export
    instrumentation: log an `EXPORT_DOWNLOADED` event per CSV download,
    count how many landed after some `since` timestamp (for checking the
    "at least one export per week" target).
  - `log_login()` — logs a `LOGIN` event; nothing calls it yet (no auth
    system — Phase 5.5).
  - `get_median_time_to_assignment()` — the 3.2 metric itself: median
    `(assigned_at - ingested_at)` in hours across negative-sentiment,
    assigned mentions (optionally scoped to `ingested_at >= since`).
    Computed with Python's `statistics.median` rather than a SQL
    `percentile_cont`, deliberately, so the exact same logic is correct
    on SQLite (tests) and Postgres (production). Returns `None` — not
    `0` — when there's no qualifying data yet.
  - `record_baseline_response_time()` / `get_baseline_summary()` — see
    below.

### Response-time baseline (3.3)

3.3 asks for a rough manual sample — "how long did the last 20 negative
reviews take to get a reply, before this tool existed" — captured from
Remedy's real historical Google Business Profile dashboard. That
data-collection step is human work this codebase cannot fabricate;
`docs/response-time-baseline-template.md` (written separately) is where
that process is documented. `ResponseTimeBaseline` and
`repository.record_baseline_response_time()` are only the place each
looked-up number lands once someone has actually done that lookup —
`get_baseline_summary()` then reports `{"count", "median_hours",
"mean_hours"}` over whatever rows exist, `None` (not `0`) for the two
derived fields until at least one row is captured.

## Ingestion adapters and the scheduler (Phase 4)

`backend/app/jobs/` wires every connector into the Phase 2/3 persistence
layer as a proper job: `SOURCE_NAME` + `run(session)`, wrapping
`start_run()`/`record_ingestion()` instead of writing standalone JSON.
See `app/jobs/__init__.py`'s docstring for the exact contract and how to
register a new one (one line in `JOBS`).

- **4.1/4.2 — Google jobs** (`google_reviews_job.py`, `google_places_job.py`)
  wrap the existing `fetch_owned_reviews.py`/`fetch_competitor_ratings.py`
  logic (imported, not reimplemented) with the same per-listing/per-
  competitor resilience those scripts already had.
- **4.5 — `news_job.py`** wraps `fetch_news_articles.py` the same way.
- **4.3/5.1/5.2 — Reddit** (`fetch_reddit_mentions.py`, `app/jobs/reddit_job.py`,
  `app/jobs/reddit_deletion_job.py`): PRAW-based, a versioned descriptive
  User-Agent per Reddit's required format (`_MASK_PEPPER`'s neighbor
  constant `USER_AGENT` — the account-username placeholder in it must be
  filled in before this can authenticate), a script-app credential flow,
  and a *separate* recurring job (`reddit_deletion_job.py`, its own ledger
  source `reddit_deletion_check`) that re-checks stored Reddit rows on a
  schedule and scrubs content/author fields the moment an upstream
  deletion is detected — "ingestion-in-reverse," not a delete webhook, per
  `docs/decisions/reddit-deletion-propagation.md`. **Not live-verified**:
  no Reddit credentials exist in this environment (the commercial Data
  Access tier is still a pending approval per Phase 1); every test here
  mocks PRAW at the import boundary. Fill in `REDDIT_CLIENT_ID` /
  `REDDIT_CLIENT_SECRET` / `REDDIT_USERNAME` / `REDDIT_PASSWORD` (see
  `.env.example`) and the User-Agent's username placeholder before this
  goes live.
- **4.4/5.3 — Meta** (`fetch_meta_mentions.py`, three separate job
  wrappers): Instagram comments, Instagram mentions, and Facebook comments
  are three independently-configured, independently-ledgered capabilities
  (see `app/jobs/meta_job.py`'s module docstring for why — briefly, a
  lapsed permission on one must not make the other two look stale, so
  each gets its own `IngestionRun` source and its own `app.jobs.JOBS`
  entry: `meta_instagram_comments_job.py`, `meta_instagram_mentions_job.py`,
  `meta_facebook_comments_job.py`, all thin wrappers around
  `meta_job.py`'s per-capability functions). **Not live-verified**: no
  Meta App Review access exists yet for any of the three scopes (Phase
  1.3, still open) — every test mocks the Graph API HTTP calls.
- **5.3 — PII minimization, every source**: Facebook commenter names reuse
  `fetch_owned_reviews.mask_reviewer_name()` (real names, same shape).
  Reddit usernames and Instagram handles are pseudonyms, not names, so
  each gets its own masking function (`fetch_reddit_mentions.mask_reddit_username()`,
  `fetch_meta_mentions.mask_instagram_handle()`) — a short recognizable
  prefix plus a fixed-length hash suffix, deterministic (same handle
  always masks the same way, for dedup) but not a truncation of the
  original. Neither is a security control (see each function's own
  docstring for the honest limits) — both exist to satisfy the PH Data
  Privacy Act minimization principle `mask_reviewer_name()` already cites,
  extended to the sources that didn't have it before this phase.
- **4.6 — `scheduler.py`**: deliberately simple, per the checklist's own
  instruction ("the PRD scopes v1 at same-day/next-day freshness, not
  real-time — say so in the code, or someone will over-build it"). A
  per-source cadence check (`is_due()`, default 12h) plus `python -m
  app.scheduler` (one pass) or `--loop` (a plain sleep loop) — not a task
  queue.
- **4.7 — `status_report.py`**: `python -m app.status_report` prints every
  registered source's freshness. This is explicitly a **stopgap**, not
  4.7 done — the real fix is a UI surface, which needs Phase 7's API
  layer to exist first (see the script's own module docstring).

## Authentication primitives (Phase 5.5)

`app/auth.py` — `hash_password()`/`verify_password()` (bcrypt),
`create_user()`/`authenticate()`, and hand-rolled HMAC-signed session
tokens (`create_session_token()`/`verify_session_token()` — deliberately
not JWT, see the module docstring's over-build reasoning). `authenticate()`
runs a real bcrypt comparison even when the email isn't found (against a
fixed dummy hash) so a failed lookup and a failed password check take
comparably long, and never distinguishes "no such user" from "wrong
password" in its return value — both are just `None`. A successful login
updates `User.last_login_at` and calls `repository.log_login()`, so
Phase 3's `EventType.LOGIN` finally has a caller.

This is schema/logic only — **there is no HTTP framework or login route
anywhere in this repo**, so "add authentication to the dashboard" isn't
fully done by this alone. Phase 7 (the API layer) is what actually calls
into this.

## Compliance and security documentation (Phase 5)

Several Phase 5 items are decisions/reviews, not code, and are recorded
under `docs/decisions/`:
- `ph-data-privacy-act-review.md` (5.4) — the spec `RemedyPulseSpec_1`,
  cited by `mask_reviewer_name()` and elsewhere, does not exist in this
  repo. Documents every citation found and what a real review must cover
  once the actual spec is available — does not perform that review.
- `reddit-c4-no-resale-control.md` (5.8) — the Reddit access request's
  written commitment ("not resold, redistributed, or used to train any
  model") and a recommended enforcement mechanism at the LLM-call
  boundary, relevant the moment P1-1's AI summary is wired to a real
  model instead of its current 3 canned strings.
- `secrets-at-rest.md` (5.6) — `token.json`'s live-credential risk and a
  generic recommendation (real secrets manager > env vars > bare file)
  pending an actual hosting decision.

`requirements.txt`'s `requests`/`python-dotenv` pins are set to the
earliest versions `pip-audit` (5.7, now also a CI step —
`.github/workflows/ci.yml`) reports zero known vulnerabilities for as of
this phase; re-run `pip-audit -r requirements.txt` and bump the pin
before trusting an older one again.

## Known limitations, honestly

- Individual reviews have no public deep-link URL from either API — this
  is why the mockup's "View source" links use a search fallback instead
  of a direct link, and that won't change once this connector is live.
- Competitor review data is a small sample, not the full history — fine
  for rating benchmarks, not for a full competitor mention feed.
- If the Business Profile API access request gets rejected or stalls,
  the fallback is a licensed reviews aggregator — worth flagging to
  Paul/Marketing as its own §16-style decision if that happens.
