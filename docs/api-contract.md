# Remedy Pulse API contract (Phase 7 draft)

This is the shared contract two things are built against in parallel:
`backend/app/api/` (the real implementation, 7.1/7.4) and
`remedy-pulse-mockup.html`'s data-driven refactor (7.2, and everything
that rides on it: 7.3/7.5/7.6/7.7). Both must match this document, not
each other's code, since they're built by separate agents that don't see
one another's output until a later reconciliation pass. If either side
needs to deviate, document the deviation clearly in its own report
rather than silently diverging.

**Base path:** `/api`. **Auth:** every endpoint except `POST /api/auth/login`
requires a session token, checked via `app.auth.verify_session_token()`.
Send it as `Authorization: Bearer <token>`. A missing/invalid/expired
token → `401 {"error": "unauthorized"}`. All responses are JSON.
Timestamps are ISO-8601 UTC strings (`"2026-06-29T09:03:00+00:00"`) —
the frontend converts to PHT/relative time for display (7.5), the API
never pre-formats a display string.

---

## Auth

### `POST /api/auth/login`
Body: `{"email": str, "password": str}`.
`200 {"token": str, "expires_at": iso8601, "user": {"id": int, "email": str, "display_name": str}}`
on success. `401 {"error": "invalid credentials"}` on failure — never
distinguish unknown-email from wrong-password in the response, per
`app.auth.authenticate()`'s own existing design.

---

## Overview — `GET /api/overview`

Query params: `period` (`7d` default | `30d` | `90d` | `custom` with
`from`/`to`), `source` (`all` default | `google` | `social` | `news`),
`entity` (`all` default | a specific owned listing/venue name).

```json
{
  "clarityIndex": {"score": 75, "deltaVsLastWeek": 6},
  "totalMentions": {"value": 342, "deltaPct": 18, "priorPeriodValue": 291},
  "netSentiment": {"value": 62, "deltaPts": 4},
  "avgGoogleRating": {"value": 5.0, "reviewCount": 128},
  "activeAlerts": {"total": 4, "crisis": 1, "digest": 3},
  "aiSummaryText": "string — see 'AI summary' note below",
  "lastSyncedAt": "iso8601 or null — the most recent successful run across every registered source, per repository.get_source_freshness()"
}
```

**Clarity Index** reuses the exact formula in
`docs/decisions/clarity-index-formula.md` /
`remedy-pulse-mockup.html`'s `computeClarityIndex()` — port it to Python
against real aggregate data (rating, sentiment mix, response rate,
mention-volume trend) rather than inventing a second formula.

**AI summary**: the mockup's `regenerateSummary()` cycles 3 canned
strings today (P1-1, not real). This endpoint may keep returning a
canned/templated string for now — **do not** wire it to a real LLM call
as part of this phase; that's explicitly out of scope (see
`docs/decisions/reddit-c4-no-resale-control.md`'s note that P1-1 going
live needs its own compliance gate first). Mark the field's source
clearly in a code comment either way.

---

## Mentions — `GET /api/mentions`

Query params: `keyword` (substring match on `text`), `platform` (a
`Mention.source` value or `all`), `sentiment` (`Positive`/`Neutral`/
`Negative`/`all`), `from`/`to` (date range on `published_at`), `limit`/
`cursor` (pagination — keyset on `id` or `published_at`, your call,
document which).

```json
{
  "items": [
    {
      "id": 123,
      "platform": "reddit",
      "author": "sk1n_a1b2c3d4",
      "text": "string",
      "url": "string or null",
      "publishedAt": "iso8601 or null",
      "sentiment": "Positive|Neutral|Negative|null",
      "topics": ["pricing"],
      "venue": "r/PhilippinesSkincare",
      "assignedTo": "string or null",
      "assignedAt": "iso8601 or null",
      "resolvedAt": "iso8601 or null",
      "alertCategory": "crisis|digest|null — see 6.3"
    }
  ],
  "nextCursor": "string or null"
}
```

### `POST /api/mentions/{id}/assign`
Body: `{"assignee": str}`. Calls `repository.assign_mention()`. `200`
with the updated mention (same shape as above), `404` if the id doesn't
exist.

### `POST /api/mentions/{id}/resolve`
No body. Calls `repository.resolve_mention()`. `200` with the updated
mention, `404` if the id doesn't exist.

---

## Reviews — `GET /api/reviews`

One row per owned branch, aggregated from `Mention` rows where
`kind="review"` and `source="google_reviews"`, grouped by `venue`.

```json
{
  "listings": [
    {
      "venue": "Remedy — BGC (One Uptown Residence)",
      "rating": 5.0,
      "reviewCount": 42,
      "pendingReplies": 1,
      "responseRatePct": 88,
      "status": "ok|no_reviews|access_denied|error — mirrors backend/app/models.py's IngestionRun/status conventions"
    }
  ]
}
```

