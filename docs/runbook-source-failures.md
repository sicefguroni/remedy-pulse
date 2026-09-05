# Runbook: what to do when a source fails

Checklist item 9.4: *"Runbook: what to do when a source fails. Who notices, how, and what they do. Directly serves the PRD's stale-data edge case."*

The PRD's stale-data edge case, quoted directly (`remedy-pulse-prd.md`, Edge cases):

> "As a marketing team member, if a data source is down or delayed, I want to see when the dashboard was last successfully synced so that I don't mistake stale data for 'no news.'"

This runbook is what a two-person team actually does about that, tonight, with what's actually built.

---

## 1. Who notices, and how (read this part honestly)

**Nobody is automatically notified today.** There is no monitoring, no alerting, no on-call paging, and no push notification of any kind. This is not an oversight to work around quietly — it's stated plainly in `backend/app/status_report.py`'s own module docstring: that script is an explicit **STOPGAP**, ahead of the real fix. Its own words: *"Marketing does not run CLI scripts and read their stdout; the actual fix is a UI surface (a per-source 'last synced'/'sync failed' indicator, per the mockup's own P0-11 note), and that needs Phase 7's API / data-driven refactor to exist before it can be built."*

`GET /api/status` (`backend/app/api/routes/status.py`) is the real fix for the *reading* side of this — it supersedes `status_report.py` for anything that can call an HTTP API, i.e. the actual dashboard. But it is still **pull-based**: it answers when asked, it does not push. Nothing calls it on a schedule and nothing watches its response for a change. Both of these are legitimate, currently-working ways to read the same underlying freshness data (`app.repository.get_source_freshness()`), and both require a human to go looking.

**The stopgap, until real alerting exists:** someone on the two-person team runs one of the following once at the start of each business day, before anyone looks at the dashboard for real work:

- `python -m app.status_report` (from `backend/`, with the venv active) — prints one section per registered source, no auth needed, works from a terminal.
- `GET /api/status` with a valid session token (`Authorization: Bearer <token>`, obtained via `POST /api/auth/login`) — same underlying data, JSON, usable from a browser/Postman/curl once the API is running.

Treat that daily check as the manual "notice" mechanism. **Name it as a stopgap when you do it, not a permanent process** — it depends entirely on a human remembering to run it, which is exactly the kind of thing that quietly stops happening under deadline pressure. The real fix is the per-source status surface in the dashboard UI itself (checklist 4.7), which is still open.

---

## 2. What "a source has failed" looks like, concretely

Read `GET /api/status`'s response shape directly (`docs/api-contract.md`):

```json
{
  "sources": [
    {"source": "google_reviews", "lastAttemptAt": "iso8601 or null", "lastSuccessAt": "iso8601 or null", "lastStatus": "success|partial|access_denied|error|null", "lastError": "string or null"}
  ]
}
```

One entry per member of `app.jobs.JOBS` (`backend/app/jobs/__init__.py`) — this is the **real, current, complete list**, read off the registry rather than reconstructed from memory:

| # | `source` value (exact string) | What it is |
|---|---|---|
| 1 | `google_reviews` | Owned Google reviews (per branch) |
| 2 | `google_places_competitor` | Google Places competitor ratings |
| 3 | `news_gnews` | GNews press/news ingestion |
| 4 | `reddit` | Reddit mention ingestion |
| 5 | `reddit_deletion_check` | Reddit 48-hour deletion-propagation re-check (not new ingestion — see §3.4) |
| 6 | `instagram_comments` | Meta: Instagram comments |
| 7 | `instagram_mentions` | Meta: Instagram mentions |
| 8 | `facebook_comments` | Meta: Facebook comments |

Note on 6–8: `meta_job.py` deliberately does not expose one `SOURCE_NAME` for "Meta" as a whole — Instagram comments, Instagram mentions, and Facebook comments are three independently-cadenced capabilities with three separate ledger entries, wrapped by three thin job modules (`meta_instagram_comments_job.py`, `meta_instagram_mentions_job.py`, `meta_facebook_comments_job.py`) so each can be current or stale independently. There is no single "Meta is down" status — check all three.

