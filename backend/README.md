# Google Reviews Connector — Setup Guide

Pulls real review data for Remedy's own listings, plus public rating
benchmarks for competitors, and writes it out as JSON that is the *input*
for `remedy-pulse-mockup.html`'s Reviews tab table — not a byte-for-byte
match of it. See "What you get" below for exactly what is and isn't
covered.

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

## Retry behavior

Every outbound `GET` in `fetch_owned_reviews.py` and
`fetch_competitor_ratings.py` goes through `http_utils.get_with_retry`,
which retries transient failures (429, 500, 502, 503, 504) with
exponential backoff and jitter — honoring a `Retry-After` header on 429
when the server sends one. A run that exhausts its retries raises clearly
(`RetryExhaustedError`) instead of silently writing partial or stale-look
data; the per-listing/per-competitor `status` field records it as
`"error"` rather than pretending the fetch succeeded.

## Known limitations, honestly

- Individual reviews have no public deep-link URL from either API — this
  is why the mockup's "View source" links use a search fallback instead
  of a direct link, and that won't change once this connector is live.
- Competitor review data is a small sample, not the full history — fine
  for rating benchmarks, not for a full competitor mention feed.
- If the Business Profile API access request gets rejected or stalls,
  the fallback is a licensed reviews aggregator — worth flagging to
  Paul/Marketing as its own §16-style decision if that happens.
