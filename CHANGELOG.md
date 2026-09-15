# Changelog

What actually shipped to Remedy Pulse, in the order it landed. One entry per
merged PR, newest first.

This is different from `docs/implementation-checklist.md`'s own "Changelog"
blocks — those track how *that document* changed between re-runs (items
closed, added, re-verified), per the checklist-maintenance convention it
follows. This file tracks what shipped to the actual product. There's no
versioned-release scheme yet (pre-v1, no tags cut) — entries are dated
instead.

---

## 2026-09-14 — Honour the 48-hour Reddit deletion commitment without an API credential

The signed Data Access application commits to removing deleted Reddit
content within 48 hours. `reddit_deletion_job.py` implements that through
PRAW — and has recorded `Missing required Reddit credential(s)` on every
run it has ever had. **The commitment had never once been honoured**, and
the new `reddit_public` source made that concrete rather than theoretical
by storing real Reddit posts nothing re-checked.

New `app/jobs/reddit_public_deletion_job.py` closes it with the same
public feed the ingestion job uses, so it works today rather than waiting
on an approval that may never arrive.

The endpoint was found by probing, because the obvious ones are gone:
`/api/info.json` and `/comments/<id>.json` both return 403 to non-OAuth
clients now; `/comments/<id>.rss` returns 200. Critically, a missing post
returns **404 with a valid empty feed** while rate limiting returns **429
with a zero-length body** — cleanly distinguishable, which is what makes
this safe.

**The failure policy is deliberately the inverse of the PRAW job's.**
That job treats any fetch failure as a deletion, which is sound for PRAW
(it separates not-found from transport failure itself). It would be
dangerous here: unauthenticated Reddit rate-limits hard — 2 of 7 rows
were 429'd on the first live pass — and a scrub cannot be undone. This
job scrubs only on positive evidence (a 404, or a `[deleted]`/`[removed]`
tombstone). A 429, 5xx, network error, unparseable body, or a comment
whose presence cannot be established is recorded *unverified*, left
untouched, and retried.

Verified live in both directions: 5 rows conclusively confirmed alive
with the 2 rate-limited rows left intact, and a real Reddit 404 scrubbing
content and author while keeping `url`/`venue`/`source` for the audit
trail.

`python -m app.admin reddit-compliance` answers the question the job's own
status cannot — whether every held row has actually been verified inside
the window — and exits non-zero when any is overdue.

Comments are best-effort by design: a gone parent thread or a tombstoned
body is conclusive, but a comment merely absent from its parent's feed is
**not** treated as deleted, because the feed truncates long threads.

Tests: 409 → 431.

## 2026-09-14 — Run it on real data: three key-free sources, a working login, and four bugs only a live run could find

A review of the shipped project found that no source was returning data,
nobody could log in, and a `.env` with live keys had gone out inside a
review archive. Everything below was verified by running it, not by
reading it — see [`docs/live-run-evidence.md`](docs/live-run-evidence.md)
for the run, the counts, and what is still honestly broken.

**Three sources that need no key, no account and no approval.** Google
News RSS, Bing News RSS, and Reddit's public search feed, over a new
shared `backend/fetch_rss.py` (stdlib XML parsing, no new dependency).
From an empty database, one `python -m app.scheduler` pass now ingests
**39 real items in 6 minutes** and classifies and topic-tags all of them.
One is genuine Remedy brand coverage, found on the same day GNews
returned zero for every configured brand term. The approval-gated
adapters stay registered and return better data the day their access
exists.

**A login that can actually be created.** `app.auth.create_user()` had no
caller outside the test suite — no CLI, no seed, no signup — so a
reviewer had no account and every feature behind the login was
unreachable. New `python -m app.admin`: `create-user`, `reset-password`,
`list-users`, `generate-secret`, and `check` (what the pipeline has
actually ingested and classified, read from the database). Also fixed:
nothing in the `uvicorn` import path called `load_dotenv()`, so the API
signed sessions with a random per-process key even when
`SESSION_SECRET_KEY` was set — tokens silently stopped verifying on every
restart.

**Topic tagging was never wired to run.** `app/topic_tagging.py` was
complete and tested with no caller, the same defect the classifier had.
Every mention sat at `topics = NULL` and the Topics tab rendered empty
against a full database. New `app/jobs/topic_tagging_job.py`, hourly.

**Enrichment was erased on every re-ingest.** Every adapter passes
`sentiment=None` (correctly), and `upsert_mention`'s last-write-wins rule
applied that NULL to the stored value — so each 12-hourly re-fetch wiped
the classifier's verdict. It did not self-heal: `classified_at` survived
the same upsert, and `classify_unclassified_batch()` selects on
`classified_at IS NULL`, so the row stayed permanently blank while every
status surface called it classified. Fixed via
`repository.ENRICHMENT_FIELDS`. Only a second pass over the same item can
produce this, which is why a green suite and a clean first run both
missed it.