**A source has failed if either is true:**

- `lastStatus` is `error` or `access_denied` for any of the eight sources above.
- `lastAttemptAt` is older than that source's cadence allows for. Every registered source currently runs on the same cadence — `backend/app/scheduler.py`'s `DEFAULT_CADENCE_HOURS = 12.0`, with an empty `CADENCE_HOURS` override dict (no source has a custom cadence as of this writing). A source is "due" again 12 hours after its `lastAttemptAt`, so if `lastAttemptAt` is more than roughly 24 hours old (two missed cycles, to allow for the scheduler itself not having been invoked, not just a slow run), treat that as a failure of the *pipeline*, not just of one source — check whether `python -m app.scheduler` is being run/scheduled at all (see §4).

---

## 3. Per-source-type response steps

Don't invent generic advice per source — respond according to what this repo already documents as that source's own failure mode.

### 3.1 Google reviews / Google Places (`google_reviews`, `google_places_competitor`) — `lastStatus: access_denied`

`fetch_owned_reviews.py`'s own docstring is explicit: `access_denied` means the reviews endpoint returned a 403 for that listing, and its module-level comment says a 403 **"almost always means the Business Profile API access request"** hasn't been granted yet — Phase 1's known blocker (checklist 1.1, still open as of the last checklist pass).

**Response:**
- While Google Business Profile API access has not yet been confirmed as granted (checklist 1.1): treat `access_denied` here as **expected, not a new outage**. Confirm this against the current status of 1.1 rather than escalating it as if something broke.
- Once access has been confirmed granted, and `access_denied` starts appearing (or keeps appearing) anyway: **escalate as a new problem** — something changed (revoked scope, expired credential, wrong project) and needs investigation, it is no longer the known Phase 1 gap.

### 3.2 Reddit / Meta (`reddit`, `reddit_deletion_check`, `instagram_comments`, `instagram_mentions`, `facebook_comments`)

These adapters are built and tested against mocks but **not live-verified** — per Phase 4/5's own status notes in `docs/implementation-checklist.md`, no real Reddit or Meta credentials exist in this environment as of this repo's current state (Reddit's commercial-tier approval and Meta's App Review are both still open per Phase 1).

**Response:**
- Expect these five sources to show as fully down, erroring, or simply never-attempted (`lastAttemptAt: null`) until credentials are actually provisioned. That is the **expected state**, not an incident.
- The only thing that is a real incident here is **divergence from that expected state** — e.g., a source was working (had a recent `lastSuccessAt`) and has now stopped, or credentials were provisioned and it never produced a first successful run. If you don't know whether credentials have been provisioned yet, check with whoever owns Phase 1 access requests before treating a `reddit`/Meta failure as new.

### 3.3 GNews (`news_gnews`) — `lastStatus: error`

`fetch_news_articles.py`'s own docstring: GNews's free tier is **100 requests/day**, results generally capped further on top of that. A 403 from GNews "usually means `GNEWS_API_KEY` is invalid or the free-tier daily quota (100 requests/day) is exhausted."

**Response, in order:**
1. Confirm `GNEWS_API_KEY` is actually set in the environment the job runs in (missing key fails loudly with `"GNEWS_API_KEY is not set. Copy .env.example to .env and fill it in."` — check that message specifically in `lastError`).
2. If the key is set, check whether the day's 100-request quota has been exhausted — this resolves itself on the next UTC day; it is not something to "fix" beyond confirming that's actually what happened.
3. If neither explains it, treat it as a genuine `error` after retries were exhausted (`http_utils.get_with_retry` already retries transient failures) and investigate as a real outage.

### 3.4 `reddit_deletion_check` specifically — treat as higher severity than an ordinary ingestion failure

