# Remedy Pulse

A real-time reputation monitoring dashboard for Remedy, replacing Media Meter/MediaWatch with a single view of what people are saying online — reviews, mentions, competitor benchmarks, topic sentiment, and earned media value (EMV).

## Status

Everything below has been run end to end against live data and the output
checked in the database. Counts and dates come from that run, recorded in
[`docs/live-run-evidence.md`](docs/live-run-evidence.md) with the commands
to reproduce it.

**Working on real data (verified 2026-09-14, no key or approval required):**

| Source | What it returns | Last verified run |
| --- | --- | --- |
| Google News RSS | Philippine press coverage of Remedy and tracked competitors | 19 articles |
| Bing News RSS | A partly different outlet set from the same queries | 13 articles |
| Reddit public search | Threads naming a tracked clinic, or asking for a derma recommendation | 7 threads |

Those 39 items were classified for sentiment and alert routing (Groq) and
tagged for topic, both on a schedule — **39 of 39 classified, 39 of 39
tagged, 0 pending** — from an empty database, in one unattended
`python -m app.scheduler` pass taking 6 minutes. Login and the API were
exercised against that same data.

The Reddit source reported `PARTIAL`, not `SUCCESS`: two of seven queries
were rate-limited. That and every other source still returning nothing are
listed with their real reasons in
[`docs/live-run-evidence.md`](docs/live-run-evidence.md).

**Built, but returning nothing until someone completes a setup step:**

| Blocked on | What it needs | Who can unblock it |
| --- | --- | --- |
| Google Places (competitor ratings) | Billing enabled on the Cloud project — the key is valid, the API returns `REQUEST_DENIED` without it. Also needs the real `place_id`s; `backend/config.py` still holds `REPLACE_ME` placeholders. | Us, today |
| Reddit API (richer per-post data) | A free self-serve script app at reddit.com/prefs/apps. The public-feed source above covers Reddit meanwhile. | Us, today |
| GNews | Nothing — the key works. Every configured brand search term returns zero results, because Remedy has almost no press coverage yet. Kept as a second opinion; the RSS sources carry the load. | N/A |
| Remedy's own Google reviews | A Business Profile API access request. No SLA. | Google |
| Instagram / Facebook | Meta App Review, separately, for each of three permission scopes. | Meta |

The EMV (earned media value) formula is deliberately not computed — it
needs an editorial-judgment sign-off from Marketing/Finance that hasn't
happened yet; every article's `grossEmv`/`netEmv` is honestly `null`
rather than invented. `tier` is likewise `null` for any outlet not in
`config.OUTLET_TIER_MAP`, so an unpriced outlet shows as unpriced rather
than silently mispriced.

409 backend tests pass and CI is green, but note what that does and does
not establish: it was true throughout a period when no source was
returning data and no one could log in. Treat
[`docs/live-run-evidence.md`](docs/live-run-evidence.md) as the status of
record, not the test count.

## Repo layout

```
index.html   The dashboard frontend — sample data if you're not logged in, real data once you are
backend/     The FastAPI app, Postgres persistence, auth, ingestion adapters, scheduler, and classifier (see backend/README.md)
docs/        API contract, decision records, runbooks, and the implementation checklist
```

`index.html` was renamed from `remedy-pulse-mockup.html` (checklist 0.26) once it started serving real, logged-in sessions too, not just the demo — a "mockup" filename in a real user's address bar was the wrong signal to send.

## Getting started

### Real data in about five minutes

No API keys needed for the three sources that carry the load. From a
clone:

```bash
cd backend
docker compose up -d                        # Postgres on :5434
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                        # the DATABASE_URL default already matches

python -m app.admin generate-secret         # paste into .env as SESSION_SECRET_KEY
alembic upgrade head

python -m app.admin create-user --email you@example.com --name "Your Name"
python -m app.scheduler                     # one ingestion + classification pass
python -m app.admin check                   # what actually landed

uvicorn app.api.main:app --port 8000        # then open ../index.html and log in
```

`python -m app.admin check` is the command to reach for whenever you want
to know whether this thing is working. It prints rows per source, how much
the classifier has processed, and the real status and error of each
source's last run — read from the database, not from any summary.

On Windows use `.venv\Scripts\pip` and `.venv\Scripts\python`. Run
everything through the project venv: invoking the scheduler with a system
Python that lacks the dependencies produces an `ERROR` ledger row reading
"The `groq` package is not installed", which looks like a broken
classifier rather than a wrong interpreter.

**Sending this repo to someone:** use `python scripts/package_release.py`,
never a folder zip. It builds the archive from git-tracked files only and
refuses to write one containing anything credential-shaped. See
[`docs/security/2026-09-14-env-in-distributed-archive.md`](docs/security/2026-09-14-env-in-distributed-archive.md).

**To view the demo:** open [`index.html`](index.html) in any browser. See [`docs/README-Remedy-Pulse-Demo.md`](docs/README-Remedy-Pulse-Demo.md) for a full walkthrough of what's real vs. sample, and things to try.

**To set up the backend** (ingestion connectors, Google Cloud steps, API access requirements, and known limitations for each source): see [`backend/README.md`](backend/README.md).

**To run the dashboard against a real local backend (not sample data):** see [`docs/local-dev-setup.md`](docs/local-dev-setup.md).

**To deploy a real, reachable instance on free-tier cloud services:** see [`docs/runbook-deploy-free-tier.md`](docs/runbook-deploy-free-tier.md) (also published as an interactive artifact, linked at the top of that file).

## What's next

In order:

1. **Enable billing on the Google Cloud project and fill in the real
   `place_id`s** in `backend/config.py` (they are still `REPLACE_ME`).
   That turns competitor ratings on. Both are ours to do, today.
2. **Register the free Reddit script app** for richer per-post data and
   to bring the deletion-propagation job into scope for Reddit rows.
3. **Tune the search and relevance terms** in `backend/config.py` with
   Marketing. They are a first pass written by an engineer, and the
   Mentions feed is only as good as they are.
4. **Get the EMV rate card signed off** so `grossEmv`/`netEmv` can stop
   being `null`.

Google Business Profile and Meta App Review remain genuinely external and
are calendar time, not engineering time. Everything above them is not.

See [`docs/implementation-checklist.md`](docs/implementation-checklist.md)
for the itemized status of every requirement.
