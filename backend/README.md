# Google Reviews Connector — Setup Guide

Pulls real review data for Remedy's own listings, plus public rating
benchmarks for competitors, and writes it out as JSON shaped to match
`remedy-pulse-mockup.html`'s data model directly.

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

- **`reviews_aggregate.json`** — one row per branch (rating, review count,
  response rate, pending replies). Matches the Reviews tab table exactly.
- **`reviews_raw.json`** — one row per individual review, with reviewer
  names already masked to first-name-plus-initial per the PH Data Privacy
  Act note in §11 of the spec. This is the input for the Mentions tab
  once the mockup is refactored to render from data instead of hardcoded
  markup — that refactor is separate work, not part of this script.
- **`competitor_ratings.json`** — rating + review count per competitor,
  plus a small capped sample of their reviews (Google doesn't give more
  than that for listings you don't own).

## Known limitations, honestly

- Individual reviews have no public deep-link URL from either API — this
  is why the mockup's "View source" links use a search fallback instead
  of a direct link, and that won't change once this connector is live.
- Competitor review data is a small sample, not the full history — fine
  for rating benchmarks, not for a full competitor mention feed.
- If the Business Profile API access request gets rejected or stalls,
  the fallback is a licensed reviews aggregator — worth flagging to
  Paul/Marketing as its own §16-style decision if that happens.
