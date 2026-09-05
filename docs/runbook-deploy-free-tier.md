<!-- artifact-url: https://claude.ai/code/artifact/486868fb-e034-412b-81b9-56abd81d9a05 -->

# Free-Tier Deploy Runbook

A real, reachable Remedy Pulse — API, database, scheduled ingestion, and
the dashboard — on services that cost nothing recurring. Every part is a
numbered step with something you can check afterwards. This is a demo/
pilot deployment, not a production one; §10 says exactly why, and it
isn't negotiable by configuration.

An interactive, checkbox-tracked version of this same document is
published at the artifact URL above — republish it from this file (see
the artifact's own footer) if it and this file ever disagree.

## 0 · The one real decision: deploy today, or wait for Phase 1?

Nothing here is blocked on a technical unknown. It's blocked on which of
Phase 1's vendor access requests (`docs/implementation-checklist.md`,
items 1.1–1.3) have actually landed:

| | Stage 1 — deploy today | Stage 2 — once approved |
|---|---|---|
| Needs | Nothing pending | Google Business Profile access (1.1) and/or Meta App Review (1.3) |
| Sources live | GNews (news/press), Reddit (self-serve script-app tier), Google Places (competitor ratings) | + Google owned reviews, + Instagram/Facebook |
| Sentiment classification | Works (Groq API key, self-serve, genuinely free at this project's volume — §3) | Same |
| What you get | A genuinely live dashboard with real news/Reddit/competitor data, real classification, real assign/resolve — just no owned-review or social data yet | The full six-tab picture |

**Do Stage 1 first regardless of where Phase 1 stands.** It proves the
whole pipeline — database, API, scheduler, frontend — end to end without
waiting on anyone's approval, and Stage 2 is additive (two more env vars
set on an already-working deployment), not a redo.

## 1 · The stack

| Piece | Service | Free tier | Notes |
|---|---|---|---|
| Frontend | **Cloudflare Pages** | Unlimited requests and bandwidth | Static file, zero build step — §6 |
| API | **Render** web service | 750 instance-hours/mo · 0.1 CPU / 512 MB | Sleeps after 15 min idle — §5, §8 |
| Database | **Neon** Postgres | 512 MB, scale-to-zero | Demo scale only — §9 |
| Scheduled ingestion | **GitHub Actions** | Free/unlimited (this repo is public) | Runs `python -m app.scheduler` hourly — §7 |
| News/press | **GNews** | Free tier, self-serve, no approval wait | Already the 1.5 decision's pick |
| Reddit | **Reddit "script" app** | Free, self-serve, no approval wait | Distinct from the elevated commercial tier 1.2 is still waiting on |
| Competitor ratings | **Google Places API** | Free monthly credit | Needs a Cloud **billing account attached** even though usage stays inside the free credit — §3 |
| Sentiment/topic classification | **Groq API** (`llama-3.3-70b-versatile`) | Free, generous at this project's volume | Self-serve, no approval wait — see §3 |
| Owned reviews | Google Business Profile API | — | Stage 2, gated on 1.1 |
| Instagram/Facebook | Meta Graph API | — | Stage 2, gated on 1.3 |

## 2 · Code status

**Nothing here is waiting on code.** Everything below already exists in
the repo:

- `GET /health` — a plain liveness probe, no auth, for Render's own
  health check and an uptime pinger (§8).
- `.github/workflows/scheduler.yml` — the scheduled ingestion workflow,
  already committed, just needs its secrets set (§7).
- CORS is already wide open (`allow_origins=["*"]`, `app/api/main.py`) —
  safe here specifically because `remedy-pulse-mockup.html`'s
  `apiFetch()` sends the session token as an `Authorization` header, not
  a cookie, so there's no credentialed-request/wildcard-origin risk.
  This means the frontend and API can be **fully cross-origin** (Cloudflare
  Pages calling a `*.onrender.com` API directly) with no same-origin proxy
  needed — unlike a cookie-session app, which would need one.
- `_redirects` (repo root) — the one Cloudflare Pages config file this
  deploy needs, so the root URL serves `remedy-pulse-mockup.html`
  (which isn't named `index.html`, since it has no build step to rename
  it during).
- `DATABASE_URL` is already declared with the `postgresql+psycopg://`
  scheme everywhere in this repo (`.env.example`, `docker-compose.yml`),
  matching Neon's own connection string shape exactly — no scheme
  rewrite needed, unlike some psycopg2-era examples you may see
  elsewhere.

