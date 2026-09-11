# Remedy Pulse

A real-time reputation monitoring dashboard for Remedy, replacing Media Meter/MediaWatch with a single view of what people are saying online — reviews, mentions, competitor benchmarks, topic sentiment, and earned media value (EMV).

## Status

The backend, API, and dashboard frontend are fully built and tested (345 backend tests passing, CI green) — well past demo/mockup stage. `index.html` shows sample data if you're not logged in, and real data pulled from the sources below once you are.

**Live today, no approval needed:**

- The full FastAPI backend (`backend/app/api/`) — real Postgres persistence, real authentication, every endpoint in [`docs/api-contract.md`](docs/api-contract.md).
- Real ingestion: news/press coverage via GNews, and competitor rating benchmarks via Google Places (both self-serve — a Places API key with billing enabled is the only setup step).
- Real sentiment classification and crisis/digest alert routing (Groq), running on a schedule.
- A free-tier deploy runbook ([`docs/runbook-deploy-free-tier.md`](docs/runbook-deploy-free-tier.md)) — Cloudflare Pages + Render + Neon + GitHub Actions.

**Built and tested, but not yet live against real data** — each blocked on an external party's approval, not on code (see [`backend/README.md`](backend/README.md) for exactly where each stands):

- **Remedy's own Google reviews** — Google gates the reviews endpoint behind a Business Profile API access request with no SLA.
- **Reddit mentions, and the 48-hour deletion-propagation job that access commits to** — needs real Reddit credentials, plus a separate pending approval for the commercial Data Access tier this project committed to in writing.
- **Instagram/Facebook mentions and comments** — needs Meta App Review approval, separately, for each of three permission scopes.

The EMV (earned media value) formula is deliberately not computed — it needs an editorial-judgment sign-off from Marketing/Finance that hasn't happened yet; every article's `grossEmv`/`netEmv` is honestly `null` rather than invented. See [`docs/implementation-checklist.md`](docs/implementation-checklist.md) for the full, itemized status of every requirement.

## Repo layout

```
index.html   The dashboard frontend — sample data if you're not logged in, real data once you are
backend/     The FastAPI app, Postgres persistence, auth, ingestion adapters, scheduler, and classifier (see backend/README.md)
docs/        API contract, decision records, runbooks, and the implementation checklist
```

`index.html` was renamed from `remedy-pulse-mockup.html` (checklist 0.26) once it started serving real, logged-in sessions too, not just the demo — a "mockup" filename in a real user's address bar was the wrong signal to send.

## Getting started

**To view the demo:** open [`index.html`](index.html) in any browser. See [`docs/README-Remedy-Pulse-Demo.md`](docs/README-Remedy-Pulse-Demo.md) for a full walkthrough of what's real vs. sample, and things to try.

**To set up the backend** (ingestion connectors, Google Cloud steps, API access requirements, and known limitations for each source): see [`backend/README.md`](backend/README.md).

**To run the dashboard against a real local backend (not sample data):** see [`docs/local-dev-setup.md`](docs/local-dev-setup.md).

**To deploy a real, reachable instance on free-tier cloud services:** see [`docs/runbook-deploy-free-tier.md`](docs/runbook-deploy-free-tier.md) (also published as an interactive artifact, linked at the top of that file).

## What's next

Going live against real Google reviews, Reddit, and Meta data is gated on three separate external approvals (see "Status" above) — chasing those is calendar time, not engineering work. Everything else that's actually buildable without an approval or a payment has been built; see [`docs/implementation-checklist.md`](docs/implementation-checklist.md) for what's left and why each remaining item is blocked.