This is not a normal ingestion job. Per `reddit_deletion_job.py`'s own docstring and `docs/decisions/reddit-deletion-propagation.md`: it re-checks already-stored Reddit rows against Reddit to comply with the written commitment (in the submitted Reddit Data Access Request) that content and author-identifying data are removed **within 48 hours of deletion on Reddit**.

A failed `reddit_deletion_check` run doesn't just mean stale data the way a failed ingestion run does — as the decision doc puts it, *"a failed run doesn't just mean stale data, as it would for ordinary ingestion; it means an active breach of a written retention commitment, growing more overdue with every hour it stays down."* The roadmap's own Phase 5 header states this as a hard constraint: nothing here may be deferred past the first production ingestion run against real data.

**Response:** if `reddit_deletion_check` shows `error` or a stale `lastAttemptAt` while `reddit` (ingestion) itself is healthy, **escalate this immediately, ahead of other open failures** — every hour it stays down while Reddit content is actually stored is time against the 48-hour window, not just a data-freshness inconvenience. Restart it (§4) as the first priority, not the last.

---

## 4. What "fixed" looks like

1. Re-run ingestion: either run `python -m app.scheduler` once (from `backend/`), or wait for whatever is invoking it on schedule (cron / Windows Task Scheduler — see `scheduler.py`'s own docstring; it does not schedule itself) to tick again. `scheduler.py` skips any source not yet due, so re-running it is always safe — it will not re-run a source before its cadence says it should.
2. Confirm via `GET /api/status` (or `python -m app.status_report`) that the affected source now shows a **fresh `lastSuccessAt`** — not just a fresh `lastAttemptAt`, since an attempt can still fail. `lastStatus` should read `success` (or `partial`, which means some items ingested but not all — check `lastError` even on `partial`).
3. If `lastStatus` is still `error`/`access_denied` after a re-run, you have not fixed it yet — go back to §3 for that source type.

---

## 5. How stale is too stale — and what a marketing team member should do

The PRD scopes v1 freshness at **same-day/next-day**, not real-time (`remedy-pulse-prd.md`, Non-Goals: *"Real-time (sub-hour) alerting... v1 targets same-day/next-day freshness, not live streaming alerts"* — the same framing `scheduler.py`'s own docstring and the checklist's Phase 4 status note both use, so this runbook keeps the same number rather than inventing a stricter or looser one).

**Guideline:** if a tab's "last synced" indicator (once built — see §1, this is currently only readable via the two stopgap tools, not yet a UI element on every tab per checklist 4.7) is more than **one business day old**, that source is stale by the PRD's own standard, whether or not `lastStatus` shows an outright error.

**If you are a marketing team member (not an engineer) and a tab looks stale:**

1. Do not assume "no news" — that is exactly the failure mode the PRD names. A blank or unchanged tab can mean the source is down, not that nothing happened.
2. Check `GET /api/status` yourself if you have a way to (a session token and something to call it with), or ask whoever ran the morning check (§1) what it showed.
3. There is currently **no named on-call or owner** for this — same as `docs/decisions/assignment-roster.md`'s own honest note that "who owns keeping the roster current" is unresolved. Don't invent an owner here either: until the team assigns one, escalate to whichever of the two engineers (Angelo or Ceferino, per the PRD's own author line) is reachable, and treat "nobody owns this yet" as a real gap to raise, not something to quietly work around.
4. Once someone with access confirms the cause (§2–§3) and re-runs ingestion (§4), the tab should reflect a fresh sync on the next page load / next successful `GET` call.

---

## What this runbook does not cover

- It does not cover fixing bugs in the adapters themselves — only recognizing and responding to the failure states the system already reports.
- It does not assign a permanent on-call owner — that decision has not been made anywhere in this project yet (§5, point 3), and this document does not invent one.
- It will need a rewrite once checklist 4.7 (per-source failure surfaced in the dashboard UI) lands — at that point "who notices, and how" in §1 should be replaced with the real UI indicator, not the CLI/API stopgap described here.