**One line needs editing before the frontend actually works against a
real deployment**, covered in §6 — `remedy-pulse-mockup.html`'s
`API_BASE` constant is hardcoded to `http://localhost:8000/api` for
local dev (see `docs/local-dev-setup.md`), and Vite-style build-time env
vars don't exist here (no build step, by this project's own design
decision — 7.2's `docs/implementation-checklist.md` entry). Editing one
constant and redeploying the static file is the entire "build" step.

## 3 · Accounts and secrets

Where each of these ends up (Render's environment vs. GitHub Actions
repo secrets) is in the table at the end of this section.

1. **Create the Neon project.** [neon.tech](https://neon.tech) → New
   project. Copy the **pooled** connection string (has `-pooler` in the
   host) and change its scheme:
   ```
   # what Neon gives you
   postgres://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb

   # what you store — scheme changed, ?sslmode=require appended
   postgresql+psycopg://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
   This project already runs on psycopg 3 (`requirements.txt`), so this
   is the only edit needed — no `ModuleNotFoundError` gotcha to work
   around.

2. **Get a GNews API key.** [gnews.io](https://gnews.io) → free-tier
   signup. Self-serve, no approval wait (`docs/decisions/02-news-press-ingestion-path.md`).

3. **Register a Reddit "script" app.** [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)
   → create app → type "script". This is the free, self-serve tier
   `backend/.env.example` already documents — separate from the
   elevated commercial Data Access tier item 1.2 is still waiting on.
   You need the app's client ID/secret plus a Reddit account's own
   username/password (the account the script authenticates as).

4. **Get a Google Places API key.** [console.cloud.google.com](https://console.cloud.google.com)
   → new project → enable "Places API" → Credentials → API key.
   ⚠️ **This needs a billing account attached to the project**, even
   though normal demo-scale usage stays inside the free monthly credit
   — Google requires it for any API key regardless of whether you ever
   cross into paid usage. Set a budget alert (`backend/README.md`
   already recommends this).

5. **Get a Groq API key.** [console.groq.com](https://console.groq.com) →
   API Keys. Self-serve, no approval wait, and — unlike an equivalent
   Claude/GPT key would be — a genuine recurring free tier, not a
   one-time trial credit. Each classification call
   (`app/classification.py`) is short (a few hundred tokens, one review/
   mention/press excerpt at a time, not a large payload), which is
   comfortably inside Groq's free-tier rate limits at this project's
   stated volume (a few hundred items a week).

   > ⚠️ **The free tier still has real rate limits** (requests/tokens
   > per minute, and per day) — generous for this project's stated
   > volume, not unlimited. If `app/scheduler.py`'s hourly run ever logs
   > a `RateLimitError` from `classify_unclassified_batch()`, that's the
   > signal to either space the schedule out further or move to a paid
   > Groq tier — not a sign anything is broken.

6. **Generate `SESSION_SECRET_KEY`.**
   ```
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   `app/auth.py` falls back to a random per-process key if this is
   unset — fine for a quick local check, **not fine for Render**: every
   issued session token becomes invalid the moment the process
   restarts (and Render's free tier restarts constantly — every sleep/
   wake cycle). Set this explicitly.

### Where each value ends up

| Value | Render env (§5) | GitHub Actions secret (§7) |
|---|---|---|
| `DATABASE_URL` (Neon) | yes | yes |
| `GNEWS_API_KEY` | — (API doesn't ingest directly) | yes |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USERNAME` / `_PASSWORD` | — | yes |
| `GOOGLE_PLACES_API_KEY` | — | yes |
| `GROQ_API_KEY` | — | yes |
| `SESSION_SECRET_KEY` | **yes** | — (the API signs tokens, not the scheduler) |

The API server itself only ever reads `DATABASE_URL` and
`SESSION_SECRET_KEY` — every ingestion credential is consumed by the
scheduler (§7), which runs as a separate GitHub Actions job, not inside
the Render process. Getting a value into the wrong one of these two
places is the single most common mistake in this runbook.

## 4 · Database: migrate Neon

From `backend/`, with the venv from `docs/local-dev-setup.md` active:

```
DATABASE_URL="<the Neon URL from §3 step 1>" alembic upgrade head
```

`migrations/env.py` reads `DATABASE_URL` from the environment the exact
same way the app does (`app.config.get_settings()`), so this is the
identical command from `docs/local-dev-setup.md`, just pointed at Neon
instead of the local Docker container.

**You should see** Alembic running each revision in order, ending at
`19e01c8137d1 (head)`. Run this once now, and again after any future
schema change — never on Render's own boot (see §5's note on why).

## 5 · Deploy the API on Render

1. [render.com](https://render.com) → New → **Web Service** → connect
   this repo.
2. **Root Directory:** `backend`. **Runtime:** Python 3. **Build
   Command:** `pip install -r requirements.txt`. **Start Command:**
   ```
   uvicorn app.api.main:app --host 0.0.0.0 --port $PORT
   ```
3. **Environment variables:**
   ```
   DATABASE_URL=<the Neon URL from §3 step 1, ?sslmode=require included>
   SESSION_SECRET_KEY=<from §3 step 6>
   ```
   Nothing else is required for the API process itself to boot and
   serve every read/write endpoint — the ingestion credentials belong
   to the scheduler (§7), not here.
4. Deploy. **You should see** a build log running `pip install`, then
   `Uvicorn running on http://0.0.0.0:$PORT`.

> **Do not run migrations as part of the boot command.** Keep §4's
> `alembic upgrade head` a separate, manual step you run from your own
> machine. Render can restart this process at any time (a sleep/wake
> cycle, a redeploy) — if migrations ran on every boot, two boots
> racing each other would fight over the same migration lock for no
> reason a one-shot manual run doesn't already handle correctly.

## 6 · Deploy the frontend on Cloudflare Pages

1. [dash.cloudflare.com](https://dash.cloudflare.com) → Workers &
   Pages → Create → Pages → **Connect to Git** → this repo.
2. **Build command:** leave blank. **Build output directory:** `/`
   (the repo root — there is no build step and nothing to output).
3. **Edit one line before this first deploy** — `remedy-pulse-mockup.html`,
   near the top of its `<script>` block:
   ```js
   const API_BASE = 'http://localhost:8000/api';
   ```
   Change it to the Render service's real URL from §5:
   ```js
   const API_BASE = 'https://<your-render-service>.onrender.com/api';
   ```
   This is the entire "build" step this deploy has — there is no
   environment-variable injection to configure instead, by design (see
   §2's note on why there's no build step to hook one into).
4. Deploy. **You should see** the build log publish the repo root
   as-is (no compile step to watch), and `_redirects` take effect so
   the site's root URL serves `remedy-pulse-mockup.html` directly.

## 7 · Wire the ingestion scheduler

`.github/workflows/scheduler.yml` already runs `python -m app.scheduler`
on an hourly cron (`get_source_freshness()`'s own per-source cadence
check makes over-calling this harmless — see `app/scheduler.py`'s
module docstring — so hourly is deliberately more frequent than any
source's actual ~12h default cadence).

1. This repo → Settings → Secrets and variables → Actions.
2. Add each secret from §3's "Where each value ends up" table (the
   GitHub Actions column) — at minimum `DATABASE_URL`; add the rest as
   each account from §3 gets created.
3. Actions tab → "Ingestion scheduler (free-tier deploy)" → **Run
   workflow** (the manual trigger — `workflow_dispatch` — so you can
   test this without waiting up to an hour for the cron).
4. **You should see** the run's log end with a line like
   `Ran: news_gnews, reddit` (or `Ran: (nothing due)` if every
   configured source already ran within its cadence window — that's
   correct behavior, not a failure).

A source with no credentials set for it fails cleanly with a specific,
readable error for that one source (see `backend/README.md`'s
ingestion adapters section) — it does not crash the whole run. Add
credentials incrementally as each account from §3 gets created; nothing
here requires configuring every source at once.

## 8 · Verify, in this order

Each step isolates a different failure — going in order means the
first thing that breaks tells you where the problem is.

1. **The API is alive** — `https://<render-service>.onrender.com/health`.
   You should see `{"status":"ok"}`.
2. **The Cloudflare site loads** — open the Pages URL. You should see
   the dashboard in demo mode (the "Demo" badge, sample data) — this is
   correct even before you've logged in.
3. **Log in works** — create a real user first (see
   `docs/local-dev-setup.md`'s step 4, pointed at Neon instead of the
   local container), then log in on the deployed site with those
   credentials. You should see the Demo badge disappear and the "Last
   synced" pill in the header.
4. **A real sync actually ran** — after §7's manual trigger completes,
   click the "Last synced" pill (or reload). You should see a real
   timestamp, not `—:— PHT`.
5. **A per-source failure is visible if one exists** (4.7) — if any
   source has no credentials configured yet, you should see a coral
   banner near the top of every tab naming it and its error, not a
   silent gap in the data.
6. **An uptime pinger is running — set this up now, not after noticing
   the site feels slow.** [UptimeRobot](https://uptimerobot.com) or
   [cron-job.org](https://cron-job.org) (either free) → ping
   `https://<render-service>.onrender.com/health` every 10 minutes.
   Render sleeps a free web service after 15 minutes idle, and the
   first request after that takes tens of seconds to wake it — the
   pinger keeps that from ever being the visitor's actual experience.

## 9 · Stage 2 — once Phase 1's approvals land

Additive, not a redo of anything above:

- **Google Business Profile (owned reviews), once 1.1 is approved:**
  `GOOGLE_CLIENT_SECRETS_FILE` and `GOOGLE_TOKEN_FILE` are currently
  *file paths*, not env vars (`app/config.py`) — Render's filesystem is
  ephemeral, so a file written by hand doesn't survive a redeploy. The
  workaround needs no code change: store each file's full contents as a
  GitHub Actions secret (e.g. `GOOGLE_TOKEN_JSON`), and have the
  scheduler workflow write it to the expected path as its first step
  (`echo "$GOOGLE_TOKEN_JSON" > token.json`) before running
  `python -m app.scheduler` — the same "write a secret to disk at boot"
  pattern used for file-shaped credentials on any platform without
  persistent storage.
- **Instagram/Facebook, once 1.3 is approved:** `META_ACCESS_TOKEN`,
  `META_IG_BUSINESS_ACCOUNT_ID`, `META_PAGE_ID` as GitHub Actions
  secrets — no file-path problem this time, these are already plain
  env vars.

## 10 · What this deployment cannot do

State these plainly to anyone who asks what they're looking at.

- **It is not the production deployment**, and shouldn't be treated as
  one for a real launch decision. No vendor here signs anything beyond
  its own standard terms of service at this tier, and `docs/decisions/08-secrets-at-rest.md`'s
  open question (where secrets actually live) is still open — env vars
  on a free host answer "not committed to git," not "production-grade
  secret management."
- **Free tier is demo scale, and here is the actual number.** Neon's
  512 MB is generous for this project's stated volume (same-day/
  next-day ingestion, per the PRD) — but it is not unlimited, and a
  real multi-branch, multi-month dataset will eventually need a paid
  tier or a different host. No code change is needed to move off it
  later — `DATABASE_URL` is the only thing that changes.
- **First request after idle is slow.** Render sleeps at 15 minutes;
  the §8 pinger fixes this for the API specifically. Neon also scales
  its compute to zero after inactivity and takes a moment to resume —
  the pinger doesn't reach Neon directly, so an API request that's been
  quiet for a while may still have a brief extra delay on its first
  database query even with the pinger running.
- **Groq's free tier has real rate limits** (§3) — generous at this
  project's stated volume, not unlimited. This is no longer the one
  paid line item in the stack (it was, before switching from Claude —
  see `docs/decisions/09-sentiment-classifier-choice.md`'s "Update"
  section), but it is also not an infinite resource to build against.
- **Nothing is alerting.** 4.7 makes a source failure visible *in the
  UI*; nothing pages anyone if the scheduler workflow itself stops
  running (a GitHub Actions outage, a secret rotated without updating
  it here). Check the Actions tab's run history occasionally rather
  than assuming silence means success.
- **This is still gated on Phase 1's vendor approvals for two of the
  six tabs' full data.** §0 and §9 already say which ones and what
  unlocks them — that gate doesn't move by deploying harder.

---

Source of truth for the companion interactive artifact — this file
mirrors it and vice versa; if they ever disagree, this file (committed,
reviewed) wins. Platform figures (free-tier limits, pricing) were as
published by each vendor on 2026-09-05 — free tiers change without
notice, so re-verify anything load-bearing before depending on it for a
real decision. The app-side facts (env var names, file locations, what
code already exists) are read directly from this codebase, not
guessed.