**Two feed-format bugs**, both found by reading a live response: Google
News guids exceed `external_id`'s `VARCHAR(512)` and killed a real insert
(now hashed, not truncated — they share long prefixes and the column is
part of a UNIQUE constraint); and Bing's outlet lives in `<News:Source>`
under a namespace URI that *is the query URL*, so every Bing article had
a null outlet and therefore a null EMV tier.

**Secrets out of everything.** `scripts/package_release.py` builds review
archives with `git archive` — tracked files only, so an untracked `.env`
is never a candidate rather than being filtered out — then re-scans the
finished archive and deletes it on any finding. Wired into CI. Nothing
sensitive was ever committed (verified against full history); the root
cause was compressing the folder, which does not read `.gitignore`. Full
write-up in
[`docs/security/2026-09-14-env-in-distributed-archive.md`](docs/security/2026-09-14-env-in-distributed-archive.md).

**README corrected.** The Status section claimed GNews and Google Places
were live; neither was returning a row. It now states what was verified,
what is blocked and on whom, and that the test count is not the status of
record.

Tests: 345 → 409 passing.

## 2026-09-12 — [#20](https://github.com/sicefguroni/remedy-pulse/pull/20) Update README's Status section

The root README was frozen at Phase 0 ("demo/mockup stage... nothing
connected to live feeds yet") while `backend/README.md` stayed accurate
through every phase. Rewrote Status to name what's live today (GNews,
Google Places, Groq classification, the full API/auth layer, the
free-tier deploy runbook), what's built but blocked on an external
approval (Google's own reviews, Reddit, Meta), and what's deliberately
not computed pending a business sign-off (EMV). Docs-only.

## 2026-09-12 — [#19](https://github.com/sicefguroni/remedy-pulse/pull/19) API: malformed date query params now 400, not 500

`GET /api/mentions`, `GET /api/emv`, and `POST /api/exports/{type}` each
let a malformed `from`/`to` date 500 instead of cleanly 400ing —
`GET /api/overview` already handled this correctly; the other three now
match it.

## 2026-09-12 — [#18](https://github.com/sicefguroni/remedy-pulse/pull/18) Rename remedy-pulse-mockup.html to index.html

With real auth, a real API, and mock data removed from live sessions all
shipped, this file is the production frontend for real sessions, not just a
demo artifact — a filename containing "mockup" was the wrong signal in a
real user's address bar or a shared link. `git mv`, history preserved;
every reference updated across docs/comments/tests. `_redirects` removed
(no longer needed — Cloudflare Pages serves `index.html` at the root
automatically).

## 2026-09-12 — [#17](https://github.com/sicefguroni/remedy-pulse/pull/17) Add CHANGELOG.md

This file. Backfilled from all merged PRs to date.

## 2026-09-11 — [#16](https://github.com/sicefguroni/remedy-pulse/pull/16) Remove all mock data from logged-in sessions

Four places still rendered fabricated content in a real, authenticated
session: the Competitors tab's Top Voices/Category Watch cards, the
assign-roster dropdown's fallback, and `GET /api/overview`'s `aiSummaryText`
(a canned string returned on every call, real data or not). All four now
show honest empty/placeholder states in a live session; demo mode is
unaffected. See checklist item 0.25.

## 2026-09-11 — [#15](https://github.com/sicefguroni/remedy-pulse/pull/15) Ingestion scheduler hardening, classification job, Reviews/Competitors reply flow

- Reviews/Competitors: recovered "Also matches: ..." alias tooltips into the
  live API; wired the Reviews reply modal to a real
  `POST /api/reviews/by-venue/{venue}/reply` endpoint (checklist 7.4, 8.5,
  8.8).
- Hardened Google OAuth credential loading: job-safe errors instead of
  `SystemExit`, plus `GOOGLE_TOKEN_JSON` as a production secrets option
  (checklist 0.21 partial, 5.6).
- Scheduler: one job's failure no longer takes down the rest of a pass or
  crashes the standing process; fixed the same bug shape in GNews's
  403/quota path (checklist 0.21).
- Meta capability: an unexpected fetch failure now gets a real ledger row
  instead of vanishing (checklist 0.22).
- Added the classification scheduled job — `app/classification.py` (Phase
  6) was fully built but nothing ever called it in production (checklist
  0.23).
- `lastSyncedAt` now excludes non-external-data sources (classification,
  reddit_deletion_check) so the sync pill can't read "fresh" off an
  unrelated background job (checklist 0.24).

## 2026-09-05 — [#14](https://github.com/sicefguroni/remedy-pulse/pull/14) Fix deprecated Groq model, verify live end-to-end with a real key

The classifier's first-picked model (`llama-3.3-70b-versatile`) had been
deprecated on Groq's side by the time a real API key existed to test
against. Switched to `openai/gpt-oss-120b`, verified with a live smoke test
against the real Groq API.

## 2026-09-05 — [#13](https://github.com/sicefguroni/remedy-pulse/pull/13) Switch classification/topic tagging from Claude to Groq

Cost-driven switch (Anthropic has no free tier at this project's scale).
See `docs/decisions/09-sentiment-classifier-choice.md`.

## 2026-09-05 — [#12](https://github.com/sicefguroni/remedy-pulse/pull/12) Add a free-tier deploy runbook

Cloudflare Pages + Render + Neon + GitHub Actions — see
`docs/runbook-deploy-free-tier.md`.

## 2026-09-05 — [#11](https://github.com/sicefguroni/remedy-pulse/pull/11) Surface per-source failures in the UI; add /health

Closed checklist 4.7 for real (`renderDataSourceBanner()` — the mockup
never surfaced `GET /api/status`'s per-source error info beyond the sync
pill's timestamp) and retroactively corrected 5.5's checkbox (Phase 7 had
already built the auth it was waiting on). Added unauthenticated
`GET /health` and an hourly GitHub Actions scheduled workflow running the
scheduler, both needed by the upcoming free-tier deploy runbook.

## 2026-09-05 — [#10](https://github.com/sicefguroni/remedy-pulse/pull/10) Number decision records in chronological order

Docs-only: `docs/decisions/*.md` renumbered so the sequence reflects when
each was actually decided.

## 2026-09-05 — [#9](https://github.com/sicefguroni/remedy-pulse/pull/9) Merge Phase 8/9 into main

Reconciliation merge, not new feature work: Phases 0–7 were already on
`main`, but Phase 8/9 (the six tabs against real data, QA/acceptance tests,
backup/restore runbook, MediaWatch decommission decision) had only ever
landed on a phase branch that was never merged directly. Brought `main`
fully current through Phase 9.

## 2026-09-05 — [#8](https://github.com/sicefguroni/remedy-pulse/pull/8) Phase 4/5: adapters, security

Reddit/Meta ingestion adapters, deletion-propagation worker, PII
minimization extended to every source, dependency audit in CI.

## 2026-09-05 — [#7](https://github.com/sicefguroni/remedy-pulse/pull/7) Phase 8/9: six tabs against real data, launch readiness

Closed the six dashboard tabs' P0 acceptance criteria against real API
data; QA checklist and acceptance tests.

## 2026-09-05 — [#6](https://github.com/sicefguroni/remedy-pulse/pull/6) Phase 6/7: sentiment classification, alert routing, API layer, live dashboard

The FastAPI `app/api/` layer (every `GET`/`POST` in the API contract) and
the mockup's data-driven refactor, built in parallel against one shared
hand-written contract. First version of `app/classification.py`.

## 2026-09-04 — [#5](https://github.com/sicefguroni/remedy-pulse/pull/5) Phase 4/5: ingestion adapters, scheduler, auth primitives, compliance

`app/jobs/` wiring every connector into the ledger/repository; the
cadence-based scheduler; `app/auth.py`.

## 2026-09-04 — [#4](https://github.com/sicefguroni/remedy-pulse/pull/4) Phase 3: instrumentation — event log, assignment metric, exports

Event log, the core "time to assignment" metric query, export-usage
instrumentation — all landed before the features that would otherwise have
no baseline to measure against.

## 2026-09-04 — [#3](https://github.com/sicefguroni/remedy-pulse/pull/3) Phase 2: schema, persistence, ledger, and migrations

The vendor-agnostic `Mention` schema, Postgres + SQLAlchemy + Alembic,
config/secrets handling, the ingestion run ledger, idempotent upsert.

## 2026-09-04 — [#2](https://github.com/sicefguroni/remedy-pulse/pull/2) Phase 1: GNews news/press ingestion connector

The engineering half of checklist 1.5's GNews evaluation.

## 2026-09-04 — [#1](https://github.com/sicefguroni/remedy-pulse/pull/1) Phase 0: close the gaps in what already exists

The initial gap audit's 20 findings, closed — retry/backoff, per-listing
status/fetchedAt, XSS-safe rendering, tests/lint/CI stood up from nothing.