`pendingReplies`/`responseRatePct` are computed from `has_reply` across
that venue's rows — do not reintroduce the mockup's old bug (0.14) where
one reply cleared a whole branch; this must reflect the real per-row
`has_reply` count.

### `POST /api/reviews/{mention_id}/reply`
Marks that one review's `has_reply = true` in the database (this API does
**not** post a real reply to Google — that would need the Business
Profile write scope, out of scope here) and logs nothing further beyond
the row update. `200` with the updated listing aggregate (same shape as
one entry in `GET /api/reviews`'s `listings` array), `404` if not found.

---

## Topics — `GET /api/topics`

```json
{
  "topics": [
    {
      "key": "facial-results",
      "label": "Facial Results",
      "mentionCountThisWeek": 38,
      "sentimentSplit": {"positivePct": 38, "neutralPct": 47, "negativePct": 15},
      "sampleQuote": "string",
      "tag": "watch|needs-attention|null"
    }
  ]
}
```

`key`s and taxonomy come from 6.5's topic-tagging work
(`docs/decisions/topic-tagging-approach.md`) — five fixed topics today,
matching the mockup's existing `topicMentions` object
(facial-results, staff-service, rejuran, pricing, booking).

### `GET /api/topics/{key}/mentions`
Same item shape as `GET /api/mentions`'s `items`, filtered to mentions
tagged with that topic key.

---

## Competitors — `GET /api/competitors`

```json
{
  "shareOfVoice": [{"name": "Remedy", "pct": 14, "isOwn": true}, {"name": "Belo Medical Group", "pct": 40, "isOwn": false}],
  "sourceBreakdown": [{"platform": "google_reviews", "pct": 34}],
  "competitorSentiment": [{"name": "Remedy", "positivePct": 72, "neutralPct": 22, "negativePct": 6, "isOwn": true}]
}
```

Ratings come from `google_places_competitor` Mention rows (4.2);
`competitorSentiment` needs sentiment classification per-competitor,
same classifier as 6.1 — reuse it, don't build a second one.

---

## EMV — `GET /api/emv`

Query params: `outlet` (a specific outlet or `all`), `from`/`to`.

```json
{
  "grossTotal": 2366000,
  "netTotal": 2809000,
  "filtered": false,
  "articles": [
    {
      "id": 456,
      "outlet": "Rappler",
      "headline": "Remedy BGC clinic review",
      "tier": "National News",
      "sentiment": "Positive",
      "grossEmv": null,
      "netEmv": null,
      "url": "string or null",
      "publishedAt": "iso8601"
    }
  ]
}
```

**`grossEmv`/`netEmv` are `null` on every article from this API.**
`backend/fetch_news_articles.py`'s own docstring already establishes why:
the formula needs editorial-judgment inputs (prominence, PubScore,
PR_Credibility) no connector or classifier can supply. Computing real
EMV numbers is explicitly **not** in scope for this phase — the API
exposes the raw ingested articles; a human-entered prominence/PubScore
step is future work, not invented here. The frontend should render an
honest "not yet priced" state for these rows rather than a fabricated
number (see 7.6, empty/error states) — this is a real, deliberate gap in
the contract, not an oversight; don't let either agent quietly fill it
with a guessed formula.

---

## Assignment roster — `GET /api/roster`

```json
{"assignees": [{"id": 1, "email": "...", "displayName": "Gian"}]}
```

Backed by `app.models.User` rows (5.5) — see
`docs/decisions/assignment-roster.md` for why this replaces the
mockup's hardcoded Gian/Paul/Boom/Mixi list.

---

## Exports — `POST /api/exports/{type}`

`type` ∈ `mentions_csv` | `reviews_csv` | `emv_csv` (matching the
mockup's three existing export buttons). Calls `repository.log_export()`
(3.4) and returns the CSV as the response body
(`Content-Type: text/csv`), built from the same filtered query the
corresponding `GET` endpoint would run (accept the same query params).

---

## Status — `GET /api/status`

The REAL fix for 4.7/0.2 (status_report.py was an explicit stopgap
pending this endpoint existing):

```json
{
  "sources": [
    {"source": "google_reviews", "lastAttemptAt": "iso8601 or null", "lastSuccessAt": "iso8601 or null", "lastStatus": "success|partial|access_denied|error|null", "lastError": "string or null"}
  ]
}
```

One entry per `app.jobs.JOBS` registry member, from
`repository.get_source_freshness()`.

---

## What's deliberately NOT in this contract

- Real-time push/websockets — the PRD scopes v1 at same-day/next-day
  freshness (4.6's own reasoning), so polling `GET /api/overview` etc.
  on a page-level refresh is enough.
- A real EMV monetary calculation (see the EMV section above).
- A real AI-generated summary (see the Overview section above).
- Writing back to Google/Reddit/Meta (the reply endpoint only updates
  this system's own copy).
